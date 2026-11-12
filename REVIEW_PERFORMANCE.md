# Performance & Operations Review — telegram-shop

Reviewer scope: performance, async-correctness, and operations only.
Out of scope (other agents): security, correctness bugs, style.

Project: Python 3.11 / aiogram 3.13.1 / aiosqlite / httpx / FastAPI (admin) /
APScheduler. SQLite single-file. Bot + admin web run in one process from
`run.py`.

Overall verdict: the code is genuinely async-clean — no `requests`, no
`sqlite3.connect`, no `time.sleep`, no synchronous file I/O on hot paths in the
handlers. The biggest concrete risks are (1) per-call `aiosqlite.connect` /
close on every query, (2) N+1 queries on shop & admin product list pages, (3)
the broadcast loop being a serial blocking sender with no flood-control, and
(4) the Binance poller's quadratic risk on the on-chain `deposit_history`
matcher. Read the findings below in severity order.

---

## HIGH

### [HIGH] Broadcast loop is serial, blocking, no rate-limit, no flood-control
- **File**: `handlers/admin.py:399-406` and `handlers/admin.py:450-454`
- **Issue**: `cmd_broadcast` and `broadcast_restock` load every user row, then
  `await bot.send_message(...)` for each user **sequentially** in the same
  coroutine. Each call hits Telegram's API (~80-200ms RTT). For 1,000 users
  that's ~3 minutes during which `auto_payment_tick` cannot run (it can —
  APScheduler has its own task — but the broadcast monopolises the bot HTTP
  session connection pool and Telegram will start 429-throttling). Worse,
  failures are merely logged: there's no exponential backoff for 429s, no
  `await asyncio.sleep(0.05)` between sends to stay under Telegram's
  30-msg/sec global limit, and no batching. Once you hit 1,000+ users the
  bot will get rate-limited globally for all users for ~minute.
- **Impact**: Bites at >100 users for short messages, becomes catastrophic
  past 500 users. The admin who pressed /broadcast also has their FSM tied
  up until the loop ends.
- **Fix**:
  1. Cap concurrency with `asyncio.Semaphore(25)` and use `asyncio.gather`.
  2. Sleep ~33ms between sends (Telegram allows 30 broadcasts/sec).
  3. Catch `TelegramRetryAfter` and honour `retry_after`.
  4. Run the broadcast in a background task (`asyncio.create_task`) so the
     admin's handler returns immediately; report progress via a follow-up
     message. The same fix applies to `broadcast_restock`, which is invoked
     automatically every time admin adds stock — that's a foot-gun that
     freezes the bot's outbound queue.

### [HIGH] No `PRAGMA busy_timeout` — concurrent writes throw `SQLITE_BUSY`
- **File**: `db.py:125-145` (init_db), `db.py:173-181` (get_db)
- **Issue**: WAL is enabled (good), but `PRAGMA busy_timeout` is never set.
  With the **bot** and the **admin webapp** both opening connections against
  the same `shop.db`, and the bot itself opening fresh connections per
  call (each `get_db()` ⇒ new `aiosqlite.connect`), a write that races
  another writer raises `sqlite3.OperationalError: database is locked`
  **immediately**. This will surface during `claim_stock_items` (which uses
  `BEGIN IMMEDIATE`) overlapping with the admin clicking "add stock"
  from the dashboard, or with the auto-payment tick committing a
  `confirm_payment` while a user pays from wallet. WAL only allows *one*
  writer at a time; without a busy_timeout the second one fails instead of
  waiting.
- **Impact**: Bites the moment you have 2-3 concurrent users + the admin
  doing anything. Surfaces as random crashes in the bot logs, with users
  seeing "something went wrong" silently inside handlers that don't catch
  `OperationalError`.
- **Fix**: In both `init_db` and `get_db`, add
  `await db.execute("PRAGMA busy_timeout=5000;")` (5s wait). Also add
  `await db.execute("PRAGMA synchronous=NORMAL;")` for WAL — safe, ~2-5×
  faster commits than the default `FULL`. Consider also
  `PRAGMA temp_store=MEMORY;` and `PRAGMA cache_size=-20000;` (20MB).

