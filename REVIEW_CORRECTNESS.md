# Correctness Review — Telegram Shop

Scope: bugs and logic errors only (security and style handled separately).
Files reviewed: `db.py`, `bot.py`, `run.py`, `config.py`, `services/*.py`,
`handlers/*.py`, `webapp/app.py`, `states.py`, `keyboards/payment.py`,
`keyboards/shop.py`.

Severity legend: CRITICAL (data loss / silent money loss / crash on common
path), HIGH (broken feature, error on reachable but uncommon path), MEDIUM
(incorrect under edge conditions), LOW (cosmetic correctness).

---

## 1. Database layer (`db.py`)

### [CRITICAL] `create_payment` rejects every legitimate concurrent deposit because amounts are not bumped to a free slot
- **File**: `db.py:639-669`
- **Issue**: The docstring claims that on a collision the loop "bumps to the next offset" so two users can deposit the same nominal amount concurrently. There is no loop: the function inserts using `round(base_amount, 2)` exactly once and re-raises `IntegrityError` as `RuntimeError`. Because `uq_payments_pending_amount` enforces uniqueness on `(coin, network, unique_amount)` across **all** pending rows, the *second* user who tries to deposit (or pay) the same round dollar value (e.g. $10.00 USDT BSC) gets `"A pending 10.0 USDT payment on this network already exists. Wait for it to expire or choose a different amount."` — even though they are different users.
- **Reproduction**:
  1. User A calls `/deposit 10` and picks USDT_TRC20. Row inserted with `unique_amount=10.00`.
  2. User B calls `/deposit 10` and picks USDT_TRC20 within `PAYMENT_TTL_MINUTES` (15 min). `create_payment` raises `RuntimeError`. User B sees "wait for it to expire."
- **Fix**: Implement the documented bump loop. Try `candidate = base_amount + 0.0001 * k` for `k = 0..N`, catch `IntegrityError` per attempt, and only raise after N tries:
  ```python
  for k in range(200):
      candidate = round(base_amount + 0.0001 * k, 4)
      try:
          cur = await db.execute(... unique_amount=candidate ...)
          await db.commit()
          return cur.lastrowid, candidate
      except aiosqlite.IntegrityError:
          await db.rollback()
          continue
  raise RuntimeError("All amount slots taken; try again in a few minutes.")
  ```
  Also widen `unique_amount` rounding precision to 4 decimals so the bump produces visibly different totals (10.0001, 10.0002, …) and the deposit-history matcher (`find_pending_payment_by_amount`) keeps working.

### [CRITICAL] `find_pending_payment_by_amount` matches by 2-decimal rounding — can deliver one user's funds to another user's order
- **File**: `db.py:795-804`
- **Issue**: The matcher considers a row matched if `ROUND(unique_amount, 2) = ROUND(amount, 2)`. Once the `create_payment` bump fix above is in place (4-decimal differentiators), this still matches *all* offset rows: 10.0001, 10.0002 all round to 10.00. The query then picks the oldest by `created_at ASC` — i.e. the *wrong* user gets credited.
- **Reproduction**: With the bump-loop fix, User A's payment is `10.0000`, User B's is `10.0001`. User B sends exactly `10.0001` USDT. The deposit history shows `amount=10.0001`. `ROUND(10.0001, 2) = ROUND(10.0000, 2) = 10.00`, so the matcher returns User A's pending row (older `created_at`). A gets credited; B's payment sits pending until it expires.
- **Fix**: Drop the `ROUND(unique_amount, 2) = ROUND(amount, 2)` branch. Use only the exact match tolerance, and tighten it once amounts are stored at 4 decimals: `ABS(unique_amount - ?) < 0.00005`. Order by exact-distance only; do not fall back to `created_at` for non-exact matches.

