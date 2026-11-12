# Code Quality & Maintainability Review

Scope: `c:\Users\ahmed\Desktop\telegram-shop`. Python 3.11, aiogram 3.13, FastAPI admin
webapp, SQLite. Security and runtime bugs are reviewed by separate agents. Findings
are grouped by category, ordered roughly by impact-per-fix.

---

## 1. Aiogram 3.x conventions

Overall the codebase uses aiogram 3 idioms correctly (Router objects, `F.data`
magic filters, `state.set_state()` / `state.update_data()` / `state.get_data()`,
`DefaultBotProperties(parse_mode=ParseMode.HTML)`, `Command(...)` filter from
`aiogram.filters`). No deprecated 2.x mix (no `Text(...)`, no `state.set_state`
inside `Dispatcher.message_handler`, no module-level `@dp.message_handler`).
Things to address are minor.

### Unused FSM `state` parameter in non-stateful callbacks
- **File**: `handlers/payment.py:188`, `handlers/payment.py:211`
- **Observation**: `cb_check` and `cb_cancel` declare `state: FSMContext` but
  never read or write it.
- **Why it matters**: Future readers assume the handler has FSM logic when it
  doesn't; mild signal noise.
- **Suggestion**: Drop the parameter — aiogram only injects what's declared.

### Module-level `settings = get_settings()` repeated in every handler
- **File**: `handlers/start.py:14`, `handlers/shop.py:21`,
  `handlers/payment.py:23`, `handlers/deposit.py:25`,
  `handlers/admin.py:19`, `handlers/support.py:10`,
  `services/payments.py:21`, `webapp/app.py:52`.
- **Observation**: Eight modules instantiate `Settings` at import. Works
  because of `@lru_cache`, but it couples import order to env loading and
  makes test isolation harder (you can't monkey-patch `get_settings()` after
  imports have run).
- **Why it matters**: Every handler that "tests easily in isolation" silently
  depends on `.env` being present at import time. Hot-reloading config after
  startup is impossible.
- **Suggestion**: Call `get_settings()` inside the handler, or pass settings
  via `workflow_data` / `dp["settings"]` once at startup. At minimum, document
  the load order.

### Manual `noop` callback handler registered outside a router
- **File**: `bot.py:160`
- **Observation**: `build_dispatcher` registers `_noop` directly on `dp` after
  `include_router(...)`. Mixing router-level and dispatcher-level handlers is
  legal but inconsistent with the rest of the codebase, which is 100% routers.
- **Suggestion**: Move into a small `shop` router (e.g. alongside `noop`
  page indicator usage in `keyboards/shop.py:108`).

---

## 2. `db.py` — 824-line god module

The module isn't egregiously bad — it has clean sections (`# ---- Users
----`, `# ---- Products ----`, etc.) and most queries are single-purpose — but
824 lines of "everything DB" in one file becomes a friction point fast.

### Split by aggregate
- **File**: `db.py` (entire file)
- **Observation**: One module owns users, products, stock, pricing tiers,
  coupons, orders, payments, balance adjustments, schema, migrations, AND the
  `get_db` connection context manager. The `# ---------- X ----------` banners
  are already telling you where the cut lines are.
- **Why it matters**: Every change to the payments domain forces every
  contributor to scroll past stock/coupons. Merge conflicts cluster on this
  one file.
- **Suggestion**: Carve out a `db/` package:
  - `db/__init__.py` — re-export public functions + `get_db`, `init_db`
  - `db/schema.py` — `SCHEMA` + `_migrate`
  - `db/users.py`, `db/products.py`, `db/stock.py`, `db/coupons.py`,
    `db/orders.py`, `db/payments.py`
  - Each ~50–150 lines. Names stay the same so callers don't change.

### Whitelist-based dynamic SQL is fragile
- **File**: `db.py:354–369` (`update_product`), `db.py:570–582` (`update_order`)
- **Observation**: `_PRODUCT_UPDATABLE = frozenset({...})` interpolated into
  `f"UPDATE products SET {cols} WHERE id = ?"`. Comment says "Whitelist column
  names — keys are interpolated into SQL." This works but is a vector for
  every future SQL injection attempt the moment someone forgets the comment.
- **Why it matters**: Onboarding cost — every reviewer has to convince
  themselves the whitelist is exhaustive. Easy to add a new column to
  `_PRODUCT_UPDATABLE` and forget the column name is glued into SQL.
