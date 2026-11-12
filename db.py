from __future__ import annotations

import logging
import math
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Iterable, Optional

import aiosqlite

log = logging.getLogger(__name__)

DB_PATH = Path(
    os.environ.get("SHOP_DB_PATH")
    or os.environ.get("TS_DB_PATH")
    or (Path(__file__).parent / "shop.db")
).expanduser()

# Money is rounded to this many decimal places on every balance write so
# repeated float add/subtract can't accumulate sub-cent drift (see review M1).
MONEY_DP = 4
PAYMENT_UNIQUE_BUMP = 0.01


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id           INTEGER NOT NULL UNIQUE,
    username        TEXT,
    first_name      TEXT,
    balance         REAL NOT NULL DEFAULT 0,
    referrer_id     INTEGER,
    created_at      INTEGER NOT NULL,
    FOREIGN KEY (referrer_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS products (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    emoji           TEXT DEFAULT '',
    description     TEXT DEFAULT '',
    features        TEXT DEFAULT '',          -- one feature per line, shown in detail card
    unit_label      TEXT DEFAULT 'code',      -- "code" / "key" / "account"
    base_price      REAL NOT NULL,
    active          INTEGER NOT NULL DEFAULT 1,
    created_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS pricing_tiers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id      INTEGER NOT NULL,
    min_qty         INTEGER NOT NULL,
    discount_pct    REAL NOT NULL,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS stock_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id      INTEGER NOT NULL,
    content         TEXT NOT NULL,
    cost            REAL,                     -- per-item cost the owner paid; NULL = untracked (excluded from profit)
    sold            INTEGER NOT NULL DEFAULT 0,
    sold_at         INTEGER,
    order_id        INTEGER,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_stock_product_sold ON stock_items(product_id, sold);
CREATE INDEX IF NOT EXISTS idx_stock_order ON stock_items(order_id);

CREATE TABLE IF NOT EXISTS coupons (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT NOT NULL UNIQUE,
    kind            TEXT NOT NULL,            -- percent / fixed
    value           REAL NOT NULL,
    max_uses        INTEGER NOT NULL DEFAULT 0,   -- 0 = unlimited
    used_count      INTEGER NOT NULL DEFAULT 0,
    active          INTEGER NOT NULL DEFAULT 1,
    expires_at      INTEGER,
    created_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    product_id      INTEGER NOT NULL,
    quantity        INTEGER NOT NULL,
    unit_price      REAL NOT NULL,
    subtotal        REAL NOT NULL,
    coupon_code     TEXT,
    discount        REAL NOT NULL DEFAULT 0,
    referral_discount REAL NOT NULL DEFAULT 0,
    payable         REAL NOT NULL,
    payment_method  TEXT,                     -- wallet / binance_pay / usdt_bep20 / usdt_trc20
    status          TEXT NOT NULL,            -- draft / awaiting_payment / paid / delivered / failed / expired / cancelled
    created_at      INTEGER NOT NULL,
    delivered_at    INTEGER,
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (product_id) REFERENCES products(id)
);
CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status, created_at);

CREATE TABLE IF NOT EXISTS referral_rewards (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    referrer_id     INTEGER NOT NULL,
    referred_id     INTEGER NOT NULL,
    order_id        INTEGER NOT NULL,
    amount          REAL NOT NULL,
    created_at      INTEGER NOT NULL,
    FOREIGN KEY (referrer_id) REFERENCES users(id),
    FOREIGN KEY (referred_id) REFERENCES users(id),
    FOREIGN KEY (order_id) REFERENCES orders(id),
    UNIQUE (referred_id),
    UNIQUE (order_id)
);
CREATE INDEX IF NOT EXISTS idx_referral_rewards_referrer
    ON referral_rewards(referrer_id, created_at DESC);

CREATE TABLE IF NOT EXISTS payments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    order_id        INTEGER,                  -- NULL means wallet top-up
    method          TEXT NOT NULL,            -- binance_pay / usdt_bep20 / usdt_trc20
    network         TEXT NOT NULL,            -- BSC / TRX / BINANCE_INTERNAL
    coin            TEXT NOT NULL DEFAULT 'USDT',
    base_amount     REAL NOT NULL,
    unique_amount   REAL NOT NULL,
    note            TEXT NOT NULL,            -- short alnum reference shown to the user
    address         TEXT,                     -- on-chain destination address (NULL for binance_pay)
    binance_uid     TEXT,                     -- shop's Binance UID (only for binance_pay)
    txid            TEXT,
    status          TEXT NOT NULL,            -- pending / confirmed / expired / cancelled
    created_at      INTEGER NOT NULL,
    expires_at      INTEGER NOT NULL,
    confirmed_at    INTEGER,
    raw             TEXT,
    credited        INTEGER NOT NULL DEFAULT 0,  -- wallet top-up credit applied exactly once
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (order_id) REFERENCES orders(id)
);
CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status, expires_at);
CREATE INDEX IF NOT EXISTS idx_payments_unique_amt ON payments(coin, network, unique_amount, status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_payments_txid ON payments(txid) WHERE txid IS NOT NULL;
-- Enforces "no two pending payments may share (coin, network, unique_amount)".
-- This is what makes concurrent payment creation safe — the DB rejects the
-- second insert and our create_payment() loop bumps unique_amount by one cent
-- and retries, so every concurrent pending invoice ends up with a distinct
-- amount that an incoming deposit can be attributed to unambiguously.
CREATE UNIQUE INDEX IF NOT EXISTS uq_payments_pending_amount
    ON payments(coin, network, unique_amount) WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS unmatched_deposits (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    coin            TEXT NOT NULL DEFAULT 'USDT',
    network         TEXT NOT NULL,
    amount          REAL NOT NULL,
    txid            TEXT NOT NULL UNIQUE,
    raw             TEXT,
    status          TEXT NOT NULL DEFAULT 'open', -- open / credited / ignored
    user_id         INTEGER,
    created_at      INTEGER NOT NULL,
    resolved_at     INTEGER,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_keys (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    key_hash        TEXT NOT NULL UNIQUE,
    key_prefix      TEXT NOT NULL,
    created_at      INTEGER NOT NULL,
    revoked_at      INTEGER,
    last_used_at    INTEGER,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_api_keys_active_user
    ON api_keys(user_id) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash);

CREATE TABLE IF NOT EXISTS api_orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    api_key_id      INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    order_id        INTEGER NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL,
    created_at      INTEGER NOT NULL,
    FOREIGN KEY (api_key_id) REFERENCES api_keys(id),
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (order_id) REFERENCES orders(id),
    UNIQUE (api_key_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_api_orders_user ON api_orders(user_id, created_at DESC);
"""


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA foreign_keys=ON;")
        # Pre-existing duplicate pending amounts (from older code without the
        # partial UNIQUE index) would block the index creation. Cancel the dupes
        # before applying the schema. Safe no-op on a fresh DB.
        try:
            await db.execute(
                "UPDATE payments SET status = 'cancelled' "
                "WHERE status = 'pending' AND id NOT IN ("
                "  SELECT MIN(id) FROM payments WHERE status = 'pending' "
                "  GROUP BY coin, network, unique_amount"
                ")"
            )
        except aiosqlite.OperationalError:
            # 'payments' table not created yet on a brand-new install — fine.
            pass
        await db.executescript(SCHEMA)
        await _migrate(db)
        await db.commit()


async def _migrate(db: aiosqlite.Connection) -> None:
    cur = await db.execute("PRAGMA table_info(products)")
    cols = {row[1] for row in await cur.fetchall()}
    if "icon_custom_emoji_id" not in cols:
        await db.execute(
            "ALTER TABLE products ADD COLUMN icon_custom_emoji_id TEXT DEFAULT ''"
        )
    if "brand" not in cols:
        await db.execute(
            "ALTER TABLE products ADD COLUMN brand TEXT DEFAULT ''"
        )
    if "delivery_instructions" not in cols:
        await db.execute(
            "ALTER TABLE products ADD COLUMN delivery_instructions TEXT DEFAULT ''"
        )
    if "warranty_days" not in cols:
        await db.execute(
            "ALTER TABLE products ADD COLUMN warranty_days INTEGER NOT NULL DEFAULT 0"
        )
    # Admin-controllable display order. Backfill from id on first add so the
    # existing arrangement is preserved, then the dashboard can reorder.
    if "sort_order" not in cols:
        await db.execute(
            "ALTER TABLE products ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0"
        )
        await db.execute("UPDATE products SET sort_order = id")
    # Bulk pricing moved from a percentage discount to an explicit per-unit
    # USDT price. Legacy rows keep their discount_pct (unit_price NULL) and are
    # converted on read in services.pricing; new rows write unit_price.
    cur = await db.execute("PRAGMA table_info(pricing_tiers)")
    tier_cols = {row[1] for row in await cur.fetchall()}
    if "unit_price" not in tier_cols:
        await db.execute("ALTER TABLE pricing_tiers ADD COLUMN unit_price REAL")
    # Per-item stock cost for profit tracking. Nullable with no default: old
    # rows stay NULL = "untracked" and are excluded from profit (the owner
    # opted to start fresh rather than backfill). New stock writes a cost.
    cur = await db.execute("PRAGMA table_info(stock_items)")
    stock_cols = {row[1] for row in await cur.fetchall()}
    if "cost" not in stock_cols:
        await db.execute("ALTER TABLE stock_items ADD COLUMN cost REAL")
    # Idempotent "this top-up's wallet credit has been applied" flag, so the
    # confirm→credit step is atomic+recoverable and never double-credits. (review H6)
    # Per-user UI language for the buyer-facing bot (i18n). Nullable with no
    # default on purpose: NULL = "hasn't chosen yet" -> the /start language
    # picker fires once. Existing users get the picker on their next /start.
    cur = await db.execute("PRAGMA table_info(users)")
    user_cols = {row[1] for row in await cur.fetchall()}
    if "language" not in user_cols:
        await db.execute("ALTER TABLE users ADD COLUMN language TEXT")
    if "blocked" not in user_cols:
        await db.execute(
            "ALTER TABLE users ADD COLUMN blocked INTEGER NOT NULL DEFAULT 0"
        )
    cur = await db.execute("PRAGMA table_info(orders)")
    order_cols = {row[1] for row in await cur.fetchall()}
    if "referral_discount" not in order_cols:
        await db.execute(
            "ALTER TABLE orders ADD COLUMN referral_discount REAL NOT NULL DEFAULT 0"
        )
    cur = await db.execute("PRAGMA table_info(payments)")
    payment_cols = {row[1] for row in await cur.fetchall()}
    if "credited" not in payment_cols:
        await db.execute(
            "ALTER TABLE payments ADD COLUMN credited INTEGER NOT NULL DEFAULT 0"
        )
        # CRITICAL backfill: every payment that already exists predates this
        # feature and was already settled under the old code path. Mark them
        # credited=1 so the startup reconcile sweep does NOT re-credit historical
        # confirmed top-ups. Only rows inserted after this migration start at 0
        # and flow through credit_topup_once. (review H6 / migration safety)
        await db.execute("UPDATE payments SET credited = 1")
    cur = await db.execute("PRAGMA table_info(unmatched_deposits)")
    unmatched_cols = {row[1] for row in await cur.fetchall()}
    if "status" not in unmatched_cols:
        await db.execute(
            "ALTER TABLE unmatched_deposits ADD COLUMN status TEXT NOT NULL DEFAULT 'open'"
        )
        unmatched_cols.add("status")
    if "user_id" not in unmatched_cols:
        await db.execute("ALTER TABLE unmatched_deposits ADD COLUMN user_id INTEGER")
        unmatched_cols.add("user_id")
    if "resolved_at" not in unmatched_cols:
        await db.execute("ALTER TABLE unmatched_deposits ADD COLUMN resolved_at INTEGER")
        unmatched_cols.add("resolved_at")
    if "resolved" in unmatched_cols:
        await db.execute(
            "UPDATE unmatched_deposits "
            "SET status = CASE WHEN COALESCE(resolved, 0) != 0 THEN 'credited' "
            "ELSE COALESCE(NULLIF(status, ''), 'open') END"
        )
    else:
        await db.execute(
            "UPDATE unmatched_deposits SET status = 'open' "
            "WHERE status IS NULL OR status = ''"
        )
    if "resolved_user_id" in unmatched_cols:
        await db.execute(
            "UPDATE unmatched_deposits SET user_id = resolved_user_id "
            "WHERE user_id IS NULL AND resolved_user_id IS NOT NULL"
        )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_unmatched_status "
        "ON unmatched_deposits(status, created_at)"
    )
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS balance_adjustments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            delta           REAL NOT NULL,
            balance_before  REAL NOT NULL,
            balance_after   REAL NOT NULL,
            note            TEXT DEFAULT '',
            created_at      INTEGER NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        -- Funnel/engagement analytics for the admin Statistics tab. We store
        -- tg_id (not the internal user id) so logging is a single cheap INSERT
        -- with no lookup on the bot's hot path, and admin-exclusion is a direct
        -- `tg_id NOT IN (...)`. type ∈ start / shop_view / product_view /
        -- deposit_start. Order/payment/delivery events already have their own
        -- timestamps and are NOT duplicated here.
        CREATE TABLE IF NOT EXISTS events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id       INTEGER NOT NULL,
            type        TEXT NOT NULL,
            product_id  INTEGER,
            created_at  INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_events_type_time ON events(type, created_at);
        CREATE INDEX IF NOT EXISTS idx_events_time ON events(created_at);

        -- In-app support tickets. A buyer raises one against a specific order
        -- (from the delivery screen or order history); it surfaces in the admin
        -- dashboard's Tickets queue so messages are never missed in Telegram.
        -- status ∈ open / answered / closed. admin_reply holds the latest reply
        -- (also DM'd to the buyer).
        CREATE TABLE IF NOT EXISTS tickets (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            order_id    INTEGER NOT NULL,
            message     TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'open',
            admin_reply TEXT,
            created_at  INTEGER NOT NULL,
            updated_at  INTEGER NOT NULL,
            replied_at  INTEGER,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (order_id) REFERENCES orders(id)
        );
        CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_tickets_user ON tickets(user_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS api_keys (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            key_hash        TEXT NOT NULL UNIQUE,
            key_prefix      TEXT NOT NULL,
            created_at      INTEGER NOT NULL,
            revoked_at      INTEGER,
            last_used_at    INTEGER,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_api_keys_active_user
            ON api_keys(user_id) WHERE revoked_at IS NULL;
        CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash);

        CREATE TABLE IF NOT EXISTS api_orders (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            api_key_id      INTEGER NOT NULL,
            user_id         INTEGER NOT NULL,
            order_id        INTEGER NOT NULL UNIQUE,
            idempotency_key TEXT NOT NULL,
            created_at      INTEGER NOT NULL,
            FOREIGN KEY (api_key_id) REFERENCES api_keys(id),
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (order_id) REFERENCES orders(id),
            UNIQUE (api_key_id, idempotency_key)
        );
        CREATE INDEX IF NOT EXISTS idx_api_orders_user
            ON api_orders(user_id, created_at DESC);
    """)
    await db.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
        ("referral_feature_started_at", str(int(time.time()))),
    )


@asynccontextmanager
async def get_db() -> AsyncIterator[aiosqlite.Connection]:
    db = await aiosqlite.connect(DB_PATH)
    try:
        await db.execute("PRAGMA foreign_keys=ON;")
        # Without an explicit busy_timeout SQLite's default is 0, so a writer
        # that finds the single write-lock held returns SQLITE_BUSY *immediately*
        # instead of waiting. The bot poller, the aiogram handlers, and the
        # FastAPI admin panel all write this same file (run.py runs them in one
        # loop, and the docs also support split processes), so money-path
        # BEGIN IMMEDIATE transactions could otherwise fail mid-flight with
        # "database is locked". Wait up to 5s for the lock instead. (review H3)
        await db.execute("PRAGMA busy_timeout=5000;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        db.row_factory = aiosqlite.Row
        yield db
    finally:
        await db.close()


# ---------- Settings ----------

async def get_setting(key: str, default: str | None = None) -> str | None:
    async with get_db() as db:
        cur = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = await cur.fetchone()
        return str(row["value"]) if row else default


async def set_setting(key: str, value: str) -> None:
    async with get_db() as db:
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
        await db.commit()


# ---------- Reseller API keys ----------

async def get_active_api_key_for_user(user_id: int) -> Optional[aiosqlite.Row]:
    async with get_db() as db:
        cur = await db.execute(
            "SELECT * FROM api_keys WHERE user_id = ? AND revoked_at IS NULL "
            "ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        )
        return await cur.fetchone()


async def replace_active_api_key(
    user_id: int,
    *,
    key_hash: str,
    key_prefix: str,
) -> aiosqlite.Row:
    now = int(time.time())
    async with get_db() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            await db.execute(
                "UPDATE api_keys SET revoked_at = ? "
                "WHERE user_id = ? AND revoked_at IS NULL",
                (now, user_id),
            )
            cur = await db.execute(
                "INSERT INTO api_keys (user_id, key_hash, key_prefix, created_at) "
                "VALUES (?, ?, ?, ?)",
                (user_id, key_hash, key_prefix, now),
            )
            key_id = cur.lastrowid
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        cur = await db.execute("SELECT * FROM api_keys WHERE id = ?", (key_id,))
        return await cur.fetchone()


async def get_active_api_key_by_hash(key_hash: str) -> Optional[aiosqlite.Row]:
    async with get_db() as db:
        cur = await db.execute(
            "SELECT k.*, u.tg_id AS user_tg_id, u.username AS user_username, "
            "u.first_name AS user_first_name, u.balance AS user_balance, "
            "COALESCE(u.blocked, 0) AS user_blocked "
            "FROM api_keys k JOIN users u ON u.id = k.user_id "
            "WHERE k.key_hash = ? AND k.revoked_at IS NULL",
            (key_hash,),
        )
        return await cur.fetchone()


async def touch_api_key(api_key_id: int) -> None:
    async with get_db() as db:
        await db.execute(
            "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
            (int(time.time()), api_key_id),
        )
        await db.commit()


async def api_order_by_idempotency(
    api_key_id: int,
    idempotency_key: str,
) -> Optional[aiosqlite.Row]:
    async with get_db() as db:
        cur = await db.execute(
            "SELECT * FROM api_orders WHERE api_key_id = ? AND idempotency_key = ?",
            (api_key_id, idempotency_key),
        )
        return await cur.fetchone()


async def record_api_order(
    *,
    api_key_id: int,
    user_id: int,
    order_id: int,
    idempotency_key: str,
) -> None:
    async with get_db() as db:
        await db.execute(
            "INSERT INTO api_orders "
            "(api_key_id, user_id, order_id, idempotency_key, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (api_key_id, user_id, order_id, idempotency_key, int(time.time())),
        )
        await db.commit()


# ---------- Users ----------

async def get_or_create_user(tg_id: int, username: str | None, first_name: str | None,
                             referrer_tg_id: int | None = None) -> aiosqlite.Row:
    async with get_db() as db:
        cur = await db.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,))
        row = await cur.fetchone()
        if row:
            await db.execute(
                "UPDATE users SET username = ?, first_name = ? WHERE id = ?",
                (username, first_name, row["id"]),
            )
            await db.commit()
            return row

        ref_id = None
        if referrer_tg_id and referrer_tg_id != tg_id:
            cur = await db.execute("SELECT id FROM users WHERE tg_id = ?", (referrer_tg_id,))
            r = await cur.fetchone()
            if r:
                ref_id = r["id"]

        now = int(time.time())
        cur = await db.execute(
            "INSERT INTO users (tg_id, username, first_name, balance, referrer_id, created_at) "
            "VALUES (?, ?, ?, 0, ?, ?)",
            (tg_id, username, first_name, ref_id, now),
        )
        await db.commit()
        cur = await db.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,))
        return await cur.fetchone()