### [HIGH] N+1 queries on `/products` admin page (and `_render_shop_page`)
- **File**: `webapp/app.py:262-292` (`products_page`) and
  `handlers/shop.py:40` (`stock_counts = {p["id"]: await db.stock_count(p["id"]) for p in products}`)
- **Issue**: The admin products page opens **5 separate SQLite connections
  per product**: `stock_count`, `count_sold_stock`, `list_available_stock`,
  `get_pricing_tiers`, and then `brand_choices_sorted` (cheap, but the four
  DB calls are not). 500 products = 2,000 sequential queries =
  2,000 × (open ⇒ pragma ⇒ query ⇒ close) round-trips. The shop list
  handler is lighter — only one query per product — but every browser of
  the shop runs it. For an active bot, `_render_shop_page` runs on every
  shop tap. With 40 products (the brand catalog suggests this is the
  expected size), each shop list render = 40 separate SQLite connections.
- **Impact**: At 1 product, fine. At 14 products (`PAGE_SIZE`), each shop
  open is ~14 connection open/close cycles + N+1 queries. With multiple
  concurrent users browsing, this is the path most likely to hit
  `SQLITE_BUSY`. The admin products page at 50+ products will take seconds
  to render.
- **Fix**:
  - **Shop list**: single grouped query — `SELECT product_id, COUNT(*) FROM
    stock_items WHERE sold = 0 AND product_id IN (...) GROUP BY product_id`,
    fed by the same connection that fetched products.
  - **Admin products page**: similar pattern; aggregate stock/sold counts
    with one `GROUP BY product_id` query, fetch all tiers in one `WHERE
    product_id IN (...)`, then bucket in Python. Also reuse a single
    connection (or move to a long-lived pool) so 5 calls become 1 round-trip
    each.

### [HIGH] No shared aiosqlite connection / pool — every query reopens the DB
- **File**: `db.py:173-181` (`get_db` context manager)
- **Issue**: Every single helper in `db.py` calls `get_db()` which calls
  `aiosqlite.connect(DB_PATH)` then `db.close()` on exit. `aiosqlite` spins
  up a **dedicated background thread per connection** and joins it on
  close. That's ~1-3ms overhead per query before SQLite even sees the
  request — multiply by the N+1 above and the cost adds up. It also makes
  the WAL writer-lock contention worse (more open handles thrashing).
- **Impact**: For a single user the latency tax is invisible (~5ms). At
  10+ msgs/min across users, the `aiosqlite` thread churn becomes
  measurable in top, and the bot CPU is dominated by `pthread_create`.
- **Fix**: Move to a single long-lived connection (SQLite is fine for one
  writer; readers can share it because aiosqlite serialises through a
  single thread) or a tiny pool of, say, 3 connections. Cache it in a
  module global initialised in `init_db()`. Apply the same `busy_timeout`
  and `synchronous=NORMAL` pragmas on creation, and reset
  `PRAGMA foreign_keys=ON` once instead of per `get_db()`.

---

## MEDIUM

### [MED] `poll_payments` is O(pending × deposits) and refetches `txid_already_used` per row
- **File**: `services/payments.py:70-100` (`_poll_onchain_deposits`)
- **Issue**: For each `(coin, network)` pair the worker fetches the last
  100 deposits from Binance, then for **every** deposit row, runs two
  separate DB queries: `txid_already_used` and `find_pending_payment_by_amount`.
  With 100 deposits and 5 pending payments that's 200 DB connections
  opened and closed per 30-second tick (the default `POLL_INTERVAL_SECONDS`).
- **Impact**: The tick runs in the bot's event loop. While it runs the
  bot remains responsive (everything is `await`ed), but it does generate
  steady background load. Will not bite at small scale; will compound with
  the connection-churn issue above on a busy VPS.
- **Fix**:
  - Cache confirmed TXIDs into a Python `set` on bot start
    (`SELECT txid FROM payments WHERE status='confirmed' AND txid IS NOT NULL`)
    so the dedupe check is O(1) in memory; add on confirmation.
  - Reuse one connection across the loop body.
  - Optionally pass `start_time_ms` to Binance to fetch only deposits since
    last successful poll; right now it grabs the full last 100 every tick.