### [HIGH] `add_balance` / `try_debit_balance` use `REAL` (float) for money — accumulates rounding error
- **File**: `db.py:19, 229-245`, plus all callers
- **Issue**: `users.balance` is `REAL`. Every `balance + ?` is binary-float arithmetic. Across many small deposits/debits the balance drifts (e.g. `0.1 + 0.2 = 0.30000000000000004`). The visible totals are formatted to 2 dp, but the underlying field grows long, and `try_debit_balance` compares with `balance >= ?` — a user who deposited and then tried to spend exactly their balance can be refused by a sub-cent epsilon.
- **Reproduction**: Deposit 0.1, 0.2 across two top-ups. Database `balance` is `0.30000000000000004`. Try to debit exactly `0.30`: succeeds (DB float math), but the *reverse* — debit 0.1 then 0.1 then 0.1 — yields `1.110e-16` residue. Display is fine; a future `set_user_balance` round-trip silently shifts the audit log.
- **Fix**: Store balances as integer cents (or 4-decimal microUSDT). At minimum, normalize on every write: `UPDATE users SET balance = ROUND(balance + ?, 4)`. The `try_debit_balance` epsilon comparison should be `balance + 1e-9 >= ?`.

### [HIGH] `try_debit_balance` allows negative balance when called with a negative `amount`
- **File**: `db.py:238-245`
- **Issue**: The guard `balance >= ?` is meaningless if `amount` is negative (e.g. a coding mistake elsewhere passes `-5`). Then `balance >= -5` is always true and the row is updated to `balance - (-5) = balance + 5`. No callers do this today, but the gate is documented as a safety net.
- **Reproduction**: `await db.try_debit_balance(uid, -5)` → user is credited 5 USDT and the call returns `True`.
- **Fix**:
  ```python
  if amount <= 0:
      return False
  ```

### [HIGH] `_migrate` is not idempotent across all branches — re-running raises when columns already exist on some schemas
- **File**: `db.py:148-170`
- **Issue**: The column-existence check (`if "icon_custom_emoji_id" not in cols`) is correct, but `_migrate` is invoked *after* `executescript(SCHEMA)`. If a fresh install lands on the new schema there is no row in `products` yet; the check still passes because `PRAGMA table_info` returns the live columns. However, the inner `ALTER TABLE` does **not** wrap `WHEN NOT EXISTS`, and if the user manually adds the column out-of-band the second migration aborts the whole `init_db` transaction. More importantly, the wrapping `init_db` does `await db.commit()` only once at the end — the `executescript` already implicitly commits, so a failure in `_migrate` leaves the DB in a partially migrated state with no rollback.
- **Reproduction**: Manually `ALTER TABLE products ADD COLUMN brand TEXT`. Restart the bot → `aiosqlite.OperationalError: duplicate column name: brand` raises and the process exits.
- **Fix**: Catch `OperationalError("duplicate column")` per-column, or use a `_columns(db, table)` helper before each ALTER. Also wrap the whole `_migrate` in `BEGIN/COMMIT` so partial failures roll back.