async def get_user_by_tg(tg_id: int) -> Optional[aiosqlite.Row]:
    async with get_db() as db:
        cur = await db.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,))
        return await cur.fetchone()


async def get_user_by_username(username: str) -> Optional[aiosqlite.Row]:
    lookup = (username or "").strip().lstrip("@").lower()
    if not lookup:
        return None
    async with get_db() as db:
        cur = await db.execute(
            "SELECT * FROM users WHERE LOWER(username) = ?", (lookup,),
        )
        return await cur.fetchone()


async def delivered_orders_for_tg(tg_id: int) -> list[aiosqlite.Row]:
    """Delivered orders for refund estimates (no stock contents)."""
    async with get_db() as db:
        cur = await db.execute(
            "SELECT o.id, o.product_id, o.quantity, o.unit_price, o.subtotal, "
            "o.discount, o.payable, o.delivered_at, "
            "p.name AS product_name, p.emoji AS product_emoji, "
            "COALESCE(p.warranty_days, 0) AS warranty_days "
            "FROM orders o "
            "JOIN products p ON p.id = o.product_id "
            "JOIN users u ON u.id = o.user_id "
            "WHERE u.tg_id = ? AND o.status = 'delivered' AND o.delivered_at IS NOT NULL "
            "ORDER BY o.delivered_at DESC",
            (int(tg_id),),
        )
        return await cur.fetchall()


async def get_user(user_id: int) -> Optional[aiosqlite.Row]:
    async with get_db() as db:
        cur = await db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        return await cur.fetchone()