- **Suggestion**: Build a tiny helper `_safe_update_sql(table, allowed, fields)`
  that returns `(sql, params)` and assert at module-load time that
  `_PRODUCT_UPDATABLE` is a subset of actual column names from `PRAGMA
  table_info`. Or migrate the two columns this is used for to explicit
  one-purpose setters.

### Schema string in one giant triple-quote
- **File**: `db.py:13–122`
- **Observation**: `SCHEMA` is a 110-line multi-statement string. Migrations
  in `_migrate` (`db.py:148–170`) are ad-hoc `ALTER TABLE` checks.
- **Why it matters**: There's no version table. The codebase already has two
  in-place migrations (`icon_custom_emoji_id`, `brand`) and an out-of-band
  `balance_adjustments` table created inside `_migrate`. The third migration
  will be added the same way and the file will keep growing.
- **Suggestion**: Either move to alembic for SQLite, or add a numbered list
  of `MIGRATIONS = [...]` functions plus a `schema_version` row in `settings`
  table so each migration runs once.

### N+1 queries in product listing
- **File**: `handlers/shop.py:40`, `handlers/admin.py:146`,
  `handlers/admin.py:154`, `webapp/app.py:268–273`
- **Observation**: `stock_counts = {p["id"]: await db.stock_count(p["id"])
  for p in products}` — one round trip per product. Same pattern in
  `cmd_products` and the admin `/products` page (which additionally fires
  `count_sold_stock`, `list_available_stock`, and `get_pricing_tiers` per
  row → up to 4 queries per product).
- **Why it matters**: On a shop with 200 products the admin page is 800+
  serialized SQLite queries. SQLite handles it now, but the pattern silently
  becomes the dashboard's bottleneck once the catalog grows.
- **Suggestion**: Add `db.stock_counts_for_products(ids)` returning
  `{pid: count}` via one `GROUP BY` query. Same for `count_sold_stock` and
  `get_pricing_tiers_for_products`.

### `expire_old_payments` uses `%` to splice IN-list
- **File**: `db.py:780–790`
- **Observation**: `"UPDATE payments ... WHERE id IN (%s)" % ",".join("?" * len(ids))`.
  Looks dangerous on first glance; it's not (the values are still parameterized
  via `?`), but it's the same loose pattern as `_PRODUCT_UPDATABLE` — visible
  string formatting next to SQL.
- **Suggestion**: Wrap in a tiny `_in_clause(values) -> tuple[str, tuple]`
  helper used by every multi-id update, so the pattern is named once.

---

## 3. Duplication

### "Emoji prefix" snippet repeated 5 places
- **File**: `handlers/admin.py:149`, `handlers/admin.py:436`,
  `handlers/profile.py:59`, `handlers/payment.py:133`, `handlers/shop.py:235`
- **Observation**: Identical 2-line block:
  ```
  emoji = (product["emoji"] or "").strip()
  prefix = f"{emoji} " if emoji else ""
  ```
- **Suggestion**: Add `keyboards/shop.py::product_prefix(product) -> str` or
  similar; one source of truth.

### "Send `/start` first" / "Order is no longer open" strings ad-hoc
- **File**: `handlers/payment.py:33,61,90`, `handlers/deposit.py:118`,
  `handlers/shop.py:196`