### [MED] `auto_payment_tick` notifies users serially — one slow Telegram call stalls the rest
- **File**: `bot.py:129-136`
- **Issue**: After `poll_payments()` returns a list of events, the tick
  awaits `_notify_payment_event` for each event in sequence. If Telegram
  is slow (or one user has blocked the bot — then `send_message` raises
  and the `try/except` swallows it, but only after the SDK exhausts
  retries), the next user waits.
- **Impact**: Visible when 5+ deposits confirm in the same poll cycle, or
  when an expired-payment batch lands at TTL boundary.
- **Fix**: `await asyncio.gather(*(... for ev in events),
  return_exceptions=True)` — Telegram allows ~30 messages/sec globally and
  these are user-by-user sends so concurrency is fine.

### [MED] FSM uses `MemoryStorage` — restart-fragile, leaks memory under attack
- **File**: `bot.py:150`
- **Issue**: `Dispatcher(storage=MemoryStorage())` means any user
  mid-flow loses their state on restart (e.g. mid-checkout, mid-deposit
  amount entry, mid-admin product creation). More importantly, the
  in-process dict grows with every user who taps the bot and never expires.
  No TTL on state entries.
- **Impact**: Memory leak proportional to lifetime unique-users-who-tapped-anything.
  At ~1KB per user state with 10k users you're fine; at 100k users with a
  long-lived process you're not. Also: deploy-time annoyance — every push
  forces every mid-flow user back to the start.
- **Fix**: Switch to `aiogram.fsm.storage.redis.RedisStorage` (if Redis is
  acceptable) or write a periodic eviction sweep that drops entries older
  than N minutes from the underlying `MemoryStorage.storage` dict.

### [MED] `_load_emoji_ids()` / `_load(_BRAND_IDS_PATH)` re-read JSON from disk on every shop render
- **File**: `keyboards/icons.py:73-79` and `keyboards/product_icons.py:26-32`
- **Issue**: `method_label`, `method_custom_emoji_id`, and
  `icon_id_for_product` all call `_load(...)` which `path.read_text()` +
  `json.loads()`. `icon_id_for_product` is called once per product in the
  shop keyboard render, and within it `_load` may run **twice** (brand
  dict + dots dict). For a 14-product page that's up to 28 disk reads +
  JSON parses per shop render, blocking the event loop each time (sync
  I/O). The files are small (<1KB), so it's not catastrophic, but it's
  also pointless — these are static lookup tables.
- **Impact**: Low at normal load; under burst load (a popular product
  restock alert + lots of users tapping the shop) this is a constant
  unnecessary syscall load. Also: if the JSON file is being rewritten
  (e.g. by `setup_brand_logos.py`) you can momentarily read partial JSON
  and trip the `JSONDecodeError` fallback ⇒ icons disappear.
- **Fix**: Load these JSON files once at module import (or lazily, then
  cache in a module-level dict with `@functools.cache` on a tiny wrapper).
  Reload only on `SIGHUP` if you want hot-reload.

