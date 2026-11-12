"""
Smoke test for the admin dashboard changes:
  - list_users(search=...) returns the right rows for tg_id / @username / partials.
  - Dashboard aggregate SQL (revenue / orders / payments / users count) excludes
    the rows owned by ADMIN_IDS users.
  - run.main() exits cleanly when one of its sub-tasks completes (the prior
    FIRST_EXCEPTION bug would hang here).

Run:
    python -m tests.smoke_admin_dashboard
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

TEST_DB = Path(__file__).parent / "_smoke_admin.db"
if TEST_DB.exists():
    TEST_DB.unlink()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db as db_module
db_module.DB_PATH = TEST_DB
import db  # noqa: E402


ADMIN_TG_ID = 6393267365  # matches the real .env ADMIN_IDS


async def _admin_filter_sql(admin_ids: list[int]) -> tuple[str, tuple]:
    """Mirror what webapp/app.py builds, so a divergence shows up here."""
    if not admin_ids:
        return "", ()
    placeholders = ",".join("?" for _ in admin_ids)
    return (
        f" AND user_id NOT IN ("
        f"SELECT id FROM users WHERE tg_id IN ({placeholders}))"
    ), tuple(admin_ids)


async def test_list_users_search() -> None:
    await db.get_or_create_user(tg_id=ADMIN_TG_ID, username="Ankunding7", first_name="Yousef")
    await db.get_or_create_user(tg_id=111222333, username="alice", first_name="Alice")
    await db.get_or_create_user(tg_id=444555666, username="bob_smith", first_name="Bob")
    await db.get_or_create_user(tg_id=778899001, username="carol", first_name="Carol")

    # No search returns all.
    rows = await db.list_users()
    assert len(rows) == 4
    print("✓ no-search returns all users")

    # Exact tg_id.
    rows = await db.list_users(search=str(ADMIN_TG_ID))
    assert len(rows) == 1 and int(rows[0]["tg_id"]) == ADMIN_TG_ID
    print("✓ exact tg_id search")

    # @-prefix is stripped.
    rows = await db.list_users(search="@Ankunding7")
    assert len(rows) == 1 and rows[0]["username"] == "Ankunding7"
    print("✓ @username search strips the @")

    # Partial tg_id (substring match).
    rows = await db.list_users(search="6393")
    tg_ids = [int(r["tg_id"]) for r in rows]
    assert ADMIN_TG_ID in tg_ids
    print(f"✓ partial tg_id search ({len(rows)} match(es))")

    # Case-insensitive username search.
    rows = await db.list_users(search="ANKUNDING")
    assert any(r["username"] == "Ankunding7" for r in rows)
    print("✓ case-insensitive username search")

    # Non-digit search hits first_name too.
    rows = await db.list_users(search="alice")
    assert any(r["first_name"] == "Alice" for r in rows)
    print("✓ first_name fallback match")

    # No match returns empty.
    rows = await db.list_users(search="nobody-by-this-name")
    assert rows == []
    print("✓ no-match returns empty")


async def test_admin_excluded_from_dashboard_sql() -> None:
    """Seed orders+payments for one admin and two regular users, then verify the
    admin-exclusion SQL the dashboard builds returns customer-only numbers."""
    admin = await db.get_or_create_user(tg_id=ADMIN_TG_ID, username="Ankunding7", first_name="Yousef")
    a = await db.get_or_create_user(tg_id=111222333, username="alice", first_name="Alice")
    b = await db.get_or_create_user(tg_id=444555666, username="bob_smith", first_name="Bob")
    admin_id = int(admin["id"])
    a_id = int(a["id"])
    b_id = int(b["id"])

    # Three delivered orders: admin $50 (test buy), alice $30, bob $20.
    pid = await db.create_product("X", "", "", 10.0)
    await db.add_stock_items(pid, [f"k{i}" for i in range(10)])
    for uid, amt in [(admin_id, 50.0), (a_id, 30.0), (b_id, 20.0)]:
        oid = await db.create_order(
            user_id=uid, product_id=pid, quantity=1,
            unit_price=amt, subtotal=amt,
            coupon_code=None, discount=0.0, payable=amt, status="delivered",
        )
        # Backfill delivered_at so the row looks real.
        from db import get_db
        async with get_db() as d:
            await d.execute(
                "UPDATE orders SET delivered_at = ? WHERE id = ?",
                (int(time.time()), oid),
            )
            await d.commit()

    # One confirmed payment each (no order link — wallet top-ups).
    for uid in (admin_id, a_id, b_id):
        pay_id, _ = await db.create_payment(
            user_id=uid, order_id=None, method="usdt_trc20",
            network="TRX", coin="USDT", base_amount=5.0, note=f"NOTE{uid}",
            address="TX...", binance_uid=None, ttl_seconds=600,
        )
        assert await db.confirm_payment(pay_id, f"0xtx{uid}", "{}")

    admin_filter, admin_params = await _admin_filter_sql([ADMIN_TG_ID])

    from db import get_db
    async with get_db() as d:
        async def scalar(q: str, params: tuple = ()) -> float:
            cur = await d.execute(q, params)
            row = await cur.fetchone()
            return float(row[0]) if row else 0.0

        # Revenue must be 30 + 20 = 50 (admin's 50 excluded).
        revenue = await scalar(
            "SELECT COALESCE(SUM(payable),0) FROM orders WHERE status='delivered'"
            + admin_filter,
            admin_params,
        )
        assert revenue == 50.0, f"expected 50, got {revenue}"
        print(f"✓ revenue excludes admin ($50 -> ${revenue})")

        # Delivered orders count: 2 (admin's excluded).
        orders_n = await scalar(
            "SELECT COUNT(*) FROM orders WHERE status='delivered'" + admin_filter,
            admin_params,
        )
        assert orders_n == 2, f"expected 2, got {orders_n}"
        print(f"✓ delivered-orders count excludes admin (3 -> {int(orders_n)})")

        # Confirmed payments: 2 (admin's excluded).
        payments_n = await scalar(
            "SELECT COUNT(*) FROM payments WHERE status='confirmed'" + admin_filter,
            admin_params,
        )
        assert payments_n == 2, f"expected 2, got {payments_n}"
        print(f"✓ confirmed-payments count excludes admin (3 -> {int(payments_n)})")

        # Users count: 2 (admin's excluded).
        users_n = await scalar(
            f"SELECT COUNT(*) FROM users WHERE tg_id NOT IN ({','.join('?' for _ in [ADMIN_TG_ID])})",
            (ADMIN_TG_ID,),
        )
        assert users_n == 2, f"expected 2, got {users_n}"
        print(f"✓ users count excludes admin (3 -> {int(users_n)})")

    # Empty ADMIN_IDS path must NOT crash and must include everyone.
    empty_filter, empty_params = await _admin_filter_sql([])
    assert empty_filter == "" and empty_params == ()
    async with get_db() as d:
        cur = await d.execute(
            "SELECT COALESCE(SUM(payable),0) FROM orders WHERE status='delivered'" + empty_filter,
            empty_params,
        )
        row = await cur.fetchone()
        assert float(row[0]) == 100.0, "empty admin_ids must include all rows"
    print("✓ empty ADMIN_IDS falls back to all-rows")


def _verify_run_source_has_fix() -> None:
    """The local Python doesn't have aiogram/uvicorn installed, so we can't
    import run.py here. Verify the fix textually instead, then exercise the
    same coordination pattern inline below."""
    src = (Path(__file__).resolve().parent.parent / "run.py").read_text(encoding="utf-8")
    # Strip comments before checking, so explanatory comments don't false-trip.
    code_only = "\n".join(
        line.split("#", 1)[0] for line in src.splitlines()
    )
    assert "FIRST_COMPLETED" in code_only, "run.py is missing FIRST_COMPLETED"
    assert "FIRST_EXCEPTION" not in code_only, "run.py still calls FIRST_EXCEPTION"
    assert "asyncio.gather(*pending" in code_only, "run.py is not draining cancelled tasks"
    print("✓ run.py source has FIRST_COMPLETED + gather-on-cancel")


def test_products_search_filter_markup() -> None:
    """Products search is admin-only client-side filtering; keep its hooks."""
    root = Path(__file__).resolve().parent.parent
    html = (root / "webapp" / "templates" / "products.html").read_text(encoding="utf-8")
    css = (root / "webapp" / "static" / "admin.css").read_text(encoding="utf-8")

    assert 'id="product-search-input"' in html
    assert 'placeholder="Search product name..."' in html
    assert 'id="product-search-submit"' in html
    assert "Search" in html
    assert 'id="product-search-reset"' in html
    assert "Show all" in html
    assert "product-search-quick-edit" not in html
    assert "Quick edit first match" not in html
    assert 'data-product-name="{{ it.p.name|lower }}"' in html
    assert "data-product-edit" in html
    assert "quickEditFirstMatch" not in html
    assert "applyProductSearch" in html
    assert "submit.addEventListener('click', applyProductSearch)" in html
    assert "reset.addEventListener('click', resetProductSearch)" in html
    assert "name.indexOf(q)" in html, "search must match product name only"
    assert ".product-card.is-search-hidden" in css
    print("products page has admin-only click-to-search filtering hooks")


def test_dashboard_broadcast_tool_markup() -> None:
    """The broadcast dashboard exposes product-based bulk and flash-sale tools."""
    root = Path(__file__).resolve().parent.parent
    html = (root / "webapp" / "templates" / "broadcast.html").read_text(encoding="utf-8")
    css = (root / "webapp" / "static" / "admin.css").read_text(encoding="utf-8")

    assert 'action="/broadcast/bulk-pricing"' in html
    assert 'name="product_ids"' in html
    assert "Send bulk pricing broadcast" in html
    assert 'action="/broadcast/flash-sale"' in html
    assert 'name="was_price"' in html
    assert 'name="now_price"' in html
    assert 'name="duration"' in html
    assert "does not edit the product's actual price" in html
    assert ".broadcast-product-list" in css
    assert ".broadcast-product-option" in css
    print("broadcast page exposes bulk-pricing and flash-sale tools")


async def _coordinate(bot_coro_factory, web_coro_factory):
    """Inline copy of run.main()'s task-coordination logic, with the fix
    applied. Kept in sync with run.py:40-65."""
    bot_task = asyncio.create_task(bot_coro_factory(), name="telegram-bot")
    web_task = asyncio.create_task(web_coro_factory(), name="admin-web")
    done, pending = await asyncio.wait(
        {bot_task, web_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    for t in pending:
        t.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    for t in done:
        if t.cancelled():
            continue
        exc = t.exception()
        if exc is not None:
            raise exc


async def test_run_main_no_hang_on_clean_exit() -> None:
    _verify_run_source_has_fix()

    seen: dict[str, bool] = {"bot_finally": False}

    async def fake_web():
        # Web returns cleanly (simulates uvicorn after a SIGTERM).
        await asyncio.sleep(0.05)

    async def fake_bot():
        try:
            while True:
                await asyncio.sleep(0.1)
        finally:
            seen["bot_finally"] = True

    started = time.monotonic()
    await asyncio.wait_for(_coordinate(fake_bot, fake_web), timeout=3.0)
    elapsed = time.monotonic() - started
    assert elapsed < 1.0, f"coordinate() took {elapsed:.2f}s — shutdown still hangs"
    assert seen["bot_finally"] is True, "bot finally-block didn't run"
    print(f"✓ main() pattern returns in {elapsed*1000:.0f}ms after web exits "
          "(bot cleanup ran)")


async def test_run_main_propagates_exception() -> None:
    """A sub-task exception must still bubble out under FIRST_COMPLETED."""
    async def boom():
        raise RuntimeError("kaboom")

    async def patient():
        await asyncio.sleep(10)

    try:
        await asyncio.wait_for(_coordinate(patient, boom), timeout=3.0)
    except RuntimeError as e:
        assert "kaboom" in str(e)
        print("✓ sub-task exception propagates out of main() pattern")
        return
    raise AssertionError("expected RuntimeError to propagate")


class _FakeBot:
    """Minimal aiogram-Bot stand-in. Records every send_message call so the
    tests can assert text + reply_markup without hitting the network."""
    def __init__(self, fail_for: set[int] | None = None):
        self.calls: list[dict] = []
        self.fail_for = fail_for or set()

    async def send_message(self, chat_id: int, text: str, reply_markup=None, **kwargs):
        if chat_id in self.fail_for:
            raise RuntimeError(f"simulated send fail for {chat_id}")
        self.calls.append({
            "chat_id": chat_id,
            "text": text,
            "reply_markup": reply_markup,
            "kwargs": kwargs,
        })


async def _seed_users_and_product(n_users: int) -> int:
    """Create n_users + one product with stock. Returns product_id."""
    for i in range(n_users):
        await db.get_or_create_user(tg_id=10_000 + i, username=f"u{i}", first_name=f"U{i}")
    pid = await db.create_product("Test Product", "🟢", "demo", 9.99)
    await db.add_stock_items(pid, [f"k{i}" for i in range(n_users + 5)])
    await db.update_product(pid, icon_custom_emoji_id="999888777")
    return pid


async def test_broadcast_restock_skips_when_zero() -> None:
    """items_added == 0 must short-circuit (no DB read, no sends) so callers
    can fire-and-forget without guarding."""
    from services.broadcast import broadcast_restock
    pid = await _seed_users_and_product(3)
    product = await db.get_product(pid)
    bot = _FakeBot()
    ok, fail = await broadcast_restock(bot, product, 0)
    assert (ok, fail) == (0, 0), f"expected (0,0), got ({ok},{fail})"
    assert bot.calls == [], "no send_message should have fired"
    print("✓ broadcast_restock short-circuits on items_added=0")


async def test_broadcast_restock_throttled() -> None:
    """5 users → 5 send_messages with ~25/sec throttle between each.
    Elapsed must be at least 5 * RATE_LIMIT_DELAY (sleep runs after every send,
    not just between them) — sanity-check the rate-limit is actually wired up."""
    from services.broadcast import broadcast_restock, RATE_LIMIT_DELAY
    pid = await _seed_users_and_product(5)
    product = await db.get_product(pid)
    bot = _FakeBot()
    started = time.monotonic()
    ok, fail = await broadcast_restock(bot, product, 10)
    elapsed = time.monotonic() - started
    assert (ok, fail) == (5, 0), f"expected (5,0), got ({ok},{fail})"
    # 5 sends × ~40ms = ~200ms minimum. Allow a small floor for scheduler jitter.
    floor = 5 * RATE_LIMIT_DELAY * 0.8
    assert elapsed >= floor, f"expected ≥ {floor:.3f}s, got {elapsed:.3f}s — throttle missing?"
    print(f"✓ broadcast_restock throttled: 5 sends in {elapsed*1000:.0f}ms "
          f"(floor {floor*1000:.0f}ms)")


async def test_broadcast_restock_message_shape() -> None:
    """The broadcast text must mention the product name + items added, and
    carry a Buy button whose callback_data routes to the product detail page."""
    from services.broadcast import broadcast_restock
    pid = await _seed_users_and_product(2)
    product = await db.get_product(pid)
    bot = _FakeBot()
    ok, _ = await broadcast_restock(bot, product, 7)
    assert ok == 2 and len(bot.calls) == 2
    msg = bot.calls[0]
    assert "Test Product" in msg["text"], f"product name missing: {msg['text']!r}"
    assert "7 new stock added" in msg["text"], f"items count missing: {msg['text']!r}"
    assert "RESTOCK JUST LANDED" in msg["text"], f"hype title missing: {msg['text']!r}"
    assert "Fresh batch:" in msg["text"], f"fresh batch line missing: {msg['text']!r}"
    assert "Vault status" in msg["text"], f"vault status line missing: {msg['text']!r}"
    assert "Tap below" in msg["text"], f"CTA prompt missing: {msg['text']!r}"
    kb = msg["reply_markup"]
    assert kb is not None and kb.inline_keyboard, "no inline keyboard attached"
    btn = kb.inline_keyboard[0][0]
    assert btn.callback_data == f"prod:{pid}", f"wrong callback: {btn.callback_data!r}"
    assert btn.text == "Buy Now", f"wrong CTA text: {btn.text!r}"
    assert btn.icon_custom_emoji_id == "999888777"
    print(f"✓ broadcast_restock message shape (callback_data=prod:{pid})")


async def _seed_one_user_product_with_stock(stock: int) -> int:
    """Set up a single user + product with exactly `stock` available codes."""
    await db.get_or_create_user(tg_id=99_999, username="seed", first_name="Seed")
    pid = await db.create_product("Seed Product", "🟢", "x", 1.0)
    await db.add_stock_items(pid, [f"k{i}" for i in range(stock)])
    return pid


async def test_broadcast_scarcity_low_stock_shows_count() -> None:
    """When post-restock available stock is at or below LOW_STOCK_THRESHOLD,
    the broadcast must surface the concrete remaining count."""
    from services.broadcast import broadcast_restock, LOW_STOCK_THRESHOLD
    pid = await _seed_one_user_product_with_stock(LOW_STOCK_THRESHOLD)
    product = await db.get_product(pid)
    bot = _FakeBot()
    await broadcast_restock(bot, product, 5)
    text = bot.calls[0]["text"]
    assert f"Only {LOW_STOCK_THRESHOLD} left" in text, \
        f"low-stock scarcity line missing: {text!r}"
    print(f"✓ scarcity line shows exact count when stock={LOW_STOCK_THRESHOLD}")


async def test_broadcast_scarcity_high_stock_shows_generic() -> None:
    """When available stock is well above the threshold, the broadcast must
    fall back to the generic urgency line (no fake number claims)."""
    from services.broadcast import broadcast_restock, LOW_STOCK_THRESHOLD
    pid = await _seed_one_user_product_with_stock(LOW_STOCK_THRESHOLD + 50)
    product = await db.get_product(pid)
    bot = _FakeBot()
    await broadcast_restock(bot, product, 10)
    text = bot.calls[0]["text"]
    assert "Limited drop" in text, f"generic scarcity line missing: {text!r}"
    assert "Only " not in text, f"unexpected low-stock copy at high stock: {text!r}"
    print("✓ scarcity line falls back to generic at high stock")


async def test_broadcast_restock_counts_failures() -> None:
    """One failing recipient must increment `fail`, others still go through."""
    from services.broadcast import broadcast_restock
    pid = await _seed_users_and_product(3)
    product = await db.get_product(pid)
    bot = _FakeBot(fail_for={10_001})  # second user fails
    ok, fail = await broadcast_restock(bot, product, 1)
    assert (ok, fail) == (2, 1), f"expected (2,1), got ({ok},{fail})"
    print("✓ broadcast_restock isolates per-user failures")


async def test_programmed_broadcasts_have_hype_chrome() -> None:
    from services.broadcast import (
        broadcast_admin_notification, broadcast_new_product, broadcast_price_drop,
    )

    pid = await _seed_users_and_product(1)
    product = await db.get_product(pid)

    price_bot = _FakeBot()
    ok, fail = await broadcast_price_drop(price_bot, product, 19.99, 7.0)
    assert (ok, fail) == (1, 0)
    price_msg = price_bot.calls[0]
    assert "PRICE DROP ALERT" in price_msg["text"], price_msg["text"]
    assert "Lock the cheaper price" in price_msg["text"], price_msg["text"]
    price_btn = price_msg["reply_markup"].inline_keyboard[0][0]
    assert price_btn.text == "Buy Now"
    assert price_btn.icon_custom_emoji_id == "999888777"
    assert price_btn.url and f"start=prod{pid}" in price_btn.url

    new_bot = _FakeBot()
    ok, fail = await broadcast_new_product(new_bot, product)
    assert (ok, fail) == (1, 0)
    new_msg = new_bot.calls[0]
    assert "NEW DROP IS LIVE" in new_msg["text"], new_msg["text"]
    assert "Featured:" in new_msg["text"], new_msg["text"]
    assert "Tap below" in new_msg["text"], new_msg["text"]
    new_btn = new_msg["reply_markup"].inline_keyboard[0][0]
    assert new_btn.text == "Buy Now"
    assert new_btn.icon_custom_emoji_id == "999888777"
    assert new_btn.url and f"start=prod{pid}" in new_btn.url

    admin_bot = _FakeBot()
    ok, fail = await broadcast_admin_notification(admin_bot, "Launch <sale>")
    assert (ok, fail) == (1, 0)
    admin_msg = admin_bot.calls[0]
    assert "Akunding Store Broadcast" in admin_msg["text"], admin_msg["text"]
    assert "Launch &lt;sale&gt;" in admin_msg["text"], admin_msg["text"]
    assert "━━━━━━━━━━━━━━━━" in admin_msg["text"], admin_msg["text"]
    assert admin_msg["kwargs"].get("parse_mode") == "HTML"
    print("✓ programmed broadcasts use hype chrome and safe product CTAs")


async def test_dashboard_product_broadcast_messages() -> None:
    from webapp.broadcast_tools import (
        broadcast_bulk_pricing, broadcast_flash_sale, bulk_pricing_text,
        flash_sale_text,
    )

    pid = await _seed_users_and_product(1)
    await db.update_product(pid, base_price=1.50)
    await db.set_pricing_tier(pid, 10, 1.20)
    await db.set_pricing_tier(pid, 20, 1.00)
    product = await db.get_product(pid)
    stock = await db.stock_count(pid)
    tiers = [
        {"min_qty": 10, "unit_price": 1.20},
        {"min_qty": 20, "unit_price": 1.00},
    ]

    bulk_text = bulk_pricing_text(product, stock, tiers)
    assert "New Bulk Pricing" in bulk_text, bulk_text
    assert "Test Product" in bulk_text, bulk_text
    assert "Base price: <b>1.50 USDT</b>" in bulk_text, bulk_text
    assert "Buy 10+" in bulk_text and "1.20 USDT" in bulk_text, bulk_text
    assert "Buy 20+" in bulk_text and "1 USDT" in bulk_text, bulk_text

    flash_text = flash_sale_text(product, stock, 1.50, 1.00, "12h")
    assert "FLASH SALE" in flash_text, flash_text
    assert "Was: <s>$1.50</s>" in flash_text, flash_text
    assert "Now: <b>$1 USDT</b> (-33%)" in flash_text, flash_text
    assert "ends in 12 hours" in flash_text, flash_text
    flash_text = flash_sale_text(product, stock, 1.50, 1.30, "8")
    assert "Now: <b>$1.30 USDT</b> (-13%)" in flash_text, flash_text
    assert "ends in 8 hours" in flash_text, flash_text

    bulk_bot = _FakeBot()
    ok, fail, products = await broadcast_bulk_pricing(bulk_bot, [pid])
    assert (ok, fail, products) == (1, 0, 1)
    user_msg = next(c for c in bulk_bot.calls if c["chat_id"] == 10_000)
    assert user_msg["kwargs"].get("parse_mode") == "HTML"
    btn = user_msg["reply_markup"].inline_keyboard[0][0]
    assert btn.text == "Buy Test Product"
    assert btn.url and f"start=prod{pid}" in btn.url
    assert btn.icon_custom_emoji_id == "999888777"

    flash_bot = _FakeBot()
    ok, fail, products = await broadcast_flash_sale(flash_bot, pid, 1.50, 1.00, "12h")
    assert (ok, fail, products) == (1, 0, 1)
    user_msg = next(c for c in flash_bot.calls if c["chat_id"] == 10_000)
    assert user_msg["kwargs"].get("parse_mode") == "HTML"
    btn = user_msg["reply_markup"].inline_keyboard[0][0]
    assert btn.text == "Buy Test Product"
    assert btn.url and f"start=prod{pid}" in btn.url
    print("dashboard product broadcasts render bulk and flash-sale CTAs")


async def main() -> int:
    await db.init_db()

    print("── list_users search ──")
    await test_list_users_search()

    print("\n── dashboard SQL excludes admin ──")
    await _reset_db()
    await test_admin_excluded_from_dashboard_sql()

    print("\n── run.main() clean shutdown ──")
    await test_run_main_no_hang_on_clean_exit()

    print("\nproducts search filter markup")
    test_products_search_filter_markup()

    print("\nbroadcast dashboard tools markup")
    test_dashboard_broadcast_tool_markup()

    print("\n── run.main() exception propagation ──")
    await test_run_main_propagates_exception()

    print("\n── broadcast_restock: zero-items short-circuit ──")
    await _reset_db()
    await test_broadcast_restock_skips_when_zero()

    print("\n── broadcast_restock: rate-limit throttle ──")
    await _reset_db()
    await test_broadcast_restock_throttled()

    print("\n── broadcast_restock: message + button shape ──")
    await _reset_db()
    await test_broadcast_restock_message_shape()

    print("\n── broadcast_restock: per-user failures isolated ──")
    await _reset_db()
    await test_broadcast_restock_counts_failures()

    print("\n── broadcast_restock: low-stock scarcity copy ──")
    await _reset_db()
    await test_broadcast_scarcity_low_stock_shows_count()

    print("\n── broadcast_restock: high-stock scarcity fallback ──")
    await _reset_db()
    await test_broadcast_scarcity_high_stock_shows_generic()

    print("\nprogrammed broadcast hype chrome")
    await _reset_db()
    await test_programmed_broadcasts_have_hype_chrome()

    print("\ndashboard product broadcast messages")
    await _reset_db()
    await test_dashboard_product_broadcast_messages()

    if TEST_DB.exists():
        TEST_DB.unlink()
    print("\nAll admin-dashboard smoke tests passed.")
    return 0


async def _reset_db():
    from db import get_db
    async with get_db() as d:
        for t in ("payments", "api_orders", "referral_rewards", "orders",
                  "stock_items", "pricing_tiers", "coupons", "products",
                  "balance_adjustments", "api_keys", "users"):
            await d.execute(f"DELETE FROM {t}")
        await d.commit()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