async def set_user_language(tg_id: int, language: str) -> bool:
    """Persist a buyer's chosen UI language. Returns True if a row was updated."""
    async with get_db() as db:
        cur = await db.execute(
            "UPDATE users SET language = ? WHERE tg_id = ?", (language, tg_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def add_balance(user_id: int, amount: float) -> float:
    """Credit a wallet. `amount` is rounded to MONEY_DP. A non-positive or
    non-finite amount is a caller bug (this is the credit/refund primitive) and
    is treated as a no-op rather than silently *reducing* a balance — the only
    way to take money out is try_debit_balance / adjust_user_balance."""
    amount = round(float(amount), MONEY_DP)
    if not math.isfinite(amount) or amount <= 0:
        log.warning("add_balance ignored non-positive/non-finite amount %r for user %s",
                    amount, user_id)
        cur = await get_balance(user_id)
        return cur
    async with get_db() as db:
        await db.execute("UPDATE users SET balance = ROUND(balance + ?, ?) WHERE id = ?",
                         (amount, MONEY_DP, user_id))
        await db.commit()
        cur = await db.execute("SELECT balance FROM users WHERE id = ?", (user_id,))
        row = await cur.fetchone()
        return float(row["balance"])


async def get_balance(user_id: int) -> float:
    async with get_db() as db:
        cur = await db.execute("SELECT balance FROM users WHERE id = ?", (user_id,))
        row = await cur.fetchone()
        return float(row["balance"]) if row else 0.0


async def try_debit_balance(user_id: int, amount: float) -> bool:
    """Atomically debit a wallet iff the balance covers it. Returns True on
    success. A negative/non-finite amount is REJECTED (returns False): with a
    negative amount the `balance - amount` arithmetic would *credit* the wallet
    and the `balance >= amount` guard would always pass — the core of the
    negative-quantity exploit. (review C2)"""
    amount = round(float(amount), MONEY_DP)
    if not math.isfinite(amount) or amount < 0:
        return False
    async with get_db() as db:
        cur = await db.execute(
            "UPDATE users SET balance = ROUND(balance - ?, ?) WHERE id = ? AND balance >= ?",
            (amount, MONEY_DP, user_id, amount),
        )
        await db.commit()
        return cur.rowcount > 0


async def credit_topup_once(payment_id: int, user_id: int, amount: float) -> bool:
    """Atomically credit a confirmed wallet top-up exactly once.

    Reads the payment's `credited` flag and, only if it is still 0, credits the
    wallet and sets credited=1 in the SAME transaction. Returns True iff this
    call performed the credit. This closes the deposit-confirm crash window
    (review H6): confirm_payment and the wallet credit are no longer two
    independent commits that a crash could split, and the reconciliation sweep
    can safely re-run this for any confirmed-but-uncredited top-up without
    double-crediting."""
    amount = round(float(amount), MONEY_DP)
    if not math.isfinite(amount) or amount <= 0:
        return False
    async with get_db() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            cur = await db.execute(
                "SELECT credited, user_id FROM payments WHERE id = ?", (payment_id,))
            row = await cur.fetchone()
            if not row or int(row["credited"]):
                await db.rollback()
                return False
            await db.execute(
                "UPDATE users SET balance = ROUND(balance + ?, ?) WHERE id = ?",
                (amount, MONEY_DP, user_id),
            )
            await db.execute(
                "UPDATE payments SET credited = 1 WHERE id = ?", (payment_id,))
            await db.commit()
            return True
        except Exception:
            await db.rollback()
            raise


async def list_users(
    limit: int = 200,
    search: str | None = None,
) -> list[aiosqlite.Row]:
    """List users, optionally filtered by tg_id (full or partial) or @username
    (case-insensitive partial). Used by the admin /users page search box."""
    async with get_db() as db:
        q = (search or "").strip().lstrip("@")
        if not q:
            cur = await db.execute(
                "SELECT * FROM users ORDER BY created_at DESC LIMIT ?", (limit,)
            )
            return await cur.fetchall()

        like = f"%{q}%"
        if q.isdigit():
            # Match exact tg_id, then partial tg_id, then username containing the digits.
            cur = await db.execute(
                """
                SELECT * FROM users
                WHERE tg_id = ?
                   OR CAST(tg_id AS TEXT) LIKE ?
                   OR username LIKE ? COLLATE NOCASE
                ORDER BY (tg_id = ?) DESC, created_at DESC
                LIMIT ?
                """,
                (int(q), like, like, int(q), limit),
            )
        else:
            cur = await db.execute(
                """
                SELECT * FROM users
                WHERE username LIKE ? COLLATE NOCASE
                   OR first_name LIKE ? COLLATE NOCASE
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (like, like, limit),
            )
        return await cur.fetchall()


async def is_user_blocked(user_id: int) -> bool:
    async with get_db() as db:
        cur = await db.execute("SELECT blocked FROM users WHERE id = ?", (user_id,))
        row = await cur.fetchone()
        return bool(row and int(row["blocked"] or 0))


async def is_user_blocked_by_tg(tg_id: int) -> bool:
    async with get_db() as db:
        cur = await db.execute("SELECT blocked FROM users WHERE tg_id = ?", (tg_id,))
        row = await cur.fetchone()
        return bool(row and int(row["blocked"] or 0))


async def set_user_blocked(
    user_id: int,
    blocked: bool,
    *,
    admin_tg_ids: list[int] | None = None,
) -> bool:
    async with get_db() as db:
        cur = await db.execute("SELECT tg_id FROM users WHERE id = ?", (user_id,))
        row = await cur.fetchone()
        if not row:
            raise ValueError(f"User {user_id} not found")
        if blocked and int(row["tg_id"]) in set(admin_tg_ids or []):
            raise ValueError("Cannot block an admin user")
        cur = await db.execute(
            "UPDATE users SET blocked = ? WHERE id = ?",
            (1 if blocked else 0, user_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def adjust_user_balance(user_id: int, delta: float, *, note: str = "") -> tuple[float, float]:
    """
    Add or subtract balance (delta may be negative). Returns (before, after).
    Logs the change in balance_adjustments.
    """
    delta = float(delta)
    # Authoritative guard: a non-finite delta (NaN/Inf) must never reach the
    # balance — it would persist an infinite/garbage wallet. (review: NaN/Inf)
    if not math.isfinite(delta):
        raise ValueError("delta must be finite")
    delta = round(delta, 4)
    async with get_db() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            cur = await db.execute("SELECT balance FROM users WHERE id = ?", (user_id,))
            row = await cur.fetchone()
            if not row:
                await db.rollback()
                raise ValueError(f"User {user_id} not found")
            before = float(row["balance"])
            after = round(max(0.0, before + delta), 4)
            # Log the REALISED delta (after - before), not the requested one.
            # When a subtraction is clamped at zero (e.g. subtract 10 from a 3
            # balance) the requested delta (-10) would not equal after-before
            # (-3), leaving the ledger un-reconcilable. (review M-low)
            realised = round(after - before, 4)
            await db.execute("UPDATE users SET balance = ? WHERE id = ?", (after, user_id))
            await db.execute(
                "INSERT INTO balance_adjustments "
                "(user_id, delta, balance_before, balance_after, note, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, realised, before, after, note.strip(), int(time.time())),
            )
            await db.commit()
            return before, after
        except Exception:
            await db.rollback()
            raise


async def set_user_balance(user_id: int, amount: float, *, note: str = "") -> tuple[float, float]:
    """Set balance to an exact amount. Returns (before, after)."""
    raw = float(amount)
    if not math.isfinite(raw):
        raise ValueError("amount must be finite")
    amount = round(max(0.0, raw), 4)
    async with get_db() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            cur = await db.execute("SELECT balance FROM users WHERE id = ?", (user_id,))
            row = await cur.fetchone()
            if not row:
                await db.rollback()
                raise ValueError(f"User {user_id} not found")
            before = float(row["balance"])
            await db.execute("UPDATE users SET balance = ? WHERE id = ?", (amount, user_id))
            await db.execute(
                "INSERT INTO balance_adjustments "
                "(user_id, delta, balance_before, balance_after, note, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, amount - before, before, amount, note.strip(), int(time.time())),
            )
            await db.commit()
            return before, amount
        except Exception:
            await db.rollback()
            raise


# ---------- Products ----------

async def list_products(active_only: bool = True, offset: int = 0, limit: int = 1000) -> list[aiosqlite.Row]:
    async with get_db() as db:
        if active_only:
            cur = await db.execute(
                "SELECT * FROM products WHERE active = 1 "
                "ORDER BY sort_order ASC, id ASC LIMIT ? OFFSET ?",
                (limit, offset),
            )
        else:
            cur = await db.execute(
                "SELECT * FROM products ORDER BY sort_order ASC, id ASC LIMIT ? OFFSET ?",
                (limit, offset),
            )
        return await cur.fetchall()


async def reorder_product(product_id: int, direction: str) -> bool:
    """Move a product one step up or down in the shop/admin display order.

    Works on the full product list (active + inactive) so the admin's arranged
    sequence is stable regardless of which items are currently active. We read
    the current order, swap the target with its neighbour, then rewrite every
    row's sort_order to a dense 0..N-1 sequence — this self-heals any gaps or
    duplicate sort_order values left by older data. Returns False at the ends
    (can't move past the first/last position) or for an unknown id."""
    if direction not in ("up", "down"):
        return False
    async with get_db() as db:
        cur = await db.execute(
            "SELECT id FROM products ORDER BY sort_order ASC, id ASC"
        )
        ids = [int(r["id"]) for r in await cur.fetchall()]
        if product_id not in ids:
            return False
        i = ids.index(product_id)
        j = i - 1 if direction == "up" else i + 1
        if j < 0 or j >= len(ids):
            return False
        ids[i], ids[j] = ids[j], ids[i]
        await db.executemany(
            "UPDATE products SET sort_order = ? WHERE id = ?",
            [(pos, pid) for pos, pid in enumerate(ids)],
        )
        await db.commit()
        return True


async def count_active_products() -> int:
    async with get_db() as db:
        cur = await db.execute("SELECT COUNT(*) AS n FROM products WHERE active = 1")
        return int((await cur.fetchone())["n"])


async def get_product(product_id: int) -> Optional[aiosqlite.Row]:
    async with get_db() as db:
        cur = await db.execute("SELECT * FROM products WHERE id = ?", (product_id,))
        return await cur.fetchone()


async def stock_count(product_id: int) -> int:
    async with get_db() as db:
        cur = await db.execute(
            "SELECT COUNT(*) AS n FROM stock_items WHERE product_id = ? AND sold = 0",
            (product_id,),
        )
        return int((await cur.fetchone())["n"])


async def stock_counts(product_ids: list[int]) -> dict[int, int]:
    """Unsold-stock counts for many products in ONE query. Replaces a
    per-product stock_count loop (N+1) on the shop list and admin pages. (perf)"""
    if not product_ids:
        return {}
    placeholders = ",".join("?" * len(product_ids))
    async with get_db() as db:
        cur = await db.execute(
            f"SELECT product_id, COUNT(*) AS n FROM stock_items "
            f"WHERE product_id IN ({placeholders}) AND sold = 0 GROUP BY product_id",
            tuple(product_ids),
        )
        counts = {int(r["product_id"]): int(r["n"]) for r in await cur.fetchall()}
    return {pid: counts.get(pid, 0) for pid in product_ids}


async def sold_counts(product_ids: list[int]) -> dict[int, int]:
    """Sold-stock counts for many products in ONE query. (perf)"""
    if not product_ids:
        return {}
    placeholders = ",".join("?" * len(product_ids))
    async with get_db() as db:
        cur = await db.execute(
            f"SELECT product_id, COUNT(*) AS n FROM stock_items "
            f"WHERE product_id IN ({placeholders}) AND sold = 1 GROUP BY product_id",
            tuple(product_ids),
        )
        counts = {int(r["product_id"]): int(r["n"]) for r in await cur.fetchall()}
    return {pid: counts.get(pid, 0) for pid in product_ids}


async def create_product(name: str, emoji: str, description: str, base_price: float,
                         features: str = "", unit_label: str = "code",
                         icon_custom_emoji_id: str = "", brand: str = "",
                         delivery_instructions: str = "",
                         warranty_days: int = 0) -> int:
    async with get_db() as db:
        now = int(time.time())
        # New products land at the END of the display order.
        cur = await db.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM products")
        next_order = int((await cur.fetchone())["n"])
        cur = await db.execute(
            "INSERT INTO products (name, emoji, description, features, unit_label, "
            "base_price, active, created_at, icon_custom_emoji_id, brand, "
            "delivery_instructions, warranty_days, sort_order) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)",
            (name, emoji, description, features, unit_label, base_price, now,
             icon_custom_emoji_id.strip(), brand.strip().lower(), delivery_instructions,
             max(0, int(warranty_days or 0)), next_order),
        )
        await db.commit()
        return cur.lastrowid


_PRODUCT_UPDATABLE = frozenset({
    "name", "emoji", "description", "features", "unit_label",
    "base_price", "active", "icon_custom_emoji_id", "brand",
    "delivery_instructions", "warranty_days",
})


async def update_product(product_id: int, **fields) -> None:
    # Whitelist column names — keys are interpolated into SQL.
    fields = {k: v for k, v in fields.items() if k in _PRODUCT_UPDATABLE}
    if "warranty_days" in fields:
        fields["warranty_days"] = max(0, int(fields["warranty_days"] or 0))
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields.keys())
    async with get_db() as db:
        await db.execute(f"UPDATE products SET {cols} WHERE id = ?",
                         (*fields.values(), product_id))
        await db.commit()


async def delete_product(product_id: int) -> str:
    """
    Hard-delete a product, regardless of order history.

    Cascade:
      - stock_items: removed via the existing ON DELETE CASCADE on the FK
      - orders for this product: explicitly DELETEd (the FK has no CASCADE)
      - payments that pointed at those orders:
          * pending → cancelled (frees the (coin, network, amount) slot in
            the partial UNIQUE index so future deposits can reuse it; a late
            confirmation also lands cleanly as a wallet top-up)
          * other statuses → order_id set NULL so the payment audit row
            survives without a dangling FK
    """
    async with get_db() as db:
        # One transaction so the payment-rewrites, order delete, and product
        # delete are all-or-nothing — an interruption can't leave payment rows
        # pointing at a deleted order. (review L)
        await db.execute("BEGIN IMMEDIATE")
        try:
            await db.execute(
                "UPDATE payments SET status = 'cancelled' "
                "WHERE status = 'pending' AND order_id IN "
                "(SELECT id FROM orders WHERE product_id = ?)",
                (product_id,),
            )
            await db.execute(
                "UPDATE payments SET order_id = NULL "
                "WHERE order_id IN (SELECT id FROM orders WHERE product_id = ?)",
                (product_id,),
            )
            await db.execute("DELETE FROM orders WHERE product_id = ?", (product_id,))
            cur = await db.execute("DELETE FROM products WHERE id = ?", (product_id,))
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        # "deleted" only when a product row actually went away, so callers can
        # tell a real delete from a no-op on a missing id.
        return "deleted" if cur.rowcount > 0 else "noop"


async def get_pricing_tiers(product_id: int) -> list[aiosqlite.Row]:
    async with get_db() as db:
        cur = await db.execute(
            "SELECT * FROM pricing_tiers WHERE product_id = ? ORDER BY min_qty",
            (product_id,),
        )
        return await cur.fetchall()


async def pricing_tiers_for_products(product_ids: list[int]) -> dict[int, list[aiosqlite.Row]]:
    """All pricing tiers for many products in ONE query, grouped by product.
    Replaces a per-product get_pricing_tiers loop (N+1). (perf)"""
    if not product_ids:
        return {}
    placeholders = ",".join("?" * len(product_ids))
    async with get_db() as db:
        cur = await db.execute(
            f"SELECT * FROM pricing_tiers WHERE product_id IN ({placeholders}) "
            f"ORDER BY product_id, min_qty",
            tuple(product_ids),
        )
        rows = await cur.fetchall()
    out: dict[int, list[aiosqlite.Row]] = {pid: [] for pid in product_ids}
    for r in rows:
        out.setdefault(int(r["product_id"]), []).append(r)
    return out


async def set_pricing_tier(product_id: int, min_qty: int, unit_price: float) -> None:
    """Upsert a bulk tier: buying >= `min_qty` units charges `unit_price` USDT
    per unit. Replaces the old percentage model — discount_pct is stored as 0
    (the column is NOT NULL) and services.pricing derives any % purely for
    display. Re-adding a tier with the same min_qty overwrites it."""
    async with get_db() as db:
        await db.execute(
            "DELETE FROM pricing_tiers WHERE product_id = ? AND min_qty = ?",
            (product_id, min_qty),
        )
        await db.execute(
            "INSERT INTO pricing_tiers (product_id, min_qty, discount_pct, unit_price) "
            "VALUES (?, ?, 0, ?)",
            (product_id, min_qty, unit_price),
        )
        await db.commit()


async def delete_pricing_tier(product_id: int, min_qty: int) -> bool:
    """Remove one bulk tier. Returns True if a row was deleted."""
    async with get_db() as db:
        cur = await db.execute(
            "DELETE FROM pricing_tiers WHERE product_id = ? AND min_qty = ?",
            (product_id, min_qty),
        )
        await db.commit()
        return cur.rowcount > 0


# ---------- Stock ----------

async def add_stock_items(
    product_id: int, contents: Iterable[str], cost: Optional[float] = None
) -> int:
    """Insert stock codes. `cost` is the per-item amount the owner paid for
    this batch and is applied to every code in it; None leaves the rows
    untracked (excluded from profit)."""
    cleaned = [c.strip() for c in contents if c and c.strip()]
    if not cleaned:
        return 0
    async with get_db() as db:
        # Dedupe against codes already unsold for this product AND within the
        # pasted batch, so the same key can't become two stock rows that two
        # different orders each claim — i.e. one key sold to two buyers. (review M-low)
        cur = await db.execute(
            "SELECT content FROM stock_items WHERE product_id = ? AND sold = 0",
            (product_id,),
        )
        existing = {str(r["content"]) for r in await cur.fetchall()}
        items: list[tuple[int, str, Optional[float]]] = []
        seen_in_batch: set[str] = set()
        for c in cleaned:
            if c in existing or c in seen_in_batch:
                continue
            seen_in_batch.add(c)
            items.append((product_id, c, cost))
        if not items:
            return 0
        await db.executemany(
            "INSERT INTO stock_items (product_id, content, cost) VALUES (?, ?, ?)",
            items,
        )
        await db.commit()
        return len(items)


async def list_available_stock(product_id: int, limit: int = 500) -> list[aiosqlite.Row]:
    async with get_db() as db:
        cur = await db.execute(
            "SELECT id, content, cost, sold, sold_at, order_id FROM stock_items "
            "WHERE product_id = ? AND sold = 0 ORDER BY id ASC LIMIT ?",
            (product_id, limit),
        )
        return await cur.fetchall()


async def available_stock_for_products(
    product_ids: list[int], per_limit: int = 200
) -> dict[int, list[aiosqlite.Row]]:
    """Up to `per_limit` unsold stock rows for many products in ONE query
    (window function), grouped by product. Replaces a per-product
    list_available_stock loop on the admin products page. (perf)"""
    if not product_ids:
        return {}
    placeholders = ",".join("?" * len(product_ids))
    async with get_db() as db:
        cur = await db.execute(
            f"SELECT id, content, cost, sold, sold_at, order_id, product_id FROM ("
            f"  SELECT id, content, cost, sold, sold_at, order_id, product_id, "
            f"    ROW_NUMBER() OVER (PARTITION BY product_id ORDER BY id ASC) AS rn "
            f"  FROM stock_items WHERE product_id IN ({placeholders}) AND sold = 0"
            f") WHERE rn <= ?",
            (*product_ids, per_limit),
        )
        rows = await cur.fetchall()
    out: dict[int, list[aiosqlite.Row]] = {pid: [] for pid in product_ids}
    for r in rows:
        out.setdefault(int(r["product_id"]), []).append(r)
    return out


async def count_sold_stock(product_id: int) -> int:
    async with get_db() as db:
        cur = await db.execute(
            "SELECT COUNT(*) AS n FROM stock_items WHERE product_id = ? AND sold = 1",
            (product_id,),
        )
        return int((await cur.fetchone())["n"])


async def delete_stock_item(item_id: int, product_id: int) -> bool:
    """Remove one unsold stock row. Returns True if deleted."""
    async with get_db() as db:
        cur = await db.execute(
            "DELETE FROM stock_items WHERE id = ? AND product_id = ? AND sold = 0",
            (item_id, product_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def delete_available_stock(product_id: int, item_ids: list[int]) -> int:
    """Remove selected unsold stock rows. Returns number deleted."""
    if not item_ids:
        return 0
    placeholders = ",".join("?" * len(item_ids))
    async with get_db() as db:
        cur = await db.execute(
            f"DELETE FROM stock_items WHERE product_id = ? AND sold = 0 "
            f"AND id IN ({placeholders})",
            (product_id, *item_ids),
        )
        await db.commit()
        return cur.rowcount


async def delivered_contents_by_orders(order_ids: list[int]) -> dict[int, list[str]]:
    """Map order_id -> list of stock content strings that were sold for that order."""
    if not order_ids:
        return {}
    placeholders = ",".join("?" * len(order_ids))
    async with get_db() as db:
        cur = await db.execute(
            f"SELECT order_id, content FROM stock_items "
            f"WHERE sold = 1 AND order_id IS NOT NULL "
            f"AND order_id IN ({placeholders}) "
            f"ORDER BY order_id ASC, id ASC",
            order_ids,
        )
        rows = await cur.fetchall()
    out: dict[int, list[str]] = {}
    for row in rows:
        oid = int(row["order_id"])
        out.setdefault(oid, []).append(str(row["content"]))
    return out


async def clear_stock_cost_for_order(order_id: int) -> int:
    """Wipe the recorded cost on stock sold for an order (set it to NULL /
    untracked). Used for admin test orders so the codes they consume never
    carry a cost that could be counted anywhere. Returns rows affected."""
    async with get_db() as db:
        cur = await db.execute(
            "UPDATE stock_items SET cost = NULL WHERE order_id = ?",
            (order_id,),
        )
        await db.commit()
        return cur.rowcount


async def deactivate_if_sold_out(product_id: int) -> bool:
    """Auto-hide a product that just sold out: if it has no unsold stock left
    and is still active, flip active -> 0 (NOT delete). Atomic. Returns True iff
    this call performed the deactivation. Reactivation stays manual — the admin
    re-enables it from the dashboard once restocked. (feature: sold-out auto-deactivate)"""
    async with get_db() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            cur = await db.execute(
                "SELECT COUNT(*) AS n FROM stock_items WHERE product_id = ? AND sold = 0",
                (product_id,),
            )
            remaining = int((await cur.fetchone())["n"])
            if remaining > 0:
                await db.rollback()
                return False
            cur = await db.execute(
                "UPDATE products SET active = 0 WHERE id = ? AND active = 1",
                (product_id,),
            )
            await db.commit()
            return cur.rowcount > 0
        except Exception:
            await db.rollback()
            raise


async def claimed_contents_for_order(order_id: int) -> list[str]:
    """Stock contents already claimed for an order (sold=1, order_id set).
    Used to make delivery idempotent: a 'paid' order that already has claimed
    stock was delivered-but-not-marked (a crash window), so it must be finished
    without claiming MORE stock. (review H6)"""
    async with get_db() as db:
        cur = await db.execute(
            "SELECT content FROM stock_items WHERE order_id = ? AND sold = 1 ORDER BY id",
            (order_id,),
        )
        return [str(r["content"]) for r in await cur.fetchall()]


async def claim_stock_items(product_id: int, quantity: int, order_id: int) -> list[str]:
    # A non-positive quantity must never reach the query: SQLite treats a
    # negative LIMIT as "no limit", so `LIMIT -5` would claim the ENTIRE unsold
    # stock and the `len(rows) < quantity` guard below (len < -5) would never
    # trip — the stock-drain half of the negative-quantity exploit. (review C2)
    if quantity <= 0:
        return []
    async with get_db() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            cur = await db.execute(
                "SELECT id, content FROM stock_items "
                "WHERE product_id = ? AND sold = 0 "
                "ORDER BY id LIMIT ?",
                (product_id, quantity),
            )
            rows = await cur.fetchall()
            if len(rows) < quantity:
                await db.rollback()
                return []

            now = int(time.time())
            ids = [r["id"] for r in rows]
            await db.executemany(
                "UPDATE stock_items SET sold = 1, sold_at = ?, order_id = ? WHERE id = ?",
                [(now, order_id, sid) for sid in ids],
            )
            await db.commit()
            return [r["content"] for r in rows]
        except Exception:
            await db.rollback()
            raise


# ---------- Coupons ----------

async def get_coupon(code: str) -> Optional[aiosqlite.Row]:
    async with get_db() as db:
        cur = await db.execute("SELECT * FROM coupons WHERE code = ? COLLATE NOCASE",
                               (code.strip(),))
        return await cur.fetchone()


async def list_coupons() -> list[aiosqlite.Row]:
    async with get_db() as db:
        cur = await db.execute("SELECT * FROM coupons ORDER BY created_at DESC")
        return await cur.fetchall()


async def create_coupon(code: str, kind: str, value: float, max_uses: int = 0,
                        expires_at: int | None = None) -> int:
    async with get_db() as db:
        now = int(time.time())
        cur = await db.execute(
            "INSERT INTO coupons (code, kind, value, max_uses, used_count, active, expires_at, created_at) "
            "VALUES (?, ?, ?, ?, 0, 1, ?, ?)",
            (code.strip().upper(), kind, value, max_uses, expires_at, now),
        )
        await db.commit()
        return cur.lastrowid


async def bump_coupon_uses(code: str) -> bool:
    """Atomically increment a coupon's used_count, but only while it is still
    under its max_uses cap (0 = unlimited). Returns True if it was bumped,
    False if the cap was already reached. The conditional WHERE makes the
    read-check-write a single atomic statement so concurrent orders can't
    push a limited coupon past its cap. (review M4)"""
    async with get_db() as db:
        cur = await db.execute(
            "UPDATE coupons SET used_count = used_count + 1 "
            "WHERE code = ? COLLATE NOCASE "
            "AND (max_uses = 0 OR used_count < max_uses)",
            (code,),
        )
        await db.commit()
        return cur.rowcount > 0


# ---------- Orders ----------

async def create_order(user_id: int, product_id: int, quantity: int,
                       unit_price: float, subtotal: float,
                       coupon_code: str | None, discount: float, payable: float,
                       status: str = "draft",
                       referral_discount: float = 0.0) -> int:
    async with get_db() as db:
        now = int(time.time())
        cur = await db.execute(
            "INSERT INTO orders (user_id, product_id, quantity, unit_price, subtotal, "
            "coupon_code, discount, referral_discount, payable, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, product_id, quantity, unit_price, subtotal,
             coupon_code, discount, referral_discount, payable, status, now),
        )
        await db.commit()
        return cur.lastrowid


async def checkout_attempt_stats(
    user_id: int,
    *,
    since_ts: int,
) -> dict[str, int]:
    """Count recent unpaid checkout attempts for buyer-side flood control.

    Paid and delivered orders are deliberately excluded so normal successful
    buyers are not punished for repeat purchases.
    """
    async with get_db() as db:
        cur = await db.execute(
            """
            SELECT COUNT(*) AS n, COALESCE(MIN(created_at), 0) AS oldest
            FROM orders
            WHERE user_id = ?
              AND created_at >= ?
              AND status IN ('draft', 'awaiting_payment', 'cancelled', 'expired', 'failed')
            """,
            (user_id, since_ts),
        )
        row = await cur.fetchone()
        return {
            "count": int((row["n"] if row else 0) or 0),
            "oldest": int((row["oldest"] if row else 0) or 0),
        }


async def referral_discounted_order_count(user_id: int) -> int:
    """Active/completed orders that already consumed a referral discount."""
    async with get_db() as db:
        cur = await db.execute(
            "SELECT COUNT(*) AS n FROM orders "
            "WHERE user_id = ? "
            "AND referral_discount > 0 "
            "AND status NOT IN ('cancelled', 'expired', 'failed')",
            (user_id,),
        )
        row = await cur.fetchone()
        return int(row["n"] or 0)


async def referral_stats(referrer_id: int) -> dict[str, int | float]:
    async with get_db() as db:
        cur = await db.execute(
            "SELECT COUNT(*) AS n FROM users WHERE referrer_id = ?",
            (referrer_id,),
        )
        referrals = int((await cur.fetchone())["n"] or 0)
        cur = await db.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS total "
            "FROM referral_rewards WHERE referrer_id = ?",
            (referrer_id,),
        )
        rewards = await cur.fetchone()
        rewarded = int(rewards["n"] or 0)
        earned = round(float(rewards["total"] or 0.0), MONEY_DP)
        return {
            "earned": earned,
            "referrals": referrals,
            "rewarded": rewarded,
            "pending": max(0, referrals - rewarded),
        }


async def create_referral_reward_once(
    *,
    referrer_id: int,
    referred_id: int,
    order_id: int,
    amount: float,
) -> bool:
    amount = round(float(amount), MONEY_DP)
    if not math.isfinite(amount) or amount <= 0:
        return False
    async with get_db() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            cur = await db.execute(
                "INSERT OR IGNORE INTO referral_rewards "
                "(referrer_id, referred_id, order_id, amount, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (referrer_id, referred_id, order_id, amount, int(time.time())),
            )
            if cur.rowcount == 0:
                await db.rollback()
                return False
            row = await (await db.execute(
                "SELECT balance FROM users WHERE id = ?", (referrer_id,)
            )).fetchone()
            if not row:
                await db.rollback()
                return False
            before = float(row["balance"])
            after = round(before + amount, MONEY_DP)
            await db.execute(
                "UPDATE users SET balance = ? WHERE id = ?",
                (after, referrer_id),
            )
            await db.execute(
                "INSERT INTO balance_adjustments "
                "(user_id, delta, balance_before, balance_after, note, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    referrer_id,
                    amount,
                    before,
                    after,
                    f"Referral reward for order #{order_id}",
                    int(time.time()),
                ),
            )
            await db.commit()
            return True
        except Exception:
            await db.rollback()
            raise


async def referral_reward_reconcile_candidates(
    *,
    started_at: int,
    min_subtotal: float,
) -> list[aiosqlite.Row]:
    async with get_db() as db:
        cur = await db.execute(
            """
            SELECT o.id, u.tg_id AS buyer_tg_id
            FROM orders o
            JOIN users u ON u.id = o.user_id
            LEFT JOIN referral_rewards rr_user ON rr_user.referred_id = o.user_id
            LEFT JOIN referral_rewards rr_order ON rr_order.order_id = o.id
            WHERE o.status = 'delivered'
              AND o.created_at >= ?
              AND u.created_at >= ?
              AND u.referrer_id IS NOT NULL
              AND o.subtotal >= ?
              AND rr_user.id IS NULL
              AND rr_order.id IS NULL
            ORDER BY COALESCE(o.delivered_at, o.created_at) ASC, o.id ASC
            """,
            (started_at, started_at, float(min_subtotal)),
        )
        return await cur.fetchall()


_ORDER_UPDATABLE = frozenset({"status", "payment_method"})


async def update_order(order_id: int, **fields) -> None:
    # Whitelist column names — keys are interpolated into SQL.
    fields = {k: v for k, v in fields.items() if k in _ORDER_UPDATABLE}
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields.keys())
    async with get_db() as db:
        await db.execute(f"UPDATE orders SET {cols} WHERE id = ?",
                         (*fields.values(), order_id))
        await db.commit()


async def get_order(order_id: int) -> Optional[aiosqlite.Row]:
    async with get_db() as db:
        cur = await db.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
        return await cur.fetchone()


async def mark_order_delivered(order_id: int) -> bool:
    """Move a 'paid' order to 'delivered'. Guarded so no caller can ever mark
    an unpaid order delivered. Returns True if the transition happened. (review M-low)

    The coupon's used_count is bumped in the SAME transaction as the delivered
    flip, so the count can never be lost if the process dies right after the
    transition (the reconcile sweep only revisits 'paid' orders, so a separate
    post-commit bump on a now-'delivered' order would be unrecoverable, letting
    a max_uses coupon be redeemed one extra time). Because the bump rides on the
    guarded paid→delivered UPDATE, it fires exactly once per order. (review:
    coupon-bump atomicity)"""
    async with get_db() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            cur = await db.execute(
                "UPDATE orders SET status = 'delivered', delivered_at = ? "
                "WHERE id = ? AND status = 'paid'",
                (int(time.time()), order_id),
            )
            if cur.rowcount == 0:
                await db.rollback()
                return False
            row = await (await db.execute(
                "SELECT coupon_code FROM orders WHERE id = ?", (order_id,)
            )).fetchone()
            code = row["coupon_code"] if row else None
            if code:
                # Same conditional cap-guard as bump_coupon_uses (review M4).
                await db.execute(
                    "UPDATE coupons SET used_count = used_count + 1 "
                    "WHERE code = ? COLLATE NOCASE "
                    "AND (max_uses = 0 OR used_count < max_uses)",
                    (code,),
                )
            await db.commit()
            return True
        except Exception:
            await db.rollback()
            raise


async def recent_orders(user_id: int, limit: int = 10) -> list[aiosqlite.Row]:
    # Also carries the product's brand + custom-icon so callers (e.g. the ticket
    # order-picker) can resolve the brand logo without a per-order get_product
    # round-trip. Harmless extra columns for text-only callers.
    async with get_db() as db:
        cur = await db.execute(
            "SELECT o.*, p.name AS product_name, p.emoji AS product_emoji, "
            "p.brand AS product_brand, p.icon_custom_emoji_id AS product_icon_emoji_id "
            "FROM orders o JOIN products p ON p.id = o.product_id "
            "WHERE o.user_id = ? ORDER BY o.created_at DESC LIMIT ?",
            (user_id, limit),
        )
        return await cur.fetchall()


async def user_has_orders(user_id: int) -> bool:
    """Cheap existence check (mirrors recent_orders' product join) for deciding
    whether to offer the order-linked ticket option."""
    async with get_db() as db:
        cur = await db.execute(
            "SELECT 1 FROM orders o JOIN products p ON p.id = o.product_id "
            "WHERE o.user_id = ? LIMIT 1",
            (user_id,),
        )
        return await cur.fetchone() is not None


async def user_total_spent(user_id: int) -> float:
    """Sum of payable on completed orders (paid + delivered)."""
    async with get_db() as db:
        cur = await db.execute(
            "SELECT COALESCE(SUM(payable), 0) AS total FROM orders "
            "WHERE user_id = ? AND status IN ('paid', 'delivered')",
            (user_id,),
        )
        row = await cur.fetchone()
        return float(row["total"] or 0)


async def all_orders(
    limit: int = 500,
    search: str | None = None,
    status: str | None = None,
    exclude_admin_ids: list[int] | None = None,
) -> list[aiosqlite.Row]:
    """List orders newest-first. With `search`, filters by order number,
    Telegram user id, or @username (used by the admin /orders search box).

    A purely numeric query matches an exact order number OR a (partial)
    Telegram user id — an exact order-number hit floats to the top. A
    non-numeric query matches @username (case-insensitive, partial).

    `status` restricts to one order status. `exclude_admin_ids` drops orders
    placed by admin accounts (by Telegram id) so the owner's own test orders
    stay out of the history."""
    base = (
        "SELECT o.*, p.name AS product_name, p.emoji AS product_emoji, "
        "u.tg_id AS user_tg_id, u.username AS user_username "
        "FROM orders o "
        "JOIN products p ON p.id = o.product_id "
        "JOIN users u ON u.id = o.user_id "
    )
    q = (search or "").strip().lstrip("#").lstrip("@")
    conds: list[str] = []
    params: list = []
    numeric_order_param: list = []  # extra param for the (o.id = ?) sort key

    if q:
        like = f"%{q}%"
        if q.isdigit():
            conds.append("(o.id = ? OR CAST(u.tg_id AS TEXT) LIKE ?)")
            params += [int(q), like]
            order_by = "ORDER BY (o.id = ?) DESC, o.created_at DESC "
            numeric_order_param = [int(q)]
        else:
            conds.append("u.username LIKE ? COLLATE NOCASE")
            params.append(like)
            order_by = "ORDER BY o.created_at DESC "
    else:
        order_by = "ORDER BY o.created_at DESC "

    if status:
        conds.append("o.status = ?")
        params.append(status)

    if exclude_admin_ids:
        placeholders = ",".join("?" for _ in exclude_admin_ids)
        conds.append(f"u.tg_id NOT IN ({placeholders})")
        params += list(exclude_admin_ids)

    where = ("WHERE " + " AND ".join(conds) + " ") if conds else ""
    sql = base + where + order_by + "LIMIT ?"
    final_params = params + numeric_order_param + [limit]

    async with get_db() as db:
        cur = await db.execute(sql, final_params)
        return await cur.fetchall()


def _profit_period_clause(period: str, *, alias: str = "o") -> str:
    """SQL fragment: delivered_at in current Casablanca day or month bucket."""
    fmt = "%Y-%m" if period == "month" else "%Y-%m-%d"
    shift = _casa_sql_shift()
    return (
        f"{alias}.delivered_at IS NOT NULL "
        f"AND strftime('{fmt}', {alias}.delivered_at, 'unixepoch', {shift}) "
        f"= strftime('{fmt}', 'now', {shift})"
    )


def _profit_admin_clause(
    exclude_admin_ids: list[int] | None, *, alias: str = "o"
) -> tuple[str, list]:
    if not exclude_admin_ids:
        return "", []
    placeholders = ",".join("?" for _ in exclude_admin_ids)
    return (
        f" AND {alias}.user_id NOT IN ("
        f"SELECT id FROM users WHERE tg_id IN ({placeholders}))",
        list(exclude_admin_ids),
    )


_COGS_SUBQUERY = (
    "SELECT order_id, SUM(cost) AS cogs, SUM(cost IS NULL) AS n_null "
    "FROM stock_items WHERE sold = 1 AND order_id IS NOT NULL "
    "GROUP BY order_id"
)


def _profit_order_reason(
    *,
    tg_id: int,
    delivered_at: int | None,
    n_null: int,
    admin_tg: set[int],
) -> str:
    if tg_id in admin_tg:
        return "admin"
    if delivered_at is None:
        return "no_delivered_at"
    if n_null > 0:
        return "untracked_cost"
    return "included"


async def revenue_for_period(
    period: str, exclude_admin_ids: list[int] | None = None
) -> float:
    """Sum of payable for all delivered orders in the current Casa day/month."""
    period_sql = _profit_period_clause(period)
    admin_sql, admin_params = _profit_admin_clause(exclude_admin_ids)
    sql = (
        "SELECT COALESCE(SUM(o.payable), 0) FROM orders o "
        f"WHERE o.status = 'delivered' AND {period_sql}{admin_sql}"
    )
    async with get_db() as db:
        cur = await db.execute(sql, admin_params)
        row = await cur.fetchone()
        return float((row[0] if row else 0) or 0)


async def profit_breakdown_for_period(
    period: str, exclude_admin_ids: list[int] | None = None
) -> dict[str, float | int]:
    """Reconcile profit vs revenue for a Casa day/month (non-admin by default)."""
    period_sql = _profit_period_clause(period)
    admin_sql, admin_params = _profit_admin_clause(exclude_admin_ids)
    eligible_sql = (
        "SELECT COALESCE(SUM(o.payable), 0), COALESCE(SUM(c.cogs), 0), COUNT(*) "
        "FROM orders o "
        f"JOIN ({_COGS_SUBQUERY}) c ON c.order_id = o.id "
        "WHERE o.status = 'delivered' AND c.n_null = 0 "
        f"AND {period_sql}{admin_sql}"
    )
    period_revenue = await revenue_for_period(period, exclude_admin_ids)
    async with get_db() as db:
        cur = await db.execute(eligible_sql, admin_params)
        row = await cur.fetchone()
        eligible_revenue = float((row[0] if row else 0) or 0)
        cogs = float((row[1] if row else 0) or 0)
        eligible_order_count = int((row[2] if row else 0) or 0)

        cur = await db.execute(
            "SELECT COUNT(*) FROM orders o "
            f"WHERE o.status = 'delivered' AND {period_sql}{admin_sql}",
            admin_params,
        )
        period_order_count = int((await cur.fetchone())[0] or 0)

        loss_sql = (
            "SELECT COALESCE(SUM(CASE WHEN o.payable < c.cogs "
            "THEN c.cogs - o.payable ELSE 0 END), 0) "
            "FROM orders o "
            f"JOIN ({_COGS_SUBQUERY}) c ON c.order_id = o.id "
            "WHERE o.status = 'delivered' AND c.n_null = 0 "
            f"AND {period_sql}{admin_sql}"
        )
        cur = await db.execute(loss_sql, admin_params)
        order_losses = float((await cur.fetchone())[0] or 0)

    excluded_order_count = max(0, period_order_count - eligible_order_count)
    excluded_revenue = max(0.0, period_revenue - eligible_revenue)
    raw_profit = eligible_revenue - cogs
    displayed_profit = max(0.0, raw_profit)
    hidden_loss = abs(raw_profit) if raw_profit < 0 else 0.0
    return {
        "period": period,
        "period_revenue": round(period_revenue, 2),
        "eligible_revenue": round(eligible_revenue, 2),
        "cogs": round(cogs, 2),
        "raw_profit": round(raw_profit, 2),
        "displayed_profit": round(displayed_profit, 2),
        "excluded_revenue": round(excluded_revenue, 2),
        "hidden_loss": round(hidden_loss, 2),
        "order_losses": round(order_losses, 2),
        "eligible_order_count": eligible_order_count,
        "excluded_order_count": excluded_order_count,
    }


async def list_profit_order_rows(
    period: str,
    exclude_admin_ids: list[int] | None = None,
    limit: int = 500,
) -> list[dict]:
    """Delivered orders in period with per-order margin and inclusion reason."""
    period_sql = _profit_period_clause(period)
    admin_sql, admin_params = _profit_admin_clause(exclude_admin_ids)
    admin_tg = set(exclude_admin_ids or [])
    sql = (
        "SELECT o.id, o.payable, o.quantity, o.delivered_at, u.tg_id, "
        "p.name AS product_name, p.emoji AS product_emoji, "
        "COALESCE(c.cogs, 0) AS cogs, COALESCE(c.n_null, 1) AS n_null "
        "FROM orders o "
        "JOIN users u ON u.id = o.user_id "
        "JOIN products p ON p.id = o.product_id "
        f"LEFT JOIN ({_COGS_SUBQUERY}) c ON c.order_id = o.id "
        f"WHERE o.status = 'delivered' AND {period_sql}{admin_sql} "
        "ORDER BY o.delivered_at DESC LIMIT ?"
    )
    async with get_db() as db:
        cur = await db.execute(sql, [*admin_params, limit])
        rows = await cur.fetchall()
    out: list[dict] = []
    for r in rows:
        payable = float(r["payable"] or 0)
        cogs = float(r["cogs"] or 0)
        reason = _profit_order_reason(
            tg_id=int(r["tg_id"]),
            delivered_at=r["delivered_at"],
            n_null=int(r["n_null"] or 0),
            admin_tg=admin_tg,
        )
        out.append({
            "order_id": int(r["id"]),
            "product_name": r["product_name"],
            "product_emoji": r["product_emoji"] or "",
            "quantity": int(r["quantity"]),
            "payable": payable,
            "cogs": cogs,
            "margin": round(payable - cogs, 2),
            "delivered_at": int(r["delivered_at"] or 0),
            "reason": reason,
        })
    return out


async def profit_orders_audit(
    period: str,
    exclude_admin_ids: list[int] | None = None,
    limit: int = 10,
) -> list[dict]:
    """Delivered orders in period with margin + exclusion reason (CLI audit)."""
    rows = await list_profit_order_rows(
        period, exclude_admin_ids=exclude_admin_ids, limit=limit,
    )
    return [
        {
            "order_id": r["order_id"],
            "payable": r["payable"],
            "cogs": r["cogs"],
            "margin": r["margin"],
            "reason": r["reason"],
        }
        for r in rows
    ]


async def profit_for_period(
    period: str, exclude_admin_ids: list[int] | None = None
) -> float:
    """Net profit (revenue − COGS) for fully-costed delivered orders this Casa period."""
    b = await profit_breakdown_for_period(period, exclude_admin_ids)
    return float(b["raw_profit"])


# ---------- Support tickets ----------

TICKET_STATUSES = ("open", "answered", "closed")
_TICKET_REPLY_STATUSES = ("open", "answered")  # which states a reply may move


async def create_ticket(user_id: int, order_id: int, message: str) -> int:
    """Open a support ticket for an order. Returns the new ticket id."""
    async with get_db() as db:
        now = int(time.time())
        cur = await db.execute(
            "INSERT INTO tickets (user_id, order_id, message, status, created_at, updated_at) "
            "VALUES (?, ?, ?, 'open', ?, ?)",
            (user_id, order_id, message, now, now),
        )
        await db.commit()
        return cur.lastrowid


async def get_ticket(ticket_id: int) -> Optional[aiosqlite.Row]:
    async with get_db() as db:
        cur = await db.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
        return await cur.fetchone()


async def recent_tickets_for_user(user_id: int, limit: int = 10) -> list[aiosqlite.Row]:
    async with get_db() as db:
        cur = await db.execute(
            "SELECT * FROM tickets WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        )
        return await cur.fetchall()


async def count_open_tickets(exclude_admin_ids: list[int] | None = None) -> int:
    """Count tickets still awaiting attention (open or answered, not closed).
    Mirrors the dashboard's admin-exclusion so the owner's own test tickets
    don't inflate the queue counter."""
    sql = ("SELECT COUNT(*) AS n FROM tickets t "
           "JOIN users u ON u.id = t.user_id "
           "WHERE t.status IN ('open', 'answered')")
    params: list = []
    if exclude_admin_ids:
        ph = ",".join("?" for _ in exclude_admin_ids)
        sql += f" AND u.tg_id NOT IN ({ph})"
        params += list(exclude_admin_ids)
    async with get_db() as db:
        cur = await db.execute(sql, params)
        return int((await cur.fetchone())["n"])


async def count_active_tickets_for_user(user_id: int) -> int:
    """How many not-yet-closed tickets (open or answered) a user has. Used to
    rate-limit ticket creation so one buyer can't flood the queue."""
    async with get_db() as db:
        cur = await db.execute(
            "SELECT COUNT(*) AS n FROM tickets "
            "WHERE user_id = ? AND status IN ('open', 'answered')",
            (user_id,),
        )
        return int((await cur.fetchone())["n"])


async def all_tickets(
    limit: int = 500,
    search: str | None = None,
    status: str | None = None,
    exclude_admin_ids: list[int] | None = None,
) -> list[aiosqlite.Row]:
    """List tickets newest-first for the admin dashboard. Joins the buyer and
    the linked order/product. `search` matches ticket #, Telegram id, or
    @username; `status` filters to one status; `exclude_admin_ids` drops the
    owner's own test tickets (by Telegram id). Mirrors all_orders()."""
    base = (
        "SELECT t.*, u.tg_id AS user_tg_id, u.username AS user_username, "
        "o.quantity AS order_qty, o.payable AS order_payable, "
        "p.name AS product_name, p.emoji AS product_emoji "
        "FROM tickets t "
        "JOIN users u ON u.id = t.user_id "
        "LEFT JOIN orders o ON o.id = t.order_id "
        "LEFT JOIN products p ON p.id = o.product_id "
    )
    q = (search or "").strip().lstrip("#").lstrip("@")
    conds: list[str] = []
    params: list = []
    if q:
        like = f"%{q}%"
        if q.isdigit():
            conds.append("(t.id = ? OR CAST(u.tg_id AS TEXT) LIKE ?)")
            params += [int(q), like]
        else:
            conds.append("u.username LIKE ? COLLATE NOCASE")
            params.append(like)
    if status:
        conds.append("t.status = ?")
        params.append(status)
    if exclude_admin_ids:
        ph = ",".join("?" for _ in exclude_admin_ids)
        conds.append(f"u.tg_id NOT IN ({ph})")
        params += list(exclude_admin_ids)
    where = ("WHERE " + " AND ".join(conds) + " ") if conds else ""
    sql = base + where + "ORDER BY t.created_at DESC LIMIT ?"
    async with get_db() as db:
        cur = await db.execute(sql, params + [limit])
        return await cur.fetchall()


async def set_ticket_status(ticket_id: int, status: str) -> bool:
    """Set a ticket's status (open/answered/closed). Returns True if a row changed."""
    if status not in TICKET_STATUSES:
        return False
    async with get_db() as db:
        cur = await db.execute(
            "UPDATE tickets SET status = ?, updated_at = ? WHERE id = ?",
            (status, int(time.time()), ticket_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def reply_ticket(ticket_id: int, reply: str) -> Optional[aiosqlite.Row]:
    """Record an admin reply, move the ticket to 'answered', and return the
    (updated) ticket row so the caller can DM the buyer. None if not found."""
    async with get_db() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            cur = await db.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
            row = await cur.fetchone()
            if not row:
                await db.rollback()
                return None
            now = int(time.time())
            await db.execute(
                "UPDATE tickets SET admin_reply = ?, status = 'answered', "
                "updated_at = ?, replied_at = ? WHERE id = ?",
                (reply, now, now, ticket_id),
            )
            cur = await db.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
            updated = await cur.fetchone()
            await db.commit()
            return updated
        except Exception:
            await db.rollback()
            raise


# ---------- Time zone (Africa/Casablanca) ----------
#
# The admin dashboard shows all times in Casablanca local time, and analytics
# bucket boundaries (day/week/month) follow the Casablanca calendar. Casablanca
# is normally UTC+1 (UTC+0 during Ramadan); zoneinfo handles that automatically
# where the OS tz database is available (the Linux VPS), with a UTC+1 fallback
# for environments without tzdata (e.g. bare Windows dev).
try:
    from zoneinfo import ZoneInfo
    CASA_TZ = ZoneInfo("Africa/Casablanca")
except Exception:  # pragma: no cover - missing tzdata
    CASA_TZ = timezone(timedelta(hours=1))


def casa_now() -> datetime:
    """Timezone-aware 'now' in Africa/Casablanca."""
    return datetime.now(CASA_TZ)


def casa_format(ts, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Format a UTC unix timestamp as Africa/Casablanca local time (admin UI)."""
    try:
        return datetime.fromtimestamp(int(ts), CASA_TZ).strftime(fmt)
    except Exception:
        return str(ts)


def _casa_sql_shift() -> str:
    """SQLite strftime modifier for the CURRENT Casablanca UTC offset, e.g.
    "'+1 hours'" (or "'+0 hours'" during Ramadan). The offset is stable across
    a recent reporting window, so the current value is used for the whole
    window. Hardcoded literal built from a small int — injection-safe."""
    h = int(casa_now().utcoffset().total_seconds() // 3600)
    return f"'+{h} hours'"


# ---------- Analytics (Statistics tab) ----------
#
# All time-bucketing follows Casablanca local time (see CASA_TZ): SQL groups raw
# rows into DAY buckets via strftime('%Y-%m-%d', col, 'unixepoch', <casa shift>);
# Python then rolls days up into week/month buckets and zero-fills missing
# periods. Any SQL fragment f-string-interpolated below (table/column/agg/
# strftime literals + the casa shift) is a hardcoded constant — never user
# input — so it is injection-safe; all values are bound. Admin accounts are
# excluded via the same pattern as the dashboard.


async def log_event(tg_id: int, type: str, product_id: int | None = None) -> None:
    """Append a funnel/engagement event (start / shop_view / product_view /
    deposit_start). Cheap single INSERT, fully best-effort: it swallows its own
    errors so a caller can `await db.log_event(...)` on the bot's hot path and
    analytics can never break a real interaction."""
    try:
        async with get_db() as db:
            await db.execute(
                "INSERT INTO events (tg_id, type, product_id, created_at) VALUES (?, ?, ?, ?)",
                (int(tg_id), type, product_id, int(time.time())),
            )
            await db.commit()
    except Exception:
        log.warning("log_event(%s, %s) failed", tg_id, type)


def _window_days(period: str, count: int) -> int:
    if period == "month":
        return 31 * count + 5
    if period == "week":
        return 7 * count + 7
    return count + 1


def _buckets_for(period: str, count: int) -> tuple[list[str], dict[str, str]]:
    """Ordered bucket keys (oldest→newest) + key→display-label, in Casablanca time."""
    today = casa_now().date()
    keys: list[str] = []
    labels: dict[str, str] = {}
    if period == "day":
        for i in range(count - 1, -1, -1):
            d = today - timedelta(days=i)
            k = d.strftime("%Y-%m-%d")
            keys.append(k); labels[k] = d.strftime("%b %d")
    elif period == "week":
        monday = today - timedelta(days=today.weekday())
        for i in range(count - 1, -1, -1):
            ws = monday - timedelta(weeks=i)
            k = ws.strftime("%Y-%m-%d")
            keys.append(k); labels[k] = "wk " + ws.strftime("%b %d")
    else:  # month
        y, m = today.year, today.month
        pairs: list[tuple[int, int]] = []
        for i in range(count):
            mm, yy = m - i, y
            while mm <= 0:
                mm += 12; yy -= 1
            pairs.append((yy, mm))
        for yy, mm in reversed(pairs):
            k = f"{yy:04d}-{mm:02d}"
            keys.append(k); labels[k] = datetime(yy, mm, 1).strftime("%b %y")
    return keys, labels


def _day_to_bucket(day_str: str, period: str) -> str:
    if period == "day":
        return day_str
    d = datetime.strptime(day_str, "%Y-%m-%d").date()
    if period == "week":
        return (d - timedelta(days=d.weekday())).strftime("%Y-%m-%d")
    return d.strftime("%Y-%m")


def _admin_clause(exclude_admin_ids, column: str) -> tuple[str, list]:
    """Return (sql_fragment, params) excluding admin accounts. `column` is
    'tg_id' (events/users tables hold tg_id) or an internal-id column
    (orders/payments) resolved via a users sub-select."""
    if not exclude_admin_ids:
        return "", []
    ph = ",".join("?" for _ in exclude_admin_ids)
    if column == "tg_id":
        return f" AND tg_id NOT IN ({ph})", list(exclude_admin_ids)
    return (f" AND {column} NOT IN (SELECT id FROM users WHERE tg_id IN ({ph}))",
            list(exclude_admin_ids))


async def _series(*, table: str, ts_col: str, agg_sql: str, period: str, count: int,
                  extra_where: str = "", extra_params: tuple = (),
                  exclude_admin_ids=None, admin_via: str = "user_id") -> dict:
    """Generic day-bucketed time series rolled up to period buckets + zero-filled."""
    keys, labels = _buckets_for(period, count)
    win_start = int(time.time()) - _window_days(period, count) * 86400
    where = [f"{ts_col} >= ?"]
    params: list = [win_start]
    if extra_where:
        where.append(extra_where); params += list(extra_params)
    clause, cparams = _admin_clause(exclude_admin_ids, admin_via)
    shift = _casa_sql_shift()
    sql = (
        f"SELECT strftime('%Y-%m-%d', {ts_col}, 'unixepoch', {shift}) AS d, "
        f"{agg_sql} AS v FROM {table} WHERE " + " AND ".join(where) + clause + " GROUP BY d"
    )
    params += cparams
    async with get_db() as db:
        cur = await db.execute(sql, params)
        rows = await cur.fetchall()
    vals = {k: 0.0 for k in keys}
    for r in rows:
        d = r["d"]
        if not d:
            continue
        bk = _day_to_bucket(d, period)
        if bk in vals:
            vals[bk] += float(r["v"] or 0)
    return {"labels": [labels[k] for k in keys],
            "values": [round(vals[k], 2) for k in keys]}


async def new_users_series(period: str, count: int, exclude_admin_ids=None) -> dict:
    return await _series(table="users", ts_col="created_at", agg_sql="COUNT(*)",
                         period=period, count=count,
                         exclude_admin_ids=exclude_admin_ids, admin_via="tg_id")


async def events_series(period: str, count: int, event_type: str, exclude_admin_ids=None) -> dict:
    return await _series(table="events", ts_col="created_at", agg_sql="COUNT(*)",
                         period=period, count=count,
                         extra_where="type = ?", extra_params=(event_type,),
                         exclude_admin_ids=exclude_admin_ids, admin_via="tg_id")


async def orders_series(period: str, count: int, status: str, exclude_admin_ids=None) -> dict:
    return await _series(table="orders", ts_col="created_at", agg_sql="COUNT(*)",
                         period=period, count=count,
                         extra_where="status = ?", extra_params=(status,),
                         exclude_admin_ids=exclude_admin_ids, admin_via="user_id")


async def revenue_series(period: str, count: int, exclude_admin_ids=None) -> dict:
    return await _series(table="orders", ts_col="delivered_at",
                         agg_sql="COALESCE(SUM(payable),0)", period=period, count=count,
                         extra_where="status = 'delivered'",
                         exclude_admin_ids=exclude_admin_ids, admin_via="user_id")


async def deposits_series(period: str, count: int, exclude_admin_ids=None) -> dict:
    # A "deposit" is a wallet top-up: order_id IS NULL. Without that filter this
    # also summed crypto payments that SETTLED an order, double-counting money
    # that revenue_series already reports and conflating settlements with
    # wallet funding. (review: deposits_series double-count)
    return await _series(table="payments", ts_col="confirmed_at",
                         agg_sql="COALESCE(SUM(unique_amount),0)", period=period, count=count,
                         extra_where="status = 'confirmed' AND order_id IS NULL",
                         exclude_admin_ids=exclude_admin_ids, admin_via="user_id")


async def _scalar(sql: str, params) -> float:
    async with get_db() as db:
        cur = await db.execute(sql, params)
        row = await cur.fetchone()
        return float((row[0] if row else 0) or 0)


def _period_bounds(period: str) -> tuple[int, int]:
    """(current_period_start_unix, previous_period_start_unix) at Casablanca
    local midnight / week-start (Mon) / month-start."""
    now = casa_now()  # tz-aware
    midnight = {"hour": 0, "minute": 0, "second": 0, "microsecond": 0}
    if period == "day":
        cur = now.replace(**midnight)
        prev = cur - timedelta(days=1)
    elif period == "week":
        cur = (now - timedelta(days=now.weekday())).replace(**midnight)
        prev = cur - timedelta(weeks=1)
    else:
        cur = now.replace(day=1, **midnight)
        py, pm = (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)
        prev = cur.replace(year=py, month=pm)
    return int(cur.timestamp()), int(prev.timestamp())


async def kpi_summary(period: str, exclude_admin_ids=None) -> dict:
    """Headline KPIs: current-period value + previous-period value (for % delta),
    plus a few all-time totals. Mirrors the dashboard's admin exclusion."""
    cur_start, prev_start = _period_bounds(period)
    ev_excl, ev_p = _admin_clause(exclude_admin_ids, "tg_id")
    or_excl, or_p = _admin_clause(exclude_admin_ids, "user_id")
    us_excl, us_p = _admin_clause(exclude_admin_ids, "tg_id")

    async def pair(table, ts, agg, base_where, base_params, excl, ep):
        cur = await _scalar(
            f"SELECT {agg} FROM {table} WHERE {ts} >= ?{(' AND ' + base_where) if base_where else ''}{excl}",
            [cur_start, *base_params, *ep])
        prev = await _scalar(
            f"SELECT {agg} FROM {table} WHERE {ts} >= ? AND {ts} < ?{(' AND ' + base_where) if base_where else ''}{excl}",
            [prev_start, cur_start, *base_params, *ep])
        return {"current": round(cur, 2), "previous": round(prev, 2)}

    new_users = await pair("users", "created_at", "COUNT(*)", "", [], us_excl, us_p)
    revenue = await pair("orders", "delivered_at", "COALESCE(SUM(payable),0)",
                         "status='delivered'", [], or_excl, or_p)
    delivered = await pair("orders", "delivered_at", "COUNT(*)",
                           "status='delivered'", [], or_excl, or_p)
    starts = await pair("events", "created_at", "COUNT(*)", "type='start'", [], ev_excl, ev_p)

    total_users = await _scalar(
        f"SELECT COUNT(*) FROM users WHERE 1=1{us_excl}", list(us_p))
    total_revenue = await _scalar(
        f"SELECT COALESCE(SUM(payable),0) FROM orders WHERE status='delivered'{or_excl}", list(or_p))
    wallet_liability = await _scalar(
        f"SELECT COALESCE(SUM(balance),0) FROM users WHERE 1=1{us_excl}", list(us_p))
    return {
        "period": period,
        "new_users": new_users, "revenue": revenue,
        "delivered": delivered, "starts": starts,
        "total_users": int(total_users),
        "total_revenue": round(total_revenue, 2),
        "wallet_liability": round(wallet_liability, 2),
    }


async def funnel_counts(days: int = 30, exclude_admin_ids=None) -> dict:
    """Distinct-user counts at each funnel stage over the last `days`.
    Engagement stages (start/shop/product) come from the events table and so
    only reflect activity since event logging was deployed."""
    win = int(time.time()) - days * 86400
    ev_excl, ev_p = _admin_clause(exclude_admin_ids, "tg_id")
    or_excl, or_p = _admin_clause(exclude_admin_ids, "user_id")

    async def ev(t):
        return int(await _scalar(
            f"SELECT COUNT(DISTINCT tg_id) FROM events WHERE type=? AND created_at>=?{ev_excl}",
            [t, win, *ev_p]))

    async def orders_stage(statuses):
        ph = ",".join("?" for _ in statuses)
        return int(await _scalar(
            f"SELECT COUNT(DISTINCT user_id) FROM orders "
            f"WHERE status IN ({ph}) AND created_at>=?{or_excl}",
            [*statuses, win, *or_p]))

    return {
        "started": await ev("start"),
        "shop_view": await ev("shop_view"),
        "product_view": await ev("product_view"),
        "ordered": await orders_stage(("draft", "awaiting_payment", "paid", "delivered")),
        "checkout": await orders_stage(("awaiting_payment", "paid", "delivered")),
        "paid": await orders_stage(("paid", "delivered")),
        "delivered": await orders_stage(("delivered",)),
        "days": days,
    }


async def top_products(days: int = 30, limit: int = 8, exclude_admin_ids=None) -> list[aiosqlite.Row]:
    """Best-selling products (delivered) over the last `days`: units + revenue."""
    win = int(time.time()) - days * 86400
    excl, ep = _admin_clause(exclude_admin_ids, "o.user_id")
    async with get_db() as db:
        cur = await db.execute(
            "SELECT p.name AS name, p.emoji AS emoji, "
            "COALESCE(SUM(o.quantity),0) AS units, COALESCE(SUM(o.payable),0) AS revenue "
            "FROM orders o JOIN products p ON p.id = o.product_id "
            f"WHERE o.status='delivered' AND o.delivered_at >= ?{excl} "
            "GROUP BY o.product_id ORDER BY units DESC, revenue DESC LIMIT ?",
            [win, *ep, limit],
        )
        return await cur.fetchall()


async def payment_method_mix(days: int = 30, exclude_admin_ids=None) -> list[aiosqlite.Row]:
    """Paid/delivered orders grouped by payment method over the last `days`."""
    win = int(time.time()) - days * 86400
    excl, ep = _admin_clause(exclude_admin_ids, "user_id")
    async with get_db() as db:
        cur = await db.execute(
            "SELECT COALESCE(payment_method,'unknown') AS method, COUNT(*) AS n, "
            "COALESCE(SUM(payable),0) AS revenue FROM orders "
            f"WHERE status IN ('paid','delivered') AND created_at >= ?{excl} "
            "GROUP BY payment_method ORDER BY n DESC",
            [win, *ep],
        )
        return await cur.fetchall()


async def repeat_buyer_stats(exclude_admin_ids=None) -> dict:
    """All-time buyers vs repeat buyers (≥2 delivered orders)."""
    excl, ep = _admin_clause(exclude_admin_ids, "user_id")
    async with get_db() as db:
        cur = await db.execute(
            "SELECT c FROM (SELECT COUNT(*) AS c FROM orders "
            f"WHERE status='delivered'{excl} GROUP BY user_id)",
            list(ep),
        )
        counts = [int(r["c"]) for r in await cur.fetchall()]
    buyers = len(counts)
    repeat = sum(1 for c in counts if c >= 2)
    return {
        "buyers": buyers,
        "repeat": repeat,
        "repeat_rate": round(100.0 * repeat / buyers, 1) if buyers else 0.0,
    }


# ---------- Payments ----------

async def create_payment(*, user_id: int, order_id: int | None, method: str, network: str,
                         coin: str, base_amount: float,
                         note: str, address: str | None, binance_uid: str | None,
                         ttl_seconds: int) -> tuple[int, float]:
    """
    Insert a pending payment with a UNIQUE matching amount.

    Starts from the rounded base amount (e.g. $10.00) and, if a pending payment
    with that exact (coin, network, amount) already exists, bumps the amount by
    one cent and retries — so two users wanting to pay/deposit the same figure
    each get a distinct amount ($10.00, $10.01, …) that an incoming on-chain or
    Binance Pay deposit can be attributed to unambiguously. Without this the
    second user was simply refused (a DoS on common price points) and the whole
    amount-matching scheme had no real uniqueness guarantee. (review H4/H5)

    The cent step matches the 2-decimal "send exactly" amount shown to the user.
    `base_amount` keeps the original price; only `unique_amount` is bumped.

    Returns (payment_id, unique_amount).
    """
    now = int(time.time())
    base = round(float(base_amount), 2)
    candidate = base
    # 100 cents of headroom: covers up to 100 concurrent pending invoices at
    # the same base price before giving up.
    for _attempt in range(100):
        async with get_db() as db:
            try:
                cur = await db.execute(
                    "INSERT INTO payments (user_id, order_id, method, network, coin, "
                    "base_amount, unique_amount, note, address, binance_uid, "
                    "status, created_at, expires_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                    (user_id, order_id, method, network, coin, base,
                     candidate, note, address, binance_uid, now, now + ttl_seconds),
                )
                await db.commit()
                return cur.lastrowid, candidate
            except aiosqlite.IntegrityError:
                await db.rollback()
        candidate = round(candidate + PAYMENT_UNIQUE_BUMP, 2)
    raise RuntimeError(
        f"Too many pending {coin} payments near {base} on this network right now. "
        "Wait a moment or choose a slightly different amount."
    )


async def cancel_pending_payments_for_order(order_id: int) -> int:
    """When a user changes payment method, cancel any prior pending payments
    on the same order so we don't end up with two simultaneous reservations
    that could both fund and cause a double delivery."""
    async with get_db() as db:
        cur = await db.execute(
            "UPDATE payments SET status = 'cancelled' "
            "WHERE order_id = ? AND status = 'pending'",
            (order_id,),
        )
        await db.commit()
        return cur.rowcount


async def get_pending_payment_for_order_method(order_id: int, method: str) -> Optional[aiosqlite.Row]:
    async with get_db() as db:
        cur = await db.execute(
            "SELECT * FROM payments WHERE order_id = ? AND method = ? "
            "AND status = 'pending' ORDER BY created_at DESC LIMIT 1",
            (order_id, method),
        )
        return await cur.fetchone()


async def cancel_other_pending_payments_for_order(order_id: int, keep_payment_id: int) -> int:
    """Cancel pending sibling payments only after the replacement exists."""
    async with get_db() as db:
        cur = await db.execute(
            "UPDATE payments SET status = 'cancelled' "
            "WHERE order_id = ? AND status = 'pending' AND id != ?",
            (order_id, keep_payment_id),
        )
        await db.commit()
        return cur.rowcount


async def try_transition_order_status(order_id: int, from_statuses: tuple[str, ...],
                                       to_status: str,
                                       payment_method: str | None = None) -> bool:
    """
    Atomically transition the order's status only if it's currently in one of
    `from_statuses`. Returns True if the transition actually happened.

    This is the gate that prevents a second confirmed payment from
    re-delivering an already-paid order.
    """
    placeholders = ",".join("?" * len(from_statuses))
    async with get_db() as db:
        if payment_method is None:
            cur = await db.execute(
                f"UPDATE orders SET status = ? "
                f"WHERE id = ? AND status IN ({placeholders})",
                (to_status, order_id, *from_statuses),
            )
        else:
            cur = await db.execute(
                f"UPDATE orders SET status = ?, payment_method = ? "
                f"WHERE id = ? AND status IN ({placeholders})",
                (to_status, payment_method, order_id, *from_statuses),
            )
        await db.commit()
        return cur.rowcount > 0


async def cleanup_stale_drafts(older_than_seconds: int = 24 * 3600) -> int:
    """Remove draft orders never converted to a real purchase."""
    cutoff = int(time.time()) - older_than_seconds
    async with get_db() as db:
        cur = await db.execute(
            "DELETE FROM orders WHERE status = 'draft' AND created_at < ?",
            (cutoff,),
        )
        await db.commit()
        return cur.rowcount


async def get_payment(payment_id: int) -> Optional[aiosqlite.Row]:
    async with get_db() as db:
        cur = await db.execute("SELECT * FROM payments WHERE id = ?", (payment_id,))
        return await cur.fetchone()


async def pending_payments() -> list[aiosqlite.Row]:
    async with get_db() as db:
        now = int(time.time())
        cur = await db.execute(
            "SELECT * FROM payments WHERE status = 'pending' AND expires_at > ?",
            (now,),
        )
        return await cur.fetchall()


async def recent_expired_payments(
    *,
    lookback_seconds: int = 24 * 3600,
) -> list[aiosqlite.Row]:
    cutoff = int(time.time()) - lookback_seconds
    async with get_db() as db:
        cur = await db.execute(
            "SELECT * FROM payments "
            "WHERE status = 'expired' AND txid IS NULL AND expires_at >= ?",
            (cutoff,),
        )
        return await cur.fetchall()


async def cancel_payment(payment_id: int) -> None:
    async with get_db() as db:
        await db.execute(
            "UPDATE payments SET status = 'cancelled' WHERE id = ? AND status = 'pending'",
            (payment_id,),
        )
        await db.commit()


async def confirm_payment(payment_id: int, txid: str, raw: str) -> bool:
    async with get_db() as db:
        try:
            cur = await db.execute(
                "UPDATE payments SET status = 'confirmed', txid = ?, raw = ?, confirmed_at = ? "
                "WHERE id = ? AND status = 'pending'",
                (txid, raw, int(time.time()), payment_id),
            )
            await db.commit()
            return cur.rowcount > 0
        except aiosqlite.IntegrityError:
            await db.rollback()
            return False


async def expire_old_payments() -> list[aiosqlite.Row]:
    """Mark expired pending payments. Also marks their parent orders 'expired'.
    Returns the list of expired payment rows (for caller notifications)."""
    async with get_db() as db:
        now = int(time.time())
        cur = await db.execute(
            "SELECT * FROM payments WHERE status = 'pending' AND expires_at <= ?",
            (now,),
        )
        rows = await cur.fetchall()
        if not rows:
            return []
        ids = [r["id"] for r in rows]
        order_ids = [r["order_id"] for r in rows if r["order_id"]]
        id_ph = ",".join("?" * len(ids))
        await db.execute(
            f"UPDATE payments SET status = 'expired' WHERE id IN ({id_ph})",
            ids,
        )
        if order_ids:
            order_ph = ",".join("?" * len(order_ids))
            # Expire the parent order from any pre-paid status (draft as well as
            # awaiting_payment) so an expired invoice never leaves the order
            # looking open. (review L) 'paid'/'delivered' are untouched.
            await db.execute(
                f"UPDATE orders SET status = 'expired' WHERE id IN ({order_ph}) "
                "AND status IN ('awaiting_payment', 'draft')",
                order_ids,
            )
        await db.commit()
        return rows


async def find_pending_payment_by_amount(amount: float, coin: str, network: str) -> Optional[aiosqlite.Row]:
    """Match a pending payment by exact amount."""
    async with get_db() as db:
        cur = await db.execute(
            "SELECT * FROM payments WHERE coin = ? AND network = ? AND status = 'pending' "
            "AND ABS(unique_amount - ?) < 0.00005 "
            "ORDER BY ABS(unique_amount - ?) ASC, created_at ASC LIMIT 1",
            (coin, network, amount, amount),
        )
        return await cur.fetchone()


async def find_recently_expired_payment_by_amount(
    amount: float,
    coin: str,
    network: str,
    *,
    lookback_seconds: int = 24 * 3600,
) -> Optional[aiosqlite.Row]:
    """Return one exact expired invoice match, or None when ambiguous/missing."""
    cutoff = int(time.time()) - lookback_seconds
    async with get_db() as db:
        cur = await db.execute(
            "SELECT * FROM payments "
            "WHERE coin = ? AND network = ? AND status = 'expired' "
            "AND ABS(unique_amount - ?) < 0.00005 "
            "AND expires_at >= ? "
            "ORDER BY expires_at DESC LIMIT 2",
            (coin, network, amount, cutoff),
        )
        rows = await cur.fetchall()
        return rows[0] if len(rows) == 1 else None


async def count_recent_expired_payments_by_amount(
    amount: float,
    coin: str,
    network: str,
    *,
    lookback_seconds: int = 24 * 3600,
) -> int:
    cutoff = int(time.time()) - lookback_seconds
    async with get_db() as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM payments "
            "WHERE coin = ? AND network = ? AND status = 'expired' "
            "AND ABS(unique_amount - ?) < 0.00005 "
            "AND expires_at >= ?",
            (coin, network, amount, cutoff),
        )
        row = await cur.fetchone()
        return int((row[0] if row else 0) or 0)


async def recover_expired_payment_to_wallet(
    payment_id: int,
    txid: str,
    raw: str,
    amount: float,
) -> bool:
    """Confirm an expired invoice and credit the buyer wallet exactly once."""
    amount = round(float(amount), MONEY_DP)
    if not math.isfinite(amount) or amount <= 0:
        return False
    async with get_db() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            cur = await db.execute(
                "SELECT id, user_id, credited FROM payments "
                "WHERE id = ? AND status = 'expired' AND txid IS NULL",
                (payment_id,),
            )
            row = await cur.fetchone()
            if not row or int(row["credited"] or 0):
                await db.rollback()
                return False
            await db.execute(
                "UPDATE payments SET status = 'confirmed', txid = ?, raw = ?, "
                "confirmed_at = ?, credited = 1 WHERE id = ? AND status = 'expired'",
                (txid, raw, int(time.time()), payment_id),
            )
            await db.execute(
                "UPDATE users SET balance = ROUND(balance + ?, ?) WHERE id = ?",
                (amount, MONEY_DP, int(row["user_id"])),
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            await db.rollback()
            return False
        except Exception:
            await db.rollback()
            raise


async def record_unmatched_deposit(
    coin: str,
    network: str,
    amount: float,
    txid: str,
    raw: str,
) -> int | None:
    """Record verified funds that could not be safely matched to a buyer."""
    if not txid:
        return None
    amount = round(float(amount), MONEY_DP)
    if not math.isfinite(amount) or amount <= 0:
        return None
    async with get_db() as db:
        try:
            cur = await db.execute(
                "INSERT INTO unmatched_deposits "
                "(coin, network, amount, txid, raw, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'open', ?)",
                (coin, network, amount, txid, raw, int(time.time())),
            )
            await db.commit()
            return int(cur.lastrowid)
        except aiosqlite.IntegrityError:
            await db.rollback()
            return None


async def record_ignored_deposit(
    coin: str,
    network: str,
    amount: float,
    txid: str,
    raw: str,
) -> int | None:
    """Remember an unrelated incoming transfer so it is not reprocessed."""
    if not txid:
        return None
    amount = round(float(amount), MONEY_DP)
    if not math.isfinite(amount) or amount <= 0:
        return None
    async with get_db() as db:
        try:
            cur = await db.execute(
                "INSERT INTO unmatched_deposits "
                "(coin, network, amount, txid, raw, status, created_at, resolved_at) "
                "VALUES (?, ?, ?, ?, ?, 'ignored', ?, ?)",
                (coin, network, amount, txid, raw, int(time.time()), int(time.time())),
            )
            await db.commit()
            return int(cur.lastrowid)
        except aiosqlite.IntegrityError:
            await db.rollback()
            return None


async def open_unmatched_deposits() -> list[aiosqlite.Row]:
    async with get_db() as db:
        cur = await db.execute(
            "SELECT * FROM unmatched_deposits WHERE status = 'open' ORDER BY created_at DESC"
        )
        return await cur.fetchall()


async def resolve_unmatched_deposit(deposit_id: int, user_id: int) -> tuple[bool, float]:
    """Credit an open unmatched deposit to a user exactly once."""
    async with get_db() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            cur = await db.execute(
                "SELECT amount FROM unmatched_deposits WHERE id = ? AND status = 'open'",
                (deposit_id,),
            )
            row = await cur.fetchone()
            if not row:
                await db.rollback()
                return False, 0.0
            amount = round(float(row["amount"]), MONEY_DP)
            if not math.isfinite(amount) or amount <= 0:
                await db.rollback()
                return False, 0.0
            await db.execute(
                "UPDATE users SET balance = ROUND(balance + ?, ?) WHERE id = ?",
                (amount, MONEY_DP, user_id),
            )
            await db.execute(
                "UPDATE unmatched_deposits SET status = 'credited', user_id = ?, "
                "resolved_at = ? WHERE id = ? AND status = 'open'",
                (user_id, int(time.time()), deposit_id),
            )
            await db.commit()
            return True, amount
        except Exception:
            await db.rollback()
            raise


async def txid_already_used(txid: str) -> bool:
    """True if this on-chain/Pay txid has already been consumed by any payment.

    Keyed on the txid's PRESENCE, not on status='confirmed': txid is only ever
    written by confirm_payment, so any row carrying it has already processed
    that deposit. Checking presence (not status) means a later status change —
    a future refund/void, or a manual edit — can't reopen the txid for
    re-matching and re-crediting. (review M10)"""
    if not txid:
        return False
    async with get_db() as db:
        cur = await db.execute(
            "SELECT 1 FROM payments WHERE txid = ?",
            (txid,),
        )
        if await cur.fetchone() is not None:
            return True
        cur = await db.execute(
            "SELECT 1 FROM unmatched_deposits WHERE txid = ?",
            (txid,),
        )
        return await cur.fetchone() is not None


async def confirmed_topups_uncredited() -> list[aiosqlite.Row]:
    """Confirmed wallet top-ups whose credit was never applied (credited=0) —
    the symptom of a crash between confirm_payment and the wallet credit.
    Recovered idempotently at startup via credit_topup_once. (review H6)"""
    async with get_db() as db:
        cur = await db.execute(
            "SELECT * FROM payments "
            "WHERE status = 'confirmed' AND order_id IS NULL AND credited = 0"
        )
        return await cur.fetchall()


async def stuck_paid_orders() -> list[aiosqlite.Row]:
    """Orders left in 'paid' (the transient state between payment-confirm and
    delivery). The normal terminal state is 'delivered'; an order stuck in
    'paid' means delivery never completed (a crash after the status flip). The
    startup sweep re-runs deliver_order, which is idempotent. (review H6)"""
    async with get_db() as db:
        cur = await db.execute("SELECT * FROM orders WHERE status = 'paid'")
        return await cur.fetchall()


async def all_payments(limit: int = 500) -> list[aiosqlite.Row]:
    async with get_db() as db:
        cur = await db.execute(
            "SELECT p.*, u.tg_id AS user_tg_id, u.username AS user_username "
            "FROM payments p JOIN users u ON u.id = p.user_id "
            "ORDER BY p.created_at DESC LIMIT ?",
            (limit,),
        )
        return await cur.fetchall()