- **Observation**: Short user-facing strings ("Type /start.", "User missing —
  /start", "Order is no longer open.") are scattered. Different phrasings
  of the same idea.
- **Suggestion**: Centralize in `keyboards/icons.py` (or a new `messages.py`)
  alongside the existing button labels — also helps if the bot ever needs i18n.

### `from db import get_db` inside functions
- **File**: `handlers/admin.py:349, 394, 446`, `services/payments.py:205`,
  `webapp/app.py:222, 485`, `tests/smoke_test.py:156, 268, 316, 452`
- **Observation**: Ten places do an in-function `from db import get_db`. The
  comment justification is presumably "to avoid module-level side effects",
  but the function is already exposed at module scope.
- **Why it matters**: It hides the dependency from the import block and from
  tools like pyflakes / IDE imports. Reviewers can't grep module imports to
  see the full data dependency surface.
- **Suggestion**: Move all `get_db` imports to the top of each file. If you
  want to avoid raw SQL escaping into handlers, add wrapper functions in
  `db.py` (e.g. `db.broadcast_user_ids() -> list[int]`,
  `db.stats_overview() -> dict`).

### Telegram `edit_text` → fallback `answer` pattern
- **File**: `handlers/payment.py:174–183`, `handlers/shop.py:47–53`,
  `handlers/shop.py:256–261`, `handlers/deposit.py:34–37`
- **Observation**: Repeated try/except `TelegramBadRequest` → fallback to
  `answer` pattern. Slightly different in each site.
- **Suggestion**: One helper `await edit_or_send(target, text, kb)` in a
  `handlers/_utils.py`. Cuts ~30 lines and makes the "message is not
  modified" handling consistent.

### Two payment-text builders nearly identical
- **File**: `handlers/payment.py:126–161` (`_payment_text`),
  `handlers/deposit.py:135–154` (`_topup_text`)
- **Observation**: Same shape: method label, "send exactly X COIN", UID,
  address, expiry, payment ID. The order payment also adds product/quantity/
  total at the top.
- **Suggestion**: Factor a `_render_payment_block(payment, header_lines=[])`
  in `keyboards/payment.py` (or a `services/payment_text.py`). The
  deposit path passes `header_lines=[]`; the order path passes product info.

### Five duplicate "💰" constants in icons.py
- **File**: `keyboards/icons.py:19,20,53,54`
- **Observation**: `PAYABLE = "💰"`, `WALLET = "💰"`, `DEPOSIT = "💰"`,
  `TOPUP = "💰"` — same glyph, four names.
- **Why it matters**: If branding wants distinct icons later, every constant
  has to be re-pointed. If it stays one glyph, the multiple names are noise.
- **Suggestion**: Pick the semantic names you actually want (e.g. keep
  `WALLET` and `DEPOSIT`, drop `PAYABLE`/`TOPUP` aliases) or, conversely,
  pick different glyphs.

### Welcome menu duplicated in three places
- **File**: `handlers/start.py:36–46`, `handlers/start.py:49–54`,
  `handlers/start.py:57–61`, `handlers/start.py:64–67`
- **Observation**: Four handlers each call `welcome_text(...)` then
  `main_menu()`. The deep-link, `/start`, `/menu`, and "back to home"
  callback all do near-identical setup.
- **Suggestion**: Factor `_send_home(message_or_cb, edit: bool)`.

---

## 4. Dead code / stale references

### Unused imports
- **File**: `handlers/admin.py:6` (`F`), `handlers/admin.py:9`
  (`CallbackQuery`), `handlers/shop.py:14` (`QTY_PRESETS`),
  `handlers/payment.py:12` (`PAYMENT_METADATA`).
- **Observation**: `F` is imported in admin.py but no `F.*` filter is used
  there. `CallbackQuery` is in the import list but the file has no callback
  handlers. `QTY_PRESETS` and `PAYMENT_METADATA` are imported and never
  referenced.
- **Why it matters**: pyflakes/ruff would catch this immediately; their
  absence suggests no linter is wired into CI.
- **Suggestion**: Drop the imports and add `ruff check` to a pre-commit hook
  or CI.

### Docstring lists a route that doesn't exist
- **File**: `webapp/app.py:21`
- **Observation**: The Routes header advertises `POST /products/{id}/tier/delete`
  but no such handler is defined in the file (only `/products/{pid}/tier`
  for create exists).
- **Why it matters**: Stale documentation in the file's own opening
  docstring — new contributor will spend time hunting the missing route.
- **Suggestion**: Remove the line or implement the route.

### Schema notes "wallet" as payment method comment but it's not in payments table
- **File**: `db.py:92`
- **Observation**: Comment says `method TEXT NOT NULL -- binance_pay /
  usdt_bep20 / usdt_trc20`. Correct — wallet payments don't insert into
  `payments`. But `orders.payment_method` (`db.py:78`) lists
  `wallet / binance_pay / usdt_bep20 / usdt_trc20` — two near-duplicate
  enumeration comments, both maintained by hand.
- **Suggestion**: Convert both to a `class PaymentMethod(StrEnum)` in
  `config.py` (already a candidate — see #5).

### `scripts/diag_payments.py` opens DB with raw `sqlite3` instead of `aiosqlite`
- **File**: `scripts/diag_payments.py:8,20–21`
- **Observation**: One-off diag script uses synchronous `sqlite3` and ignores
  the project's `db.get_db()`. Other scripts go through `db.init_db()`.
- **Why it matters**: Two ways to read the same DB. If the project ever
  swaps storage (e.g. Postgres), this script silently breaks.
- **Suggestion**: Use `db.get_db()` like the other scripts, or mark this
  script "tool-only" and delete after the diagnostic is no longer needed
  (it appears to be a one-off).

### Commented-out / orphan branches
- Visual scan found no large commented-out blocks. Code is generally clean
  on this front.

---

## 5. Naming, magic values, enums

### String literals used as enums
- **File**: `db.py:79` (status TEXT), `db.py:101` (status TEXT),
  `services/delivery.py:32, 69`, `services/coupons.py:36-42`,
  `handlers/payment.py:33, 61`, etc.
- **Observation**: Order status (`draft`/`awaiting_payment`/`paid`/`delivered`/
  `failed`/`expired`/`cancelled`), payment status (`pending`/`confirmed`/
  `expired`/`cancelled`), coupon kind (`percent`/`fixed`), payment method
  names (`wallet`/`binance_pay`/`usdt_bep20`/`usdt_trc20`) — all stringly
  typed.
- **Why it matters**: Typo in `"awating_payment"` would compile, run, and
  silently lose a payment. IDE autocomplete is useless for status checks.
- **Suggestion**: Add `class OrderStatus(StrEnum)`, `PaymentStatus`,
  `CouponKind`, `PaymentMethod` in `config.py` or a `domain.py`. SQLite
  doesn't care; the enum just constrains the Python side.

### Untyped `product`/`order`/`target` parameters
- **File**: `handlers/payment.py:87` (`order`), `:126` (`payment, order,
  product`), `handlers/shop.py:79` (`product`), `:89` (`product, …, tiers`),
  `:188` (`target_message`), `:226` (`target_message`),
  `handlers/deposit.py:28` (`target`), `:135` (`payment`),
  `handlers/admin.py:433` (`product`).
- **Observation**: Many helpers take untyped `aiosqlite.Row` parameters
  named with bare nouns. Reading `_payment_text(payment, order, product)`
  in isolation gives you zero idea what shape each carries.
- **Suggestion**: Either typed `aiosqlite.Row` (matches `services/pricing.py`)
  or — better — `TypedDict`/`dataclass` wrappers for the three core entities
  (Order, Product, Payment) at the boundary of db.py. The latter gives
  attribute access (`order.status`) and type-checked field names.

### `delete_product` returns a string status
- **File**: `db.py:372–401`
- **Observation**: Returns `"deleted"` unconditionally. Docstring and tests
  treat it as if it might return other values — historic remnant of an
  earlier soft/hard delete branch (now removed; the smoke test name is
  `test_delete_product_always_hard`).
- **Suggestion**: Make it return `None` or `bool` (was it actually deleted?).
  The string is dead-effort.

### `data` / `r` / `p` / `t` single-letter variables
- **File**: `db.py:189-214, 264-275, 779-790`, `webapp/app.py:223-258,
  483-493`, `handlers/admin.py:332-339, 357-364`, `services/payments.py:84-99,
  207-214`.
- **Observation**: `r`, `p`, `t`, `d` are extremely common (`r` for "row",
  `p` for "payment" or "product" — overloaded across files).
- **Suggestion**: Loop targets are fine short, but DB connection variables
  (`d = await aiosqlite.connect(...)`) and `r = await cur.fetchone()`
  followed by 20 lines of usage hurts. Stick to `row`, `db_conn`, `prod`.

### `process_x` family
- No `process_x` clutter found. Handler names (`cb_do_payment`,
  `addp_emoji`, `addtier_pct`) are abbreviation-heavy but at least
  domain-meaningful. `addp_*`/`addc_*`/`addtier_*` prefixes are mildly
  cryptic — `addproduct_emoji_state` / `addcoupon_value_state` would
  scan better.

---

## 6. Type hints

### Inconsistent typing across the codebase
- **File**: `services/pricing.py` and `services/binance.py` are fully typed.
  `db.py` is typed (good return types like `Optional[aiosqlite.Row]`).
  Handlers (`handlers/*.py`) mostly type `cb: CallbackQuery` and
  `state: FSMContext` but the entity helpers (`_payment_text(payment, order,
  product)`) are bare.
- **Suggestion**: Run mypy / pyright. The biggest gap is "Row passed
  positionally between layers"; once those are typed, the rest follows.

### Generic `tuple` instead of `tuple[T, U]`
- **File**: `db.py:256` returns `tuple[float, float]`, `db.py:280` same.
- **Observation**: Returning two unlabeled floats — caller has to know
  which one is "before" and which is "after".
- **Suggestion**: `class BalanceChange(NamedTuple): before: float; after: float`.

---

## 7. Error handling / logging

### Logging is generally good
- `bot.py:21–25` sets up logging, services use module loggers
  (`log = logging.getLogger(__name__)`). No `print()` in runtime code.
- All `print()` calls are confined to `scripts/` (CLI tools) and `tests/`
  (test progress), which is appropriate.

### Multiple `except Exception:` swallow paths
- **File**: `bot.py:39, 61, 77, 90, 125, 132`, `handlers/admin.py:403, 453`,
  `services/delivery.py:159`, `webapp/app.py:61, 458–459`, `db.py:509`.
- **Observation**: Most are appropriate ("notify expired failed for X" =
  user may have blocked the bot; that's expected). But several use
  `log.info(...)` for what is really a Telegram API failure (`bot.py:40`,
  `:62`, `:78`, `:91`). One uses `pass` silently
  (`webapp/app.py:458–459` — Telegram notification failure on balance
  change).
- **Why it matters**: Failed user notifications are downgraded to `INFO`
  and to silence. A widespread Telegram outage produces no visible signal.
- **Suggestion**: Use `log.warning` for send-message failures with
  exception type. Add a metric counter if you ever wire to Prometheus.

### `services/delivery.py:159` swallows referrer credit failures
- **File**: `services/delivery.py:156–161`
- **Observation**:
  ```python
  try:
      from services.payments import maybe_credit_referrer
      await maybe_credit_referrer(int(payment_row["user_id"]), actual_amount)
  except Exception:
      log.exception("maybe_credit_referrer failed (non-fatal)")
  ```
  This is correctly logged with `log.exception` (full traceback), so it's
  fine — flagging only because the bare `Exception` catch could mask
  programming errors (e.g. AttributeError) the same as transient ones.
- **Suggestion**: Tighten to `except (BinanceError, aiosqlite.Error,
  RuntimeError)` once the failure modes are catalogued.

### `webapp/app.py:61` swallows datetime format error silently
- **File**: `webapp/app.py:58–62`
- **Observation**:
  ```python
  def _datetime_filter(ts) -> str:
      try:
          return time.strftime(...)
      except Exception:
          return str(ts)
  ```
- **Why it matters**: If the column ever stops being an int (schema drift),
  every row shows the raw value with no breadcrumb in logs.
- **Suggestion**: `log.debug("non-int timestamp: %r", ts)` in the except.

---

## 8. Config / magic numbers

### Many magic numbers in source
- **File**: `keyboards/shop.py:7` (`QTY_PRESETS = [1,2,3,5,10,15,20,25]`),
  `:6` (`PAGE_SIZE = 14`), `handlers/shop.py:166` (`qty < 1 or qty > 1000`),
  `handlers/admin.py:251` (`qty < 2`), `handlers/admin.py:263`
  (`pct < 0 or pct > 90`), `services/payments.py:42`
  (`_alnum_note(n: int = 6)`), `bot.py:184` (`max(10, settings.POLL_INTERVAL_SECONDS)`),
  `bot.py:122` (`24 * 3600`), `db.py:714` (`older_than_seconds=24*3600`),
  `webapp/app.py:77` (`SESSION_TTL_SECONDS = 60*60*24*7`).
- **Suggestion**: Promote to `config.Settings` (defaults are fine):
  `MAX_QUANTITY_PER_ORDER`, `MIN_TIER_QTY`, `MAX_TIER_DISCOUNT_PCT`,
  `STALE_DRAFT_AGE_SECONDS`, `MIN_POLL_INTERVAL_SECONDS`,
  `SESSION_TTL_SECONDS`, `PAGE_SIZE`, `QTY_PRESETS`.

### Polling-interval clamp lives in `bot.py`, not config
- **File**: `bot.py:184` — `seconds=max(10, settings.POLL_INTERVAL_SECONDS)`
- **Observation**: The "10-second floor" is a policy decision sitting in a
  scheduler call site.
- **Suggestion**: Validate at `Settings` load time (pydantic validator) so
  the value is correct everywhere it's read.

### Telegram parse mode and message glue strings
- **File**: `bot.py:67–73` (delivery card), `handlers/payment.py:117–120`
  (wallet delivery card), `services/delivery.py:39, 41` etc.
- **Observation**: The "head + body + foot" delivery card is built ad-hoc
  in two different code paths (auto-poll path in `bot.py`, wallet path in
  `payment.py`).
- **Suggestion**: One `services/delivery.py::format_delivery_card(order,
  product, contents, charged_str) -> str` so both paths show the same thing.

---

## 9. Tests

### One file, scripted-not-framework-based
- **File**: `tests/smoke_test.py` (461 lines)
- **Observation**: Hand-rolled test runner — `async def main()` calling
  test functions in sequence, using `assert` statements, sharing a single
  on-disk SQLite file at `tests/_smoke.db` that is wiped between groups via
  `_reset_db()` (which `DELETE`s rows in dependency order). Covers the
  most important state machine paths (concurrent payment uniqueness,
  double-payment refund, coupon usage gates, wallet-pay status gate, etc).
- **Why it matters**: The tests *do* exercise real bug regressions — this
  is the most valuable file in the codebase from a defensive-engineering
  standpoint. But:
  - No framework → no per-test isolation; a failure mid-suite leaves
    `_smoke.db` in an indeterminate state.
  - No CI integration (no `pytest`, no `tox`, no GitHub Actions config
    visible).
  - No tests for: handlers (callback/state transitions), keyboards
    (paging math), webapp (login flow), config validation, the
    `_features_block` / `_product_card` formatters.
- **Suggestion**: Port to pytest with `pytest-asyncio`:
  - One fixture for `tmp_path / "shop.db"` that resets per test.
  - Existing `test_*` functions become pytest functions almost as-is.
  - Add: a tiny smoke test for `payment_methods_text()` /
    `_payment_text()` to lock in the HTML escape behavior, since both
    are user-facing.
  - Add a CI workflow to run on push.

### Coverage gaps that matter
- **Concurrency** — the "concurrent payment creation" test is single-
  threaded; the bug it's protecting against is concurrent. Use
  `asyncio.gather()` to actually race two `create_payment` coroutines.
- **The Binance polling loop** — `services/payments._poll_*` functions are
  not exercised end-to-end against fixtures. `_usdt_amount_from_pay_tx` is
  unit-tested in two cases.

---

## 10. Webapp templates

### Generally clean — a Jinja macro is doing the right thing
- **File**: `webapp/templates/_logo_picker.html`
- **Observation**: The logo picker is factored into a macro used by both
  Create-product and Edit-product forms. Good.

### Same datetime + status-pill pattern repeated in 3 templates
- **File**: `dashboard.html:25–35`, `orders.html:15–30`, `payments.html:14–28`
- **Observation**: Each has its own `<tr class="row-{status}">` block with
  `<span class="pill pill-{status}">`. The "user" cell pattern (`@username`
  or fallback to `tg_id`) appears in 4 templates.
- **Suggestion**: Two Jinja macros — `{{ user_cell(o) }}` and
  `{{ status_pill(o.status) }}` — in a `_macros.html` partial.

### Forms are hand-rolled (no Flask-WTF / similar)
- **File**: All POST routes in `webapp/app.py`
- **Observation**: Forms use FastAPI `Form(...)` for parameter binding (fine)
  and raw HTML for fields. There's no CSRF token, no client-side validation
  consistency. (CSRF specifically is out of scope here — security agent —
  but it's worth noting that without a form abstraction, future fields are
  one more form to maintain in two places.)
- **Suggestion**: Acceptable as-is for a small admin. If forms grow,
  fastapi-utils / Pydantic form models reduce the field-list duplication.

### Big inline `<script>` in `products.html`
- **File**: `webapp/templates/products.html:151–220` (~70 lines of JS)
- **Observation**: Logo picker + stock-select-all wiring in one inline
  block. Means changes require touching a template file rather than a
  `.js` file. No source map, no linting.
- **Suggestion**: Move to `webapp/static/products.js`.

---

## 11. `scripts/` directory

### Scripts are mostly current but `scripts/diag_payments.py` looks one-off
- **File**: `scripts/diag_payments.py`
- **Observation**: A "one-off diagnostic" labeled as such in its docstring,
  uses raw `sqlite3` (not the project's `aiosqlite` wrapper), and has no
  obvious caller. The `binance_health_check.py` covers the same ground
  more comprehensively.
- **Suggestion**: Delete or fold into `binance_health_check.py`.

### `scripts/purge_all_products.py` is a destructive footgun
- **File**: `scripts/purge_all_products.py`
- **Observation**: No `--yes` flag, no confirmation prompt, no `--dry-run`.
  Iterates every product through `db.delete_product` (which cascades to
  orders and stock).
- **Why it matters**: If anyone double-clicks this from a dev environment
  it wipes the catalog and order history.
- **Suggestion**: Require `--yes-i-mean-it`, default to dry-run.

### `scripts/setup_payment_emojis.py:95` references a "legacy_name" that's identical to current
- **File**: `scripts/setup_payment_emojis.py:95`
- **Observation**: `legacy_name = f"AkundingPayIcons_by_..."` — but the
  current set name is `f"AkundingPayColor_by_..."`. Then it deletes both
  in a loop. The intent is "delete the old-name pack too" but the variable
  name and comment trail are confusing.
- **Suggestion**: Comment why these two names exist (migration from one
  pack name to another). Or, if the migration is finished, drop
  `legacy_name`.

### Similar legacy-name pattern in `setup_product_emojis.py`
- **File**: `scripts/setup_product_emojis.py:77` — `legacy = f"AkundingProductIcons_by_..."`
  is *equal* to `set_name`. Then the loop deletes it twice in a row
  (once as `legacy`, once as `set_name`). Harmless but wrong.
- **Suggestion**: Either remove `legacy` or set it to the actual old name.

### `__pycache__` directories committed-shaped
- **File**: `__pycache__/` exists in 6 places. Suggests no `.gitignore`
  excluding them. Confirm `.gitignore` covers `__pycache__`, `.venv/`,
  `shop.db`, `tests/_smoke.db`, `*.pyc`.

---

## 12. `print()` vs `logging`

Summary: **all `print()` usage is acceptable** — confined to
`scripts/*.py` (CLI ops tools, where stdout is the intended output) and
`tests/smoke_test.py` (test progress output). The runtime bot, services,
db, and webapp modules all use `logging` exclusively. Concrete counts:

- `bot.py`, `db.py`, `webapp/app.py`, `services/*.py`, `handlers/*.py`,
  `config.py`, `run.py`, `states.py`, `keyboards/*.py`: **0 prints**.
- `scripts/binance_health_check.py`: 30+ prints (CLI tool — correct).
- `scripts/setup_brand_logos.py`, `setup_payment_emojis.py`,
  `setup_product_emojis.py`, `assign_product_brands.py`,
  `list_inactive_products.py`, `purge_all_products.py`,
  `diag_payments.py`: prints throughout (CLI tools — correct).
- `tests/smoke_test.py`: 30+ prints for test progress (acceptable;
  would normally be replaced by pytest's own output).

No action needed for category 12, but worth keeping the invariant
explicit: "no `print()` in runtime modules" is a useful pre-commit
check.

---

## Suggested priority order

If only a handful of these are addressed, the highest-impact set is:

1. **Split `db.py` into `db/` package** — biggest readability win, no risk.
2. **Introduce `OrderStatus` / `PaymentStatus` / `CouponKind` /
   `PaymentMethod` enums** — eliminates the entire class of "typo in a
   status string silently breaks the state machine" bug.
3. **Eliminate N+1 query patterns in admin `/products` and bot shop list**
   — currently invisible, becomes a problem at scale.
4. **Add ruff + a CI workflow** — `F`, `CallbackQuery`, `QTY_PRESETS`,
   `PAYMENT_METADATA` unused imports + stale docstring routes would be
   caught automatically.
5. **Port `tests/smoke_test.py` to pytest, add a single CI job** —
   the tests are already valuable; CI guarantees they stay green.
6. **Factor the "edit_or_send", "emoji_prefix", "payment text block"
   duplications** — small, mechanical, ~50–100 lines saved.

Everything else is polish.