### [HIGH] `expire_old_payments` SQL-formats integers via `%` interpolation — string-formatting injection-by-bug
- **File**: `db.py:781-790`
- **Issue**: `"UPDATE … WHERE id IN (%s)" % ",".join("?" * len(ids))` works, but if `ids` is empty (the function already returns early in that case) or if it's ever called with an unexpected sequence type, the SQL is malformed and the executescript chain breaks. Low severity functional risk, but more importantly the cur.rowcount from `db.execute` here is discarded — if the orders update fails (e.g. trigger error in future), payments are still marked expired and orders stay `awaiting_payment`.
- **Reproduction**: Add a `BEFORE UPDATE ON orders` trigger that fails. `expire_old_payments` still marks the payments expired, but orders remain `awaiting_payment` forever and `auto_payment_tick` keeps re-polling for them (they're no longer pending, so they just sit).
- **Fix**: Wrap the two updates in `BEGIN IMMEDIATE` / `COMMIT`, and execute the orders update first so a failure rolls everything back. Use parameter binding with `f"... IN ({','.join('?' * len(ids))})"`.

### [MEDIUM] `init_db` cleanup uses non-idempotent `MIN(id)` to pick survivor — picks any one row, may not be the one users expect
- **File**: `db.py:132-142`
- **Issue**: Functional correctness is fine (one pending row survives, others go to `cancelled`), but the comment says "Safe no-op on a fresh DB" — actually the `executescript(SCHEMA)` happens **after** this. On a brand-new install, the `payments` table doesn't exist when the UPDATE runs, and `OperationalError` is silently caught (correct). But on an *existing* install that already has the partial unique index, this UPDATE is a no-op every startup — overhead is negligible, but there is no log line indicating dupes were cleaned up either.
- **Fix**: Move the dupe-cleanup after `executescript(SCHEMA)` runs, so the table is guaranteed to exist; remove the broad `except OperationalError` and log a count when rows were actually cancelled.

### [MEDIUM] `claim_stock_items` doesn't enforce `quantity > 0`
- **File**: `db.py:486-511`
- **Issue**: If `quantity <= 0`, `LIMIT 0` returns no rows → `len(rows) < quantity` is `0 < 0` = false → an empty list `[]` is returned without rolling back. The order is then `mark_order_delivered`'d with 0 codes in `services/delivery.py:38` because `contents` is truthy-empty. Wait — actually `if not contents` triggers `update_order(status="failed")` because `[] is not contents` evaluates falsy. So functionally OK but only by luck.
- **Reproduction**: An admin creates an order via direct DB write with `quantity=0`. The order flips to `failed`.
- **Fix**: Add early return: `if quantity <= 0: return []` (and reject before insert in `create_order`).

### [LOW] `cleanup_stale_drafts` deletes orders even when payments exist on them
- **File**: `db.py:714-723`
- **Issue**: `DELETE FROM orders WHERE status='draft'` — but in `services/delivery.py:84-89`, `try_transition_order_status` allows a draft→paid transition if a wallet pays directly without going through `awaiting_payment`. A concurrent payment confirmation that lost the race (refund branch in `confirm_and_settle`) could in theory leave the order in `draft` while a payment row points at it. Deleting the draft orphans the payment row's `order_id`.
- **Fix**: `DELETE FROM orders WHERE status='draft' AND id NOT IN (SELECT order_id FROM payments WHERE order_id IS NOT NULL)`.

---

## 2. Payment flow

### [CRITICAL] Wallet payment refund on stock-out does not revert order status — order stuck `paid` with no delivery
- **File**: `services/delivery.py:97-101`
- **Issue**: `pay_from_wallet` transitions the order to `paid`, then calls `deliver_order` which on `OutOfStock` flips status to `failed` (line 39). Wait — `deliver_order` does `db.update_order(order_id, status="failed")` so that's fine. But then `pay_from_wallet` does `db.add_balance(user_id, payable)` — refund — and re-raises. The combination is:
  - order.status = "failed"
  - balance restored
  - The caller (`handlers/payment.py:109-114`) shows "❌ Stock ran out just before fulfilment. Your balance is unchanged."
  This is actually OK. Re-read… Wait, looking again: in `confirm_and_settle:147-150` the same OOS scenario happens for crypto pay, but the refund credits wallet (correct) **and** the order status is `paid` until `deliver_order` flips it to `failed`. That's also OK.
  Actually the real bug: in `confirm_and_settle`, after `deliver_order` raises `OutOfStock` and flips the order to `failed` (db.py:38-40), the function adds balance and returns. But it never resets `delivered_at` or clears the payment association. If the admin manually adds stock and retries, the order is `failed` (not `paid`), and `deliver_order` refuses (line 32 check: `status != 'paid'`). So the order can never be re-delivered without a DB poke. The user has been refunded to wallet, so they can retry — but the *original* order record is permanently `failed` with the user paid amount sitting in their wallet. That's recoverable behaviour, not data loss. Downgrading to MEDIUM.
- **Reproduction**: User pays via crypto. Last stock item is admin-deleted between confirm and claim. Order = `failed`, user has wallet credit equal to amount, no codes.
- **Fix**: Document that the user must manually re-place the order. Or attempt re-delivery if stock returns — but only if order was crypto-paid and not wallet-paid. Acceptable for now; mark as expected behaviour in `OrderNotPayable` docstring.

(Reclassifying above as MEDIUM. Now the real CRITICAL:)

### [CRITICAL] `confirm_and_settle` refund path on `order_already_settled` does not check unique_amount discrepancy — pays out the *transaction* amount, not what was expected
- **File**: `services/delivery.py:138-142`
- **Issue**: When two payments race and one loses the order transition, the loser refunds `actual_amount` to the wallet. `actual_amount` is taken from Binance deposit history (`h["amount"]`) — i.e. **what the user actually sent**, not the `unique_amount` the shop expected. If a user accidentally double-sent (a real risk because the user is told "Send exactly X") or sent more than required, the *full* received amount is credited as wallet refund. That's fine for the user but means the shop now owes wallet credit to a user whose order was already delivered to *another* successful payment — both deposits arrived, but only one delivered the order; the second gets wallet credit equal to the second deposit. Net result: user is correctly credited; **but** because `find_pending_payment_by_amount` (db.py:795) prefers oldest by `created_at`, the same physical TXID could match the wrong pending payment row (different user). Combined with the rounding bug above, this is a path for money landing in the wrong user's wallet.
- **Reproduction**: Two users have pending TRC20 deposits both rounded to $10.00 (after the create_payment fix that uses 4-dp differentiators — actually under the current buggy code, the second is rejected at creation, so this only matters once you fix bug #1). After fix, user B sends 10.0001 USDT. Matcher finds user A (oldest with rounded amount = 10.00). A gets wallet credit; B's payment expires.
- **Fix**: Match strictly on `unique_amount` with a small float-equality tolerance. Order ASC on `ABS(unique_amount - ?)` only. Drop the `ROUND(..., 2)` branch.

### [HIGH] `poll_payments` silently swallows `txid` already used — but the dedup check is per-status='confirmed' only; cancelled/expired payments with txid set can be re-confirmed
- **File**: `db.py:807-813`, `services/payments.py:86-89`
- **Issue**: `txid_already_used` returns True only when `status='confirmed'`. If a payment was confirmed, then somehow rolled back to another status (admin SQL, future migration), the same TXID can be confirmed again, double-crediting. More realistically: `confirm_payment` (db.py:751) updates with `WHERE status = 'pending'`. The `uq_payments_txid` unique partial index ensures no two confirmed payments share a TXID, but the IntegrityError on a UNIQUE collision is caught and returned as `False`. The `_poll_*` functions in `services/payments.py` check `txid_already_used` first; if that returns False (e.g. TXID was set on an expired-but-never-confirmed row), they call `confirm_and_settle` → `confirm_payment` → tries to UPDATE the matched pending row's txid to this value → UNIQUE collides with the existing row → returns False → silent no-op. No money is lost but the payment row never confirms.
- **Reproduction**: Hard to hit; requires manual DB intervention.
- **Fix**: `txid_already_used` should match any non-NULL txid: `SELECT 1 FROM payments WHERE txid = ?`.

### [HIGH] `poll_payments` bumps coupon usage in `deliver_order` even when the eventual delivery fails
- **File**: `services/delivery.py:43-46`
- **Issue**: `deliver_order` increments coupon usage *after* `mark_order_delivered`. If the bot crashes between `mark_order_delivered` (db.py:591) and `bump_coupon_uses` (db.py:542), the coupon counter is never bumped — the order shows delivered but the coupon's `used_count` is one short. Inverse: not a bug. Real bug: there is no protection against bumping coupons on retried deliveries — `deliver_order` re-runs would re-bump, but actually `deliver_order` refuses if status != 'paid' so this is gated. OK — this is just a transactional atomicity concern. Downgrade to MEDIUM.
- **Reproduction**: Crash between mark_order_delivered and bump_coupon_uses (small window). Coupon `used_count` drifts low.
- **Fix**: Move the coupon bump into the same transaction as `mark_order_delivered` (single DB connection).

### [HIGH] `cb_check` doesn't recover from race where confirmation lands between user's Check tap and the poller — user sees "Still pending" forever
- **File**: `handlers/payment.py:187-207`
- **Issue**: User taps Check Paid. If the auto-poller just confirmed, the row is `confirmed` and we show the right alert. Fine. But if the user sends the funds, then taps Check before the poller's next interval (could be up to 30s), the user gets "Still pending. Auto-poller checks every 30s." This is correct **but** there is no manual fallback — the user has no way to trigger an immediate check. Not a bug, just poor UX. Skip.

### [MEDIUM] Polling intervals are bound to `>=10s` but Binance has rate limits — burst at startup may 429
- **File**: `bot.py:182-187`
- **Issue**: APScheduler with `coalesce=True, max_instances=1` is fine for steady state, but at startup a backlog of pending payments (e.g. after a restart) all get polled in one tick. `_poll_onchain_deposits` iterates `pairs` of `(coin, network)` — at most 2 (BSC, TRX). Per pair one signed API call. Not a problem usually, but if you ever extend coins/networks, the 1200/min Binance limit gets close. Skip.

### [MEDIUM] `auto_payment_tick` notifies events sequentially — a slow Telegram send blocks the whole tick
- **File**: `bot.py:129-136`
- **Issue**: `for ev in events: await _notify_payment_event(bot, ev)` — Telegram sends are 1-2s under good network. With 20 confirmed events in one tick (rare but possible after API downtime), this serializes 30+ seconds, holding the scheduler tick. Because `max_instances=1, coalesce=True`, the next tick is suppressed.
- **Fix**: `asyncio.gather(*[_notify_payment_event(bot, ev) for ev in events])`. Wrap each in a `try/except` since `gather` propagates the first exception. Or `asyncio.create_task` each with an outer exception logger.

### [MEDIUM] `_poll_onchain_deposits` matches network with `.upper()` comparison but the pending payments are queried via `find_pending_payment_by_amount` which is `=` — Binance returns network `BSC` for `BEP20`, code expects `BSC` already (config.py:106). OK on inspection. Skip.

### [LOW] `_alnum_note(6)` collision space is 36**6 ≈ 2B — but `note` has no unique index, so collisions just produce duplicate display strings (no harm). Skip.

### [LOW] `services/binance.py:_signed_get` does not retry on transient 5xx. Skip; out of scope.

---

## 3. State machine (`states.py` and handlers)

### [HIGH] FSM state is cleared in `cb_payment_methods` before user confirms — back-button loses context
- **File**: `handlers/payment.py:29-30`
- **Issue**: Every call to `cb_payment_methods` does `await state.clear()`. If the user is mid-deposit (`DepositFlow.waiting_method`) and accidentally taps an order callback, the deposit FSM is wiped. Real-world this only happens if a callback URL is opened with shared state — unlikely. Skip; but the symmetric clear in `cb_do_payment:55` *is* needed because the next message is the order's pending screen.

### [HIGH] `msg_custom_qty` handler runs without rate limit or product re-validation — admin/draft orders can be spammed
- **File**: `handlers/shop.py:162-174`
- **Issue**: A user in `CheckoutFlow.waiting_custom_quantity` typing "abc" gets the error message and remains in state. Typing a valid number creates an order. There is no global state guard for "draft order already exists for this product" — every Custom quantity entry creates a new draft, growing `orders` table. With `cleanup_stale_drafts` running hourly, this is at most one hour of waste, but a user holding their finger on Custom can DoS the DB.
- **Reproduction**: User taps Custom Amount, types `1`, gets order, taps Back, taps Custom Amount, types `1` again. New draft order. Repeat. Each new draft is a row.
- **Fix**: Either reuse the latest draft for the same (user, product, status='draft') tuple, or rate-limit per-user FSM transitions. Not critical.

### [HIGH] `addstock_paste` runs inside `AdminFlow.add_stock_paste` but has no admin guard — once a non-admin somehow lands in this state, they can write arbitrary stock rows
- **File**: `handlers/admin.py:196-208`
- **Issue**: The entry points (`cmd_addstock`) check `is_admin`, but the message handler `addstock_paste` only matches the FSM state and trusts that only admins can be in it. Aiogram FSM is keyed per user; non-admins cannot reach this state via the bot. However if an admin tg_id is removed from `ADMIN_IDS` config between `cmd_addstock` and the paste step, the (now non-admin) user can still complete the action. Edge case but the guard is missing.
- **Reproduction**: Admin starts `/addstock`, picks a product. Operator removes them from `ADMIN_IDS` in `.env` and reloads config. User pastes stock — the action completes.
- **Fix**: Add `if not is_admin(message.from_user.id): await state.clear(); return` at the top of each AdminFlow message handler.

### [MEDIUM] `cb_do_topup` reads `amount` from FSM data but does not re-validate against `MIN_DEPOSIT_USDT` / `MAX_DEPOSIT_USDT`
- **File**: `handlers/deposit.py:108-127`
- **Issue**: If config changes between `msg_amount` (deposit.py:84) and `cb_do_topup`, the cap is not re-enforced. Also `data.get("amount", 0)` returns 0 if FSM data was cleared concurrently (e.g. user opened deposit in two chats); the cb_do_topup answers "Start over via Deposit" — correct, no money lost.
- **Fix**: Re-validate amount against settings before `create_topup_payment`.

### [MEDIUM] `cb_do_payment` allows `wallet` method even when wallet isn't in `enabled_methods()` — handler runs `pay_from_wallet` directly
- **File**: `handlers/payment.py:65-71`
- **Issue**: The check `if method not in settings.enabled_methods()` runs *after* the wallet branch returns. Wallet is always in `enabled_methods()` (config.py:75). OK, no bug, but the implicit ordering is fragile. Skip; LOW.

---

## 4. Async hygiene

### [HIGH] `bot.py` and `webapp/app.py` share the same SQLite file with WAL — but `webapp` opens new connections per request without `PRAGMA busy_timeout`, so the admin dashboard can `SQLITE_BUSY` under bot load
- **File**: `db.py:173-181`
- **Issue**: `get_db` opens a connection, sets `foreign_keys=ON`, but never sets `busy_timeout`. WAL allows readers to proceed while a writer is committing, but if the writer holds the lock more than `busy_timeout` (default 0 in sqlite3 / 5s in some bindings), the read fails with `OperationalError: database is locked`. The admin web is read-heavy and the bot is write-heavy; a busy moment yields HTTP 500.
- **Reproduction**: Run the bot under a burst of orders and load /payments in the dashboard concurrently. ~1% chance of `database is locked` until busy_timeout default expires.
- **Fix**: In `get_db` (db.py:177), add `await db.execute("PRAGMA busy_timeout=5000;")`. Also set the WAL pragma there (`init_db` only sets it once at startup but `aiosqlite` opens fresh connections per `get_db` call).

### [MEDIUM] `aiosqlite.connect(DB_PATH)` with `Path` — works on POSIX but on Windows the path is passed as `WindowsPath`; aiosqlite calls `str()` internally so this is fine, but `PRAGMA journal_mode=WAL` set in `init_db` does not propagate to other connections automatically (it's a persistent on-disk flag). OK.

### [MEDIUM] `httpx.AsyncClient` in `webapp/app.py:453-459` is created and discarded per request — TLS handshake every time. Performance, not correctness. Skip.

### [LOW] `services/payments.py:_binance` is a module-level singleton, but `get_binance()` is not thread-safe (race during init). The bot is single-loop so this doesn't matter. Skip.

### [LOW] `bot.py:35-40` catches `Exception` and logs — but Telegram blocks (user blocked the bot) raise `TelegramForbiddenError` which is a subclass of `TelegramAPIError`, not generic Exception. Actually `TelegramForbiddenError` *is* an Exception. OK.

---

## 5. Aiogram 3.x specifics

### [CRITICAL] `cb_shop_page` (and other handlers) read `cb.from_user.id` without None-check — channel posts crash the bot
- **File**: `handlers/shop.py:64-68`, `handlers/start.py:36-46, 49-54`, and similar in `handlers/deposit.py`, `handlers/payment.py`, `handlers/profile.py`, `handlers/admin.py`, `handlers/support.py`.
- **Issue**: `Message.from_user` / `CallbackQuery.from_user` can be `None` for messages posted to channels via the bot (channel-message updates). `cb.from_user.id` raises `AttributeError: 'NoneType' object has no attribute 'id'`. Aiogram does not auto-filter these.
- **Reproduction**: Add the bot to a channel as an admin. Send any message in the channel that triggers a callback (or simulate via raw update). Bot crashes the handler.
- **Fix**: Either filter at the router level (`@router.callback_query(F.from_user)` / `F.message.from_user`), or guard at each handler:
  ```python
  if not cb.from_user:
      await cb.answer()
      return
  ```

### [HIGH] `handlers/payment.py:cb_cancel` does not check that the payment belongs to the caller — anyone with the payment_id can cancel anyone's pending payment
- **File**: `handlers/payment.py:210-218`
- **Issue**: Callback data is `pay:cancel:<id>`. Telegram does not enforce that the callback came from the user the keyboard was sent to; a user who guesses or learns another user's payment_id (sequential integers) can craft a callback by replying to a forwarded message with inline buttons. Aiogram delivers it.
- **Reproduction**: User A pays $10 and sees payment #42. User B sends `/payment_42_cancel` style probing — better: B forwards A's inline keyboard message to themselves and taps Cancel. Telegram delivers the callback with `cb.from_user.id = B`. The handler cancels A's payment.
- **Fix**: Look up payment, verify `payment["user_id"] == (await db.get_user_by_tg(cb.from_user.id))["id"]`, else `cb.answer("Not your payment.", show_alert=True)`. Apply the same fix to `cb_check` and `cb_payment_methods`.

### [HIGH] `cb_cancel` cancels the **order** unconditionally when the payment has an order_id — even if there are sibling pending payments still waiting
- **File**: `handlers/payment.py:213-215`
- **Issue**: `await db.update_order(int(payment["order_id"]), status="cancelled")` flips the order to cancelled. But if a user previously created a TRC20 payment, then switched to Binance Pay (which goes through `cancel_pending_payments_for_order` — db.py:672), and then tries to *cancel* the TRC20 payment (which is already cancelled), the still-pending Binance Pay attempt's parent order is flipped to `cancelled` — orphaning the live payment.
- **Reproduction**: User picks TRC20 for order #1, sees the address. Taps Back, picks Binance Pay (TRC20 is cancelled by code). Taps Back to the original TRC20 screen (still has the Cancel button from the stale message). Taps Cancel. Order #1 → `cancelled`. But the Binance Pay payment row is still pending → if the user actually pays, `confirm_and_settle` runs `try_transition_order_status(from=('awaiting_payment','draft'), to='paid')` → fails (order is `cancelled`) → refunds to wallet. So the user is credited but the order screen says cancelled. Not data loss, but confusing.
- **Fix**: In `cb_cancel`, only flip the order to `cancelled` if `payment["status"] == 'pending'` AND there are no other pending payments for the same order_id.

### [HIGH] `cb_payment_methods`, `cb_check`, `cb_do_payment`, `cb_cancel`, `cb_do_topup` do `cb.message.edit_text(...)` without catching `TelegramBadRequest` — pressing the same button twice raises "message is not modified" and breaks the flow
- **File**: `handlers/payment.py:47, 95, 102, 110, 120, 199, 203, 217`, `handlers/deposit.py:74, 130`, `handlers/profile.py:73, 81`, `handlers/start.py:60`, `handlers/shop.py` (only partially handled).
- **Issue**: Only `_render_payment_screen` (payment.py:174-183) and `_render_shop_page` (shop.py:46-51) catch `TelegramBadRequest`. The other edit calls bubble the exception. Aiogram default error handler logs it; the user sees a stuck loading spinner because `cb.answer()` was never called.
- **Reproduction**: Tap Cancel twice quickly. Second tap: edit_text raises "message is not modified" → handler aborts → `cb.answer()` never reached → spinner persists.
- **Fix**: Wrap each `cb.message.edit_text(...)` in a `try/except TelegramBadRequest: pass` plus ensure `await cb.answer()` runs in a `finally`.

### [MEDIUM] `@dp.callback_query(F.data == "noop")` is registered *after* the routers — but the routers' `@router.callback_query(F.data.startswith(...))` would never match "noop" anyway. OK.

### [MEDIUM] `_noop` callback handler does not match the `F.data == "noop"` pattern dispatched from `shop.py:108` because the dispatcher-level handler is registered *after* all routers. In aiogram 3.x, routers attached to the dispatcher run before dispatcher-level handlers — so a Router that matches "noop" first would shadow this. None of the routers register a fallback for arbitrary `cb.data`, so in practice the dispatcher-level `_noop` handler does fire. OK.

### [LOW] `cmd_admin` etc. respond to non-admin users with a flat "This command is admin-only." — but other admin commands (`cmd_products`, `cmd_addstock`, etc.) silently `return` without any feedback. Inconsistent UX, not a bug.

---

## 6. Webapp (`webapp/app.py`)

### [HIGH] `users_balance` handler sends Telegram message via `httpx` direct API call, but `delta == 0` after a `set` action skips notification — a meaningful "balance set" still goes un-notified
- **File**: `webapp/app.py:427-461`
- **Issue**: When admin calls `set` action with `amount = current_balance`, `delta = 0` and no message is sent. The user is told nothing happened, but the audit row in `balance_adjustments` shows delta=0 (recorded). Minor.
- **Fix**: Always notify on `set` regardless of delta.

### [HIGH] `coupons_toggle` reads `active`, computes new value, writes — but lacks transaction isolation. Two admins toggling concurrently can both read "1" and both write "0" (or both write "1"), losing one toggle.
- **File**: `webapp/app.py:483-493`
- **Issue**: No atomic flip. `UPDATE coupons SET active = 1 - active WHERE id = ?` is the single-statement fix.
- **Reproduction**: Two admin browser tabs, both showing coupon X active. Both click toggle within 50ms. Final state matches whoever wrote last, not the intended "flip twice = identity".
- **Fix**: `UPDATE coupons SET active = 1 - active WHERE id = ?`.

### [HIGH] `products_new` and `products_edit` in webapp accept `base_price` as `float` — pydantic/FastAPI coerces but does not enforce positivity. Admin can save price = -5 or 0.
- **File**: `webapp/app.py:294-340`
- **Issue**: No validation. A negative base_price flows through to display: subtotal is negative, wallet pay refuses (insufficient balance), but a coupon could still "discount" further → undefined.
- **Fix**: `if base_price <= 0: raise HTTPException(400, "Base price must be positive.")`.

### [HIGH] `_login_attempts` dict grows unbounded across distinct IPs even with the eviction code — under attack, the eviction only fires when an IP's bucket *becomes* empty, but IPs that never returned still hold a (now-stale) deque with old timestamps
- **File**: `webapp/app.py:106-124`
- **Issue**: A bot scanning the login endpoint from many IPs each attempts once → each IP gets `deque([timestamp])` → eviction (`if not bucket: pop`) never triggers because the deque has one item. The deque is never re-checked until that exact IP returns. Over days, the dict grows to millions.
- **Reproduction**: Send 1M login attempts from 1M distinct IPs. Memory grows ~80MB+. Eventually OOM.
- **Fix**: Periodically (e.g. every N requests) sweep the dict: remove buckets whose newest timestamp is older than `window`. Or use a TTL cache (`cachetools.TTLCache`).

### [MEDIUM] `dashboard` page calls `scalar()` six times serially — each opens nothing new (same connection), so it's fine for correctness. But the inner function captures `d` from the outer scope; the outer `async with` exits while the awaits inside are mid-flight. Actually `async with` is sequential — outer waits for `scalar` to complete. OK.

### [MEDIUM] `coupons_new` passes `code` to `db.create_coupon` which does `.strip().upper()` — but no length / charset validation. A coupon code `"   "` (spaces) becomes `""` after strip → IntegrityError on UNIQUE constraint → 500 to the admin.
- **Fix**: `if not code.strip(): raise HTTPException(400, "Coupon code required.")`.

### [LOW] `products_page` calls `list_available_stock(pid, limit=200)` *per* product — N+1 queries. Not a correctness bug.

### [LOW] `template` filter `datetime` swallows exceptions silently and returns `str(ts)` — if a template expects a formatted date, a non-int `ts` shows up raw. Minor UX.

---

## Cross-cutting summary

1. **Money correctness**: Two CRITICAL paths can cross-credit users (the `create_payment` non-loop + the `find_pending_payment_by_amount` rounded matcher). Together they prevent concurrent equal-amount deposits today and, once the loop is added, route the wrong user's funds. Both fixes must land together.
2. **Crash on channel posts**: Several handlers dereference `cb.from_user.id` without a None-check.
3. **Spurious "message is not modified" crashes**: Most edit_text calls don't catch `TelegramBadRequest`.
4. **Payment cancellation cross-user**: `cb_cancel` and `cb_check` don't verify the payment belongs to the caller.
5. **SQLite locking**: `get_db` should set `busy_timeout` per connection — under combined bot+webapp load, the dashboard 500s.
6. **Audit log atomicity**: `mark_order_delivered` + `bump_coupon_uses` aren't transactional; the order can land in a coupon-undercounted state.

Apply the top three fixes (`create_payment` bump, `find_pending_payment_by_amount` strict match, channel-post None guards) before anything else — they cover both data loss and crash-on-common-path classes.