### [MED] Admin webapp calls Telegram synchronously inside the request lifecycle
- **File**: `webapp/app.py:441-459` (`users_balance`)
- **Issue**: After adjusting a user's balance the handler `await`s a
  brand-new `httpx.AsyncClient` then does a POST to Telegram with a 10s
  timeout. The HTTP request blocks the redirect back to `/users`. The
  comment in the file already calls this out ("a sync httpx.post here
  blocks the shared event loop"), but the async fix it ships is still
  inline. If Telegram is slow, the admin sits on a 10s spinner.
- **Impact**: Annoying for admins; doesn't impact bot users (the bot
  handles its own HTTPX traffic via aiogram's session). Also: a new
  `AsyncClient` is created per call — no connection reuse.
- **Fix**: Fire-and-forget with `asyncio.create_task` and use a single
  module-level `httpx.AsyncClient` (closed in a FastAPI `lifespan`
  shutdown hook). Or just call `bot.send_message` directly — the
  webapp could be handed the `Bot` instance from `run.py` at startup.

### [MED] `dispatcher.start_polling` finally-block does not drain in-flight handlers
- **File**: `bot.py:196-201`
- **Issue**: On SIGTERM, aiogram's `start_polling` cancels itself, but
  the `finally` block only `scheduler.shutdown(wait=False)` and closes
  Binance + bot session. Any handler currently running (e.g. mid-payment,
  mid-stock-claim) gets a `CancelledError` and the wallet debit / stock
  claim may complete halfway. The `BEGIN IMMEDIATE` in
  `db.claim_stock_items` should auto-rollback on connection close, so
  the data is safe. But the Telegram-side reply to the user is lost.
- **Impact**: Rare (only on restart/deploy); user has to retap to find
  out their order's status.
- **Fix**: Pass `dp.shutdown.register(...)` callbacks for cleanup. After
  `dp.start_polling` cancellation, allow a short grace window
  (`await asyncio.sleep(2)`) before tearing down `bot.session`. The
  systemd unit also needs `TimeoutStopSec=20` to give the process time
  to drain.

### [MED] `run.py` `FIRST_EXCEPTION` aborts the whole process on transient webapp errors
- **File**: `run.py:43-51`
- **Issue**: If uvicorn raises (e.g. port in use after a fast restart, or
  a single uncaught FastAPI exception in startup), the bot also dies.
  Combined with `Restart=always` in systemd this is a self-healing crash
  loop, but it means admin web outage = bot outage.
- **Impact**: Higher-than-necessary coupled-failure surface.
- **Fix**: Either separate the two processes (the docstring already says
  you can: "for production... run `python bot.py` and uvicorn
  independently") or wrap `web_task` in a supervisor that restarts it
  without killing the bot.

---

## LOW

### [LOW] `expire_old_payments` uses string interpolation for `IN (?, ?, ...)`
- **File**: `db.py:780-790`
- **Issue**: Not unsafe (the IDs come from a `SELECT` of the same table),
  but the dynamic SQL means SQLite re-prepares the statement every call.
  Same pattern in `delete_available_stock` (`db.py:475-483`).
- **Impact**: Negligible for normal sizes; only worth a footnote.
- **Fix**: Keep dynamic IN-list for now; if it ever becomes hot, prefer
  `executemany` per id or a `WITH RECURSIVE` join.

### [LOW] `webapp` and `bot` log to the same file via systemd append
- **File**: `deploy/telegram-shop.service:14-15`
- **Issue**: Both stdout/stderr go to `/var/log/telegram-shop/app.log`
  with `append:`. No logrotate config in `deploy/`. File grows
  unboundedly. Also: `access_log=True` in `run.py:32` adds two lines per
  admin request, including health checks if you ever wire one up.
- **Impact**: Disk-fill risk on long-running VPSs. Bites at ~6 months
  for a small shop, faster if the admin page is monitored.
- **Fix**: Use systemd's `journal` (drop the `StandardOutput=append:` and
  rely on `journalctl -u telegram-shop`) or ship a logrotate file in
  `deploy/`. Also set `uvicorn` `access_log=False` once you're past dev.

### [LOW] `BinanceClient` constructed lazily but `_binance` singleton not protected against import-time use
- **File**: `services/payments.py:24-31` and `bot.py:103` (`get_binance().server_time_ms()`)
- **Issue**: `get_binance()` constructs the client on first call,
  creating an `httpx.AsyncClient()`. If something calls this *outside* an
  event loop (e.g. a script that imports `services.payments`), httpx
  raises. At bot startup it's fine because `check_clock_skew` runs inside
  `asyncio.run`. But `scripts/diag_payments.py` etc. could land on it.
- **Impact**: Only an annoyance for script authors.
- **Fix**: Document, or move the `AsyncClient()` construction into an
  explicit `init()` coroutine.

### [LOW] No `MAX_RETRIES` cap on the Binance poller — it logs and continues forever
- **File**: `services/payments.py:75-82, 106-110`
- **Issue**: `BinanceError` is swallowed with `log.warning(...)` and the
  loop continues. If your API key is revoked or the VPS IP gets
  un-whitelisted on Binance, the bot keeps polling every 30s forever and
  logs forever. No alerting hook (e.g. notify ADMIN_IDS after N
  consecutive failures).
- **Impact**: Silent failure mode — users will report "my deposit didn't
  show up" before the operator notices in logs.
- **Fix**: Track a failure counter on the `BinanceClient`; after, say, 5
  consecutive errors send a DM to `settings.ADMIN_IDS[0]` via the bot.

### [LOW] Startup at import time is cheap, but `check_clock_skew()` is a blocking dependency
- **File**: `bot.py:179`
- **Issue**: `check_clock_skew()` is awaited synchronously before
  `dp.start_polling`. If Binance is slow at boot, bot start is delayed by
  up to 15s (the httpx timeout). On boot, you'd rather start the bot and
  let the clock-skew check log out-of-band.
- **Impact**: Slow restarts only.
- **Fix**: `asyncio.create_task(check_clock_skew())` — log warnings
  asynchronously without blocking poll start.

### [LOW] `recent_orders` joins products but doesn't paginate
- **File**: `db.py:600-608`
- **Issue**: Hardcoded `LIMIT 10` — fine for the UI. Same for `all_orders`
  (`LIMIT 500`). The admin webapp templates render all 500 in one HTML
  page; once you cross a few thousand orders you'll want server-side
  pagination and a sortable column index.
- **Impact**: Not yet a problem; flag for the future.
- **Fix**: Wire pagination into `/orders`, `/payments`, `/users` admin
  views before you have ~2,000 rows.

### [LOW] `shop.db` lives at the project root alongside source
- **File**: `db.py:10` (`DB_PATH = Path(__file__).parent / "shop.db"`)
- **Issue**: The DB file sits next to the code. It's correctly in
  `.gitignore`, and `deploy/deploy.ps1` ships a whitelisted set of files
  (so the dev DB doesn't overwrite prod). But it means:
  1. A naive `rsync -av` deploy would clobber prod.
  2. Backups need a separate path. Right now nothing in `deploy/`
     mentions DB backup.
  3. The `WorkingDirectory=/opt/telegram-shop` systemd unit means writes
     happen in the app dir — fine for SQLite, but if `ProtectSystem=full`
     is tightened to `strict` later, writes would be blocked.
- **Impact**: Operational, not runtime. Worth fixing before you launch
  paying customers.
- **Fix**: Move DB to `/var/lib/telegram-shop/shop.db` (or env-driven
  `DB_PATH`). Add a cron-driven `sqlite3 shop.db .backup ...` to
  `deploy/`.

### [LOW] `httpx.AsyncClient` in `BinanceClient` has no connection limits and a fixed timeout
- **File**: `services/binance.py:35`
- **Issue**: Default httpx pool: 100 max_connections, 20 keepalive — fine.
  Timeout 15s is reasonable. But there's no per-attempt retry / circuit
  breaker. Combined with the LOW finding above, transient 5xx from Binance
  means a delayed tick (not a stuck loop) — APScheduler `max_instances=1`
  + `coalesce=True` prevent pileup, so this is mild.
- **Fix**: Add `httpx.AsyncHTTPTransport(retries=2)` for transport-level
  retries on connection errors only.

### [LOW] `MemoryStorage` + `apscheduler` both pinned to the loop — no graceful FSM persistence
- See HIGH/MED notes. Tagging again because deploy hygiene (zero-downtime
  rollouts) is impossible with this setup.

---

## Things that are GOOD and shouldn't change

- WAL journal mode is enabled — keep it.
- Polling interval has a `max(10, ...)` floor — sensible.
- APScheduler uses `coalesce=True, max_instances=1` — prevents pileup.
- `claim_stock_items` uses `BEGIN IMMEDIATE` — correct for SQLite write
  isolation.
- Partial UNIQUE index on `(coin, network, unique_amount) WHERE
  status='pending'` — clever and exactly the right tool.
- All handlers are async; no `requests`, no `time.sleep`, no
  `sqlite3.connect` outside `scripts/`.
- The httpx call from the admin webapp at least uses `AsyncClient` (the
  comment shows the author thought about it).
- `.gitignore` excludes `shop.db` and WAL sidecars.
- systemd unit has `Restart=always`, `NoNewPrivileges`, `ProtectSystem`,
  `PrivateTmp` — solid baseline.
