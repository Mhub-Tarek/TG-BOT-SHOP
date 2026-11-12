"""
Smoke test for the post-review fixes. No Telegram or Binance needed.

    python -m tests.smoke_test

Covers:
  Happy paths
    - User, product (with features), pricing tiers, stock, atomic claim
    - Wallet payment + delivery, insufficient-balance reject
    - Coupon validate (percent + fixed), bad-coupon reject
  Critical race / bug regressions (the ones identified in the code review)
    - #1 Concurrent payment creation must give every payment a unique amount
    - #2 Double-paid order delivers ONCE; second confirmed payment refunds to wallet
    - #3 Coupon used_count bumps on delivery; max_uses is enforced
    - #4 pay_from_wallet refuses an already-paid order
  Plus
    - cancel_pending_payments_for_order
    - cleanup_stale_drafts
    - delete_product hard- vs soft-delete branch
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from zipfile import ZipFile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

TEST_DB = Path(__file__).parent / "_smoke.db"
if TEST_DB.exists():
    TEST_DB.unlink()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db as db_module
db_module.DB_PATH = TEST_DB

import db                                                  # noqa: E402
from services.coupons import validate_and_compute          # noqa: E402
from services.delivery import (OrderNotPayable, OutOfStock, confirm_and_settle,
                                deliver_order, pay_from_wallet)  # noqa: E402
from services.payments import (_poll_binance_pay, _poll_onchain_deposits, _recover_or_flag,
                                _usdt_amount_from_pay_tx,
                                create_order_payment)  # noqa: E402
from services.pricing import effective_unit_price          # noqa: E402
from services.refunds import refund_lookup_for_identifier, refund_lookup_for_tg  # noqa: E402


def _copy_text_value(button) -> str | None:
    copy_text = getattr(button, "copy_text", None)
    if copy_text is None:
        return None
    if isinstance(copy_text, dict):
        return copy_text.get("text")
    return getattr(copy_text, "text", None)


async def _setup_user_and_product() -> tuple[int, int]:
    user = await db.get_or_create_user(tg_id=999, username="alice", first_name="Alice")
    pid = await db.create_product(
        "Mobbin Pro 1 Year", "🟢", "Demo product", 10.00,
        features="💚 Instant Code Delivery", unit_label="code",
    )
    await db.add_stock_items(pid, [f"key-{i}" for i in range(20)])
    return int(user["id"]), pid


async def test_happy_paths():
    user_id, pid = await _setup_user_and_product()
    print("✓ user + product + stock set up")

    # Bulk tiers are now an explicit per-unit USDT price: buy 10+ → $9.00 each.
    await db.set_pricing_tier(pid, 10, 9.00)
    tiers = await db.get_pricing_tiers(pid)
    unit, disc = effective_unit_price(10.00, 10, tiers)
    assert unit == 9.00 and disc == 10.0
    # Below the threshold the base price applies.
    unit_small, _ = effective_unit_price(10.00, 5, tiers)
    assert unit_small == 10.00
    # Range-format display includes the base band + the tier band.
    from services.pricing import format_tier_summary
    summary = format_tier_summary(10.00, tiers, "code")
    assert "From 1-9: $10.00 / code" in summary
    assert "From 10+: $9.00 / code" in summary
    print("✓ dollar-based bulk tier applied + range summary")

    # Top up balance via a fake confirmed deposit payment.
    pay_id, amount = await db.create_payment(
        user_id=user_id, order_id=None, method="usdt_trc20",
        network="TRX", coin="USDT", base_amount=100.0, note="TOPUP1",
        address="TXxxxxxx", binance_uid=None, ttl_seconds=600,
    )
    assert await db.confirm_payment(pay_id, "0xtopup", "{}")
    await db.add_balance(user_id, amount)
    bal = float((await db.get_user(user_id))["balance"])
    assert bal == amount
    print(f"✓ wallet top-up; balance = {bal}")

    # Wallet pay one order.
    order_id = await db.create_order(
        user_id=user_id, product_id=pid, quantity=2,
        unit_price=10.0, subtotal=20.0,
        coupon_code=None, discount=0.0, payable=20.0, status="draft",
    )
    contents, charged = await pay_from_wallet(user_id, order_id)
    assert charged == 20.0 and contents == ["key-0", "key-1"]
    print(f"✓ wallet pay -> order #{order_id} delivered")

    # Duplicate TXID rejected.
    p2, _ = await db.create_payment(
        user_id=user_id, order_id=None, method="usdt_trc20", network="TRX",
        coin="USDT", base_amount=5.0, note="DUP1",
        address="TX...", binance_uid=None, ttl_seconds=600,
    )
    assert not await db.confirm_payment(p2, "0xtopup", "{}")
    print("✓ duplicate TXID blocked")


async def test_concurrent_amount_reservations():
    """Fix H4: two payments at the same base amount each get a DISTINCT
    unique_amount (cent-offset bump) instead of the second being refused, and
    each amount matches back to its own payment."""
    user_id, _ = await _setup_user_and_product()
    p1_id, a1 = await db.create_payment(
        user_id=user_id, order_id=None, method="usdt_trc20",
        network="TRX", coin="USDT", base_amount=10.0,
        note="RACE1", address="TX...", binance_uid=None, ttl_seconds=600,
    )
    p2_id, a2 = await db.create_payment(
        user_id=user_id, order_id=None, method="usdt_trc20",
        network="TRX", coin="USDT", base_amount=10.0,
        note="RACE2", address="TX...", binance_uid=None, ttl_seconds=600,
    )
    p3_id, a3 = await db.create_payment(
        user_id=user_id, order_id=None, method="usdt_trc20",
        network="TRX", coin="USDT", base_amount=10.0,
        note="RACE3", address="TX...", binance_uid=None, ttl_seconds=600,
    )
    # First invoice keeps the clean round amount; collisions bump by the
    # non-round PAYMENT_UNIQUE_BUMP step so the figure looks intentional and
    # users don't round it away (anti-rounding fix).
    step = db.PAYMENT_UNIQUE_BUMP
    assert a1 == 10.0, a1
    assert a2 == round(10.0 + step, 2), a2
    assert a3 == round(10.0 + 2 * step, 2), a3
    assert len({a1, a2, a3}) == 3
    # Each distinct amount resolves to exactly its own pending payment.
    m1 = await db.find_pending_payment_by_amount(a1, "USDT", "TRX")
    m2 = await db.find_pending_payment_by_amount(a2, "USDT", "TRX")
    m3 = await db.find_pending_payment_by_amount(a3, "USDT", "TRX")
    assert int(m1["id"]) == p1_id and int(m2["id"]) == p2_id and int(m3["id"]) == p3_id
    print(f"✓ same base amount → distinct unique amounts {a1}/{a2}/{a3}, each matchable")


async def test_double_payment_for_same_order():
    """Fix #2: two confirmed payments on one order — second must refund, not re-deliver."""
    user_id, pid = await _setup_user_and_product()
    order_id = await db.create_order(
        user_id=user_id, product_id=pid, quantity=1,
        unit_price=10.0, subtotal=10.0,
        coupon_code=None, discount=0.0, payable=10.0,
        status="awaiting_payment",
    )

    # Two payments for the same order on different networks.
    p1_id, a1 = await db.create_payment(
        user_id=user_id, order_id=order_id, method="usdt_trc20",
        network="TRX", coin="USDT", base_amount=10.0, note="N1",
        address="TX...", binance_uid=None, ttl_seconds=600,
    )
    p2_id, a2 = await db.create_payment(
        user_id=user_id, order_id=order_id, method="usdt_bep20",
        network="BSC", coin="USDT", base_amount=10.0, note="N2",
        address="0x...", binance_uid=None, ttl_seconds=600,
    )

    p1 = await db.get_payment(p1_id)
    p2 = await db.get_payment(p2_id)

    # First payment confirms and delivers.
    ev1 = await confirm_and_settle(p1, "0xtxa", "{}", a1)
    assert ev1 and ev1.get("contents") and not ev1.get("refunded")
    print(f"✓ first payment delivered: {ev1['contents']}")

    # Second payment also confirms, but order is already 'delivered' — must refund.
    ev2 = await confirm_and_settle(p2, "0xtxb", "{}", a2)
    assert ev2 and ev2.get("refunded") and ev2.get("refund_reason") == "order_already_settled"
    print(f"✓ second payment refunded ({a2:.4f} USDT to wallet)")

    # Order has exactly one stock item claimed.
    from db import get_db
    async with get_db() as d:
        cur = await d.execute(
            "SELECT COUNT(*) AS n FROM stock_items WHERE order_id = ?", (order_id,)
        )
        n = int((await cur.fetchone())["n"])
    assert n == 1, f"expected exactly 1 claimed stock item, got {n}"
    print(f"✓ exactly 1 stock item claimed (no double-delivery)")


async def test_cancel_siblings_on_method_change():
    """When the user picks a new payment method, earlier pending payments
    must be cancelled so they can't both fund the order."""
    user_id, pid = await _setup_user_and_product()
    order_id = await db.create_order(
        user_id=user_id, product_id=pid, quantity=1,
        unit_price=10.0, subtotal=10.0,
        coupon_code=None, discount=0.0, payable=10.0,
        status="awaiting_payment",
    )
    p1_id, _ = await db.create_payment(
        user_id=user_id, order_id=order_id, method="usdt_trc20",
        network="TRX", coin="USDT", base_amount=10.0, note="N1",
        address="TX...", binance_uid=None, ttl_seconds=600,
    )
    await db.cancel_pending_payments_for_order(order_id)
    p2_id, _ = await db.create_payment(
        user_id=user_id, order_id=order_id, method="usdt_bep20",
        network="BSC", coin="USDT", base_amount=10.0, note="N2",
        address="0x...", binance_uid=None, ttl_seconds=600,
    )
    p1 = await db.get_payment(p1_id)
    p2 = await db.get_payment(p2_id)
    assert p1["status"] == "cancelled"
    assert p2["status"] == "pending"
    print("✓ switching methods cancels the prior pending payment")


async def test_create_order_payment_preserves_old_on_collision():
    user_id, pid = await _setup_user_and_product()
    other = await db.get_or_create_user(tg_id=2002, username="other", first_name="Other")
    order_id = await db.create_order(
        user_id=user_id, product_id=pid, quantity=1,
        unit_price=10.0, subtotal=10.0,
        coupon_code=None, discount=0.0, payable=10.0,
        status="awaiting_payment",
    )
    other_order_id = await db.create_order(
        user_id=int(other["id"]), product_id=pid, quantity=1,
        unit_price=10.0, subtotal=10.0,
        coupon_code=None, discount=0.0, payable=10.0,
        status="awaiting_payment",
    )
    old_id, _ = await db.create_payment(
        user_id=user_id, order_id=order_id, method="usdt_bep20",
        network="BSC", coin="USDT", base_amount=10.0, note="OLD",
        address="0x...", binance_uid=None, ttl_seconds=600,
    )
    _block_id, block_amt = await db.create_payment(
        user_id=int(other["id"]), order_id=other_order_id, method="usdt_trc20",
        network="TRX", coin="USDT", base_amount=10.0, note="BLOCK",
        address="TX...", binance_uid=None, ttl_seconds=600,
    )

    # H4: switching `order_id` to TRC20 at the same base no longer collides —
    # it gets the next free cent-offset amount, distinct from the other user's
    # pending TRC20 invoice. The original BEP20 invoice is untouched.
    new_pid = await create_order_payment(
        user_id=user_id, order_id=order_id, method="usdt_trc20", amount=10.0,
    )
    new_pay = await db.get_payment(new_pid)
    assert new_pay["status"] == "pending"
    assert float(new_pay["unique_amount"]) != float(block_amt)
    old = await db.get_payment(old_id)
    # create_order_payment cancels OTHER pending payments for the SAME order
    # only after the new one exists, so the prior BEP20 invoice for this order
    # is now cancelled in favour of the new TRC20 one.
    assert old["status"] == "cancelled"
    print(f"✓ payment switch gets a distinct amount ({new_pay['unique_amount']}), "
          f"no collision with another user's {block_amt}")


async def test_wallet_insufficient_keeps_pending_crypto():
    user_id, pid = await _setup_user_and_product()
    order_id = await db.create_order(
        user_id=user_id, product_id=pid, quantity=1,
        unit_price=10.0, subtotal=10.0,
        coupon_code=None, discount=0.0, payable=10.0,
        status="awaiting_payment",
    )
    payment_id, _ = await db.create_payment(
        user_id=user_id, order_id=order_id, method="usdt_trc20",
        network="TRX", coin="USDT", base_amount=10.0, note="KEEP",
        address="TX...", binance_uid=None, ttl_seconds=600,
    )
    try:
        await pay_from_wallet(user_id, order_id)
        assert False, "expected insufficient wallet balance"
    except ValueError:
        pass
    payment = await db.get_payment(payment_id)
    assert payment["status"] == "pending"
    print("✓ failed wallet payment keeps pending crypto invoice")


async def test_coupon_used_count_and_max_uses():
    """Fix #3: deliver_order must bump used_count; max_uses must be enforced."""
    user_id, pid = await _setup_user_and_product()
    await db.add_balance(user_id, 200.0)

    cid = await db.create_coupon("ONEUSE", "percent", 50.0, max_uses=1)
    res, err = await validate_and_compute("ONEUSE", 10.0)
    assert err == "" and res.discount == 5.0
    print("✓ coupon validates before any use")

    # First order — should succeed and bump used_count to 1.
    order1 = await db.create_order(
        user_id=user_id, product_id=pid, quantity=1,
        unit_price=10.0, subtotal=10.0,
        coupon_code="ONEUSE", discount=5.0, payable=5.0, status="draft",
    )
    await pay_from_wallet(user_id, order1)
    coupon = await db.get_coupon("ONEUSE")
    assert int(coupon["used_count"]) == 1, f"used_count should be 1, got {coupon['used_count']}"
    print(f"✓ used_count bumped to 1 after delivery")

    # Second attempt — coupon should now refuse because max_uses=1.
    _, err = await validate_and_compute("ONEUSE", 10.0)
    assert err == "Coupon usage limit reached.", f"unexpected: {err!r}"
    print("✓ coupon refused after max_uses reached")


async def test_pay_from_wallet_status_gate():
    """Fix #4: pay_from_wallet refuses orders not in draft/awaiting_payment."""
    user_id, pid = await _setup_user_and_product()
    await db.add_balance(user_id, 100.0)

    order_id = await db.create_order(
        user_id=user_id, product_id=pid, quantity=1,
        unit_price=10.0, subtotal=10.0,
        coupon_code=None, discount=0.0, payable=10.0, status="cancelled",
    )
    try:
        await pay_from_wallet(user_id, order_id)
        assert False, "expected OrderNotPayable"
    except OrderNotPayable:
        print("✓ wallet pay refuses cancelled order")

    # Same check for an already-delivered order.
    order2 = await db.create_order(
        user_id=user_id, product_id=pid, quantity=1,
        unit_price=10.0, subtotal=10.0,
        coupon_code=None, discount=0.0, payable=10.0, status="delivered",
    )
    try:
        await pay_from_wallet(user_id, order2)
        assert False, "expected OrderNotPayable"
    except OrderNotPayable:
        print("✓ wallet pay refuses delivered order")


async def test_deliver_order_status_gate():
    """deliver_order itself refuses to run on a non-paid order."""
    user_id, pid = await _setup_user_and_product()
    order_id = await db.create_order(
        user_id=user_id, product_id=pid, quantity=1,
        unit_price=10.0, subtotal=10.0,
        coupon_code=None, discount=0.0, payable=10.0, status="draft",
    )
    try:
        await deliver_order(order_id)
        assert False, "expected OrderNotPayable"
    except OrderNotPayable:
        print("✓ deliver_order refuses non-'paid' status")


async def test_cleanup_stale_drafts():
    user_id, pid = await _setup_user_and_product()
    # Create draft with very old timestamp.
    from db import get_db
    async with get_db() as d:
        await d.execute(
            "INSERT INTO orders (user_id, product_id, quantity, unit_price, subtotal, "
            "coupon_code, discount, payable, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, NULL, 0, ?, 'draft', ?)",
            (user_id, pid, 1, 10.0, 10.0, 10.0, int(time.time()) - 100_000),
        )
        await d.commit()
    n = await db.cleanup_stale_drafts(older_than_seconds=86400)
    assert n >= 1
    print(f"✓ cleanup_stale_drafts removed {n} old draft(s)")


async def test_delete_product_always_hard():
    """delete_product is now an unconditional hard-delete: stock cascades,
    orders are wiped, pending payments cancelled, other payments unlinked."""
    user_id, _ = await _setup_user_and_product()

    # 1. Orphan product (no orders) → simple hard-delete.
    p_clean = await db.create_product("Clean", "", "", 1.0)
    result = await db.delete_product(p_clean)
    assert result == "deleted" and not await db.get_product(p_clean)
    print("✓ orphan product hard-deleted")

    # 2. Product with order + pending payment + confirmed payment.
    p_used = await db.create_product("Used", "", "", 1.0)
    await db.add_stock_items(p_used, ["k1", "k2"])
    order_id = await db.create_order(
        user_id, p_used, 1, 1.0, 1.0, None, 0, 1.0, "awaiting_payment",
    )
    pend_id, _ = await db.create_payment(
        user_id=user_id, order_id=order_id, method="usdt_trc20",
        network="TRX", coin="USDT", base_amount=1.0, note="DEL",
        address="TX...", binance_uid=None, ttl_seconds=600,
    )
    conf_id, _ = await db.create_payment(
        user_id=user_id, order_id=order_id, method="usdt_bep20",
        network="BSC", coin="USDT", base_amount=2.0, note="DEL2",
        address="0x...", binance_uid=None, ttl_seconds=600,
    )
    assert await db.confirm_payment(conf_id, "0xconfirm-del", "{}")

    result = await db.delete_product(p_used)
    assert result == "deleted"
    # Product gone.
    assert not await db.get_product(p_used)
    # Orders for this product gone.
    from db import get_db
    async with get_db() as d:
        cur = await d.execute(
            "SELECT COUNT(*) AS n FROM orders WHERE product_id = ?", (p_used,)
        )
        assert int((await cur.fetchone())["n"]) == 0
        # Stock items cascaded.
        cur = await d.execute(
            "SELECT COUNT(*) AS n FROM stock_items WHERE product_id = ?", (p_used,)
        )
        assert int((await cur.fetchone())["n"]) == 0
    # Pending payment cancelled, both rows unlinked from the deleted order.
    pend = await db.get_payment(pend_id)
    conf = await db.get_payment(conf_id)
    assert pend["status"] == "cancelled" and pend["order_id"] is None
    assert conf["status"] == "confirmed" and conf["order_id"] is None
    print("✓ product+orders+stock removed; payments preserved & unlinked")


async def test_binance_pay_matching():
    """Binance Pay amount parsing + closest pending match (no live API)."""
    user_a, _ = await _setup_user_and_product()
    user_b = await db.get_or_create_user(tg_id=1001, username="bob", first_name="Bob")
    user_b_id = int(user_b["id"])

    assert _usdt_amount_from_pay_tx({
        "amount": "10", "currency": "USDT",
        "fundsDetail": [{"currency": "USDT", "amount": "10"}],
    }) == 10.0
    assert _usdt_amount_from_pay_tx({
        "amount": "10.0001", "currency": "USDT",
        "fundsDetail": [{"currency": "USDT", "amount": "10.0001"}],
    }) == 10.0001
    print("✓ Binance Pay USDT amount parser")

    p_exact, amt = await db.create_payment(
        user_id=user_b_id, order_id=None, method="binance_pay",
        network="BINANCE_INTERNAL", coin="USDT", base_amount=10.0,
        note="B10", address=None, binance_uid="904169573", ttl_seconds=600,
    )
    assert amt == 10.0

    p_legacy, legacy_amt = await db.create_payment(
        user_id=user_a, order_id=None, method="binance_pay",
        network="BINANCE_INTERNAL", coin="USDT", base_amount=10.5,
        note="LEG", address=None, binance_uid="904169573", ttl_seconds=600,
    )
    assert legacy_amt == 10.5

    match_flat = await db.find_pending_payment_by_amount(10.0, "USDT", "BINANCE_INTERNAL")
    assert match_flat and int(match_flat["id"]) == p_exact
    match_legacy = await db.find_pending_payment_by_amount(10.5, "USDT", "BINANCE_INTERNAL")
    assert match_legacy and int(match_legacy["id"]) == p_legacy
    near_miss = await db.find_pending_payment_by_amount(10.004, "USDT", "BINANCE_INTERNAL")
    assert near_miss is None
    print("✓ exact amount match only (no 2-decimal fallback)")

    ev = await confirm_and_settle(
        await db.get_payment(p_exact), "P_TEST_FLAT_10", "{}", 10.0,
    )
    assert ev and not ev.get("order_id")
    bal = float((await db.get_user(user_b_id))["balance"])
    assert bal == 10.0
    print(f"✓ Binance Pay top-up credits wallet ({bal} USDT)")


async def test_rejects_stale_binance_pay_tx():
    """Transfers that predate the pending payment must not match."""
    from services.payments import payment_tx_time_allowed

    user_a, _ = await _setup_user_and_product()
    now = int(time.time())
    payment = {"created_at": now}
    assert payment_tx_time_allowed(payment, now)
    assert payment_tx_time_allowed(payment, now - 60)
    assert not payment_tx_time_allowed(payment, now - 1000)
    print("✓ stale Binance Pay tx rejected by time window")


async def test_code_delivery_chunking():
    from services.telegram_delivery import MAX_TELEGRAM_TEXT, code_delivery_messages

    codes = [f"code-{i}-" + ("x" * 200) for i in range(50)]
    messages = code_delivery_messages("📜 <b>Your codes:</b>", codes)
    assert len(messages) > 1
    assert all(len(m) <= MAX_TELEGRAM_TEXT + 100 for m in messages)
    assert "code-0-" in messages[0]
    assert "code-49-" in messages[-1]
    print("✓ code delivery chunks large orders")


async def test_order_download_exports_and_keyboards():
    user_id, pid = await _setup_user_and_product()
    await db.add_balance(user_id, 100.0)
    order_id = await db.create_order(
        user_id=user_id, product_id=pid, quantity=2,
        unit_price=10.0, subtotal=20.0,
        coupon_code=None, discount=0.0, payable=20.0, status="draft",
    )
    contents, _charged = await pay_from_wallet(user_id, order_id)
    order = await db.get_order(order_id)
    product = await db.get_product(pid)

    from services.order_exports import build_order_export

    txt = build_order_export(order=order, product=product, contents=contents, fmt="txt")
    assert txt.filename.endswith(f"_order_{order_id}.txt")
    txt_body = txt.data.decode("utf-8")
    assert "Mobbin Pro 1 Year" in txt_body
    assert f"Order: #{order_id}" in txt_body
    assert "1. key-0" in txt_body and "2. key-1" in txt_body

    xlsx = build_order_export(order=order, product=product, contents=contents, fmt="xlsx")
    assert xlsx.filename.endswith(f"_order_{order_id}.xlsx")
    assert xlsx.data.startswith(b"PK")
    from io import BytesIO
    with ZipFile(BytesIO(xlsx.data)) as zf:
        sheet = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert "Mobbin Pro 1 Year" in sheet
    assert f"#{order_id}" in sheet
    assert "key-0" in sheet and "key-1" in sheet

    from keyboards.main import history_menu, history_order_menu
    from keyboards.payment import delivery_keyboard

    delivery = delivery_keyboard(order_id)
    assert delivery.inline_keyboard[0][0].callback_data == f"order:download:{order_id}:txt"
    assert delivery.inline_keyboard[0][1].callback_data == f"order:download:{order_id}:xlsx"

    recent = await db.recent_orders(user_id, limit=1)
    history = history_menu(recent)
    assert history.inline_keyboard[0][0].callback_data == f"history:order:{order_id}"
    assert history.inline_keyboard[0][0].text == f"#{order_id} Mobbin Pro 1 Year"
    assert history.inline_keyboard[0][0].icon_custom_emoji_id == "5819108931443630172"
    from handlers.profile import _history_payload
    from types import SimpleNamespace
    history_text, history_orders = await _history_payload(
        SimpleNamespace(id=999, username="alice", first_name="Alice")
    )
    assert "Mobbin Pro 1 Year" not in history_text
    assert f"Order #{order_id}" not in history_text
    assert int(history_orders[0]["id"]) == order_id
    detail = history_order_menu(order_id, delivered=True)
    assert detail.inline_keyboard[0][0].callback_data == f"order:download:{order_id}:txt"
    print("OK buyer order downloads + clean logo-backed history buttons")


async def test_buyer_visual_hype_polish():
    from aiogram.methods import EditMessageText, SendDocument, SendMessage
    from bot import HypeBot
    from handlers.language import language_keyboard
    from handlers.start import welcome_text
    from handlers.shop import BUY_LOADING_FLASH_SECONDS, _buy_loading_flash_text
    from keyboards import hype_emoji as hype
    from keyboards.main import history_menu, main_menu, profile_menu
    from keyboards.payment import (
        delivery_keyboard, download_confirm_keyboard, pending_payment_keyboard,
    )
    from keyboards.shop import MAX_BUTTON_TEXT, product_button_text, product_detail_keyboard

    welcome = welcome_text("Alice")
    assert "Premium digital drops" in welcome
    assert "Choose your move below" in welcome
    assert "Refer & Earn" in welcome
    assert "{crown}" not in welcome and "{arrow}" not in welcome
    shop_title = hype.hype_text(__import__("i18n").t("shop.list_title"))
    assert "<tg-emoji" in shop_title
    assert "Live Digital Drops" in shop_title

    send_method = HypeBot._hype_method(
        SendMessage(chat_id=1, text="\U0001f6cd <b>Live Digital Drops</b> \u2728")
    )
    edit_method = HypeBot._hype_method(
        EditMessageText(chat_id=1, message_id=2, text="\U0001f6cd <b>Live Digital Drops</b> \u2728")
    )
    doc_method = HypeBot._hype_method(
        SendDocument(chat_id=1, document="file-id", caption="\U0001f4e5 Download")
    )
    assert "<tg-emoji" in send_method.text
    assert "<tg-emoji" in edit_method.text
    assert "<tg-emoji" in doc_method.caption

    menu_callbacks = [
        button.callback_data
        for row in main_menu().inline_keyboard
        for button in row
    ]
    menu_buttons = {
        button.callback_data: button
        for row in main_menu().inline_keyboard
        for button in row
    }
    assert menu_callbacks == [
        "menu:shop",
        "menu:wallet",
        "menu:profile",
        "menu:support",
        "menu:history",
        "menu:refer",
        "menu:api",
        "menu:language",
    ]
    assert menu_buttons["menu:shop"].icon_custom_emoji_id
    assert menu_buttons["menu:shop"].model_dump(exclude_none=True).get("style") == "primary"
    assert menu_buttons["menu:wallet"].icon_custom_emoji_id
    assert menu_buttons["menu:refer"].icon_custom_emoji_id
    assert menu_buttons["menu:api"].icon_custom_emoji_id
    assert menu_buttons["menu:shop"].icon_custom_emoji_id != menu_buttons["menu:wallet"].icon_custom_emoji_id
    assert menu_buttons["menu:shop"].icon_custom_emoji_id != menu_buttons["menu:refer"].icon_custom_emoji_id
    profile_callbacks = [
        button.callback_data
        for row in profile_menu().inline_keyboard
        for button in row
    ]
    assert profile_callbacks == ["menu:history", "menu:wallet", "menu:home"]

    language_flags = [
        ("\U0001f1ec\U0001f1e7", "lang:set:en"),
        ("\U0001f1f8\U0001f1e6", "lang:set:ar"),
        ("\U0001f1ee\U0001f1f3", "lang:set:hi"),
        ("\U0001f1fb\U0001f1f3", "lang:set:vi"),
        ("\U0001f1e8\U0001f1f3", "lang:set:zh"),
    ]
    language = language_keyboard(include_back=True)
    for row, (flag, callback_data) in zip(language.inline_keyboard, language_flags):
        lang_btn = row[0]
        assert lang_btn.text.startswith(f"{flag} ")
        assert lang_btn.callback_data == callback_data
        assert getattr(lang_btn, "icon_custom_emoji_id", None) in (None, "")

    order_id = 123
    delivery = delivery_keyboard(order_id)
    delivery_callbacks = [
        button.callback_data
        for row in delivery.inline_keyboard
        for button in row
    ]
    assert f"order:download:{order_id}:txt" in delivery_callbacks
    assert f"order:download:{order_id}:xlsx" in delivery_callbacks
    assert f"ticket:new:{order_id}" in delivery_callbacks
    assert "menu:shop" in delivery_callbacks
    assert "menu:home" in delivery_callbacks

    confirm = download_confirm_keyboard(order_id, "txt")
    assert confirm.inline_keyboard[0][0].callback_data == f"order:download_send:{order_id}:txt"
    assert confirm.inline_keyboard[0][1].callback_data == f"order:download_cancel:{order_id}"

    pending = pending_payment_keyboard(
        88, binance_uid="904169573", exact_amount="6.3074",
    )
    assert _copy_text_value(pending.inline_keyboard[0][0]) == "904169573"
    assert _copy_text_value(pending.inline_keyboard[1][0]) == "6.3074"
    assert pending.inline_keyboard[2][0].callback_data == "pay:check:88"
    assert pending.inline_keyboard[3][0].callback_data == "pay:cancel:88"
    address_pending = pending_payment_keyboard(
        90, address="0xABCDEF123456", exact_amount="12.34",
    )
    assert _copy_text_value(address_pending.inline_keyboard[0][0]) == "0xABCDEF123456"
    assert _copy_text_value(address_pending.inline_keyboard[1][0]) == "12.34"
    assert address_pending.inline_keyboard[2][0].callback_data == "pay:check:90"
    plain_pending = pending_payment_keyboard(89)
    assert plain_pending.inline_keyboard[0][0].callback_data == "pay:check:89"
    assert _copy_text_value(plain_pending.inline_keyboard[0][0]) is None

    history = history_menu([{
        "id": 77,
        "product_name": "Mobbin Pro 1 Year",
        "product_emoji": "🟢",
        "product_brand": "mobbin",
        "product_icon_emoji_id": "",
    }])
    assert history.inline_keyboard[0][0].callback_data == "history:order:77"

    buy_kb = product_detail_keyboard(55, True)
    buy_btn = buy_kb.inline_keyboard[0][0]
    assert buy_btn.text == "Buy Now"
    assert buy_btn.callback_data == "buy:55"
    assert buy_btn.icon_custom_emoji_id == "6129870117619634982"
    assert buy_btn.icon_custom_emoji_id == hype.buy_button_icon_id()
    assert buy_btn.icon_custom_emoji_id == hype.preferred_buy_icon_id()
    bad_percent_icon_id = "5229064374403998351"
    assert hype.mix_icons.get("\U0001f6cd") != bad_percent_icon_id
    assert hype.mix_icons.tge("\U0001f6cd", fallback="\U0001f6cd").find(bad_percent_icon_id) == -1
    assert buy_btn.icon_custom_emoji_id != bad_percent_icon_id
    assert hype.custom_id_for_char("\U0001f4e6") is None
    stock_line = hype.hype_text("\U0001f4e6 Stock: 20")
    assert bad_percent_icon_id not in stock_line
    assert "<tg-emoji" not in stock_line and "\U0001f4e6" in stock_line
    store_btn = buy_kb.inline_keyboard[1][0]
    assert store_btn.callback_data == "shop:page:0"
    assert store_btn.text == "Store"
    assert store_btn.icon_custom_emoji_id == hype.store_icon_id()
    assert store_btn.icon_custom_emoji_id != buy_btn.icon_custom_emoji_id
    assert product_detail_keyboard(55, False).inline_keyboard[0][0].callback_data == "shop:page:0"
    assert product_detail_keyboard(55, False).inline_keyboard[0][0].text == "Store"
    assert 0 < BUY_LOADING_FLASH_SECONDS <= 1
    assert _buy_loading_flash_text() == hype.loading_flash()
    assert "\u26a1" in _buy_loading_flash_text()
    assert "<tg-emoji" in hype.hype_text("\U0001f195 \U0001f4a5 \U0001f198 \u25c0\ufe0f")
    assert hype.clean_button_text("\U0001f198 Report \u25c0\ufe0f") == "Report"

    def assert_clean_buttons(markup) -> None:
        for row in markup.inline_keyboard:
            for btn in row:
                assert "<tg-emoji" not in btn.text
                assert "\U0001f198" not in btn.text
                assert "\u25c0\ufe0f" not in btn.text

    assert_clean_buttons(main_menu())
    assert_clean_buttons(delivery)
    assert_clean_buttons(confirm)
    assert_clean_buttons(pending)
    assert_clean_buttons(address_pending)
    assert_clean_buttons(history)
    assert_clean_buttons(buy_kb)

    long_name = "Very Long Premium AI Product Bundle With Extended Warranty And Extra Seats"
    assert product_button_text("Demo Product", 3.0, 1).endswith("$3 (Stock: 1)")
    assert product_button_text("Demo Product", 3.0, 0).endswith("$3 (Out of stock)")
    assert len(product_button_text(long_name, 123.45, 987)) <= MAX_BUTTON_TEXT
    assert len(product_button_text(long_name, 123.45, 0)) <= MAX_BUTTON_TEXT

    original = hype.pi.icon_id_for_product
    try:
        hype.pi.icon_id_for_product = lambda _product, _stock: None
        generic_icon = hype.product_icon_for({"name": "Unsafe", "emoji": "<bad&"})
        assert "<bad" not in generic_icon
        assert generic_icon == "\U0001f4e6"
        assert bad_percent_icon_id not in generic_icon
    finally:
        hype.pi.icon_id_for_product = original

    original_get = hype.mix_icons.get
    try:
        hype.mix_icons.get = lambda _char, index=0: None
        assert hype.buy_button_icon_id() == "6129870117619634982"
        fallback_btn = product_detail_keyboard(56, True).inline_keyboard[0][0]
        assert fallback_btn.text == "Buy Now"
        assert fallback_btn.icon_custom_emoji_id == "6129870117619634982"
    finally:
        hype.mix_icons.get = original_get

    print("OK buyer visual hype polish keeps callbacks, length limits, and safe emoji fallback")


async def test_refer_and_earn_feature():
    import types

    from handlers.refer import _refer_payload
    from handlers.shop import _create_and_show_order
    from keyboards.refer import refer_keyboard
    from services.payments import maybe_credit_referrer
    from services.referrals import (
        REFERRAL_FEATURE_STARTED_AT_KEY,
        REFERRAL_REWARD_USDT,
        maybe_apply_order_referral_reward,
        reconcile_referral_rewards,
    )

    await db.set_setting(REFERRAL_FEATURE_STARTED_AT_KEY, str(int(time.time()) - 10))

    referrer = await db.get_or_create_user(5001, "ref", "Referrer")
    other_referrer = await db.get_or_create_user(5003, "other", "Other")
    friend = await db.get_or_create_user(
        5002, "friend", "Friend", referrer_tg_id=5001
    )
    assert int(friend["referrer_id"]) == int(referrer["id"])
    await db.get_or_create_user(5002, "friend2", "Friend2", referrer_tg_id=5003)
    friend = await db.get_user(int(friend["id"]))
    assert int(friend["referrer_id"]) == int(referrer["id"])
    assert int(friend["referrer_id"]) != int(other_referrer["id"])

    text, link = await _refer_payload(
        types.SimpleNamespace(id=5001, username="ref", first_name="Referrer")
    )
    assert "Refer & Earn" in text
    assert "$2" in text
    assert "10% OFF first 3 orders" in text
    assert link.endswith("?start=ref5001")
    kb = refer_keyboard(link)
    assert _copy_text_value(kb.inline_keyboard[0][0]) == link
    assert kb.inline_keyboard[1][0].callback_data == "menu:home"

    pid = await db.create_product("Referral Product", "", "", 10.0)
    await db.add_stock_items(pid, [f"ref-code-{i}" for i in range(10)])

    class FakeMessage:
        def __init__(self):
            self.text = ""
            self.reply_markup = None

        async def answer(self, text, reply_markup=None):
            self.text = text
            self.reply_markup = reply_markup

        async def edit_text(self, text, reply_markup=None):
            self.text = text
            self.reply_markup = reply_markup

    order_ids: list[int] = []
    discounts: list[float] = []
    payables: list[float] = []
    for _ in range(4):
        msg = FakeMessage()
        await _create_and_show_order(msg, 5002, pid, 1)
        latest = max(
            await db.recent_orders(int(friend["id"]), 10),
            key=lambda row: int(row["id"]),
        )
        order_ids.append(int(latest["id"]))
        discounts.append(float(latest["referral_discount"]))
        payables.append(float(latest["payable"]))
        if discounts[-1]:
            assert f"${discounts[-1]:.2f}" in msg.text

    assert discounts[:3] == [1.0, 1.0, 1.0]
    assert payables[:3] == [9.0, 9.0, 9.0]
    assert discounts[3] == 0.0
    assert payables[3] == 10.0

    await db.update_order(order_ids[0], status="paid")
    await deliver_order(order_ids[0])
    assert float((await db.get_user(int(referrer["id"])))["balance"]) == REFERRAL_REWARD_USDT
    assert await maybe_apply_order_referral_reward(order_ids[0]) is False
    assert await reconcile_referral_rewards() == 0
    assert float((await db.get_user(int(referrer["id"])))["balance"]) == REFERRAL_REWARD_USDT

    low_friend = await db.get_or_create_user(
        5004, "low", "Low", referrer_tg_id=5001
    )
    low_pid = await db.create_product("Small Referral Product", "", "", 4.0)
    await db.add_stock_items(low_pid, ["low-code"])
    low_order = await db.create_order(
        int(low_friend["id"]), low_pid, 1, 4.0, 4.0, None, 0.0, 4.0, status="paid"
    )
    await deliver_order(low_order)
    assert float((await db.get_user(int(referrer["id"])))["balance"]) == REFERRAL_REWARD_USDT

    await maybe_credit_referrer(int(friend["id"]), 100.0)
    assert float((await db.get_user(int(referrer["id"])))["balance"]) == REFERRAL_REWARD_USDT
    print("OK refer & earn attribution, discounts, rewards, and top-up no-op")


async def test_reseller_api_key_bot_flow():
    import re
    import types

    from handlers.api import API_DOCS_URL, _api_payload
    from keyboards.api import api_link_keyboard, api_regenerate_confirm_keyboard
    from services.reseller_api import authenticate_api_key, regenerate_api_key_for_user

    user_obj = types.SimpleNamespace(id=6101, username="api_user", first_name="ApiUser")
    text, created = await _api_payload(user_obj)
    assert created is True
    assert API_DOCS_URL in text
    match = re.search(r"ak_[A-Za-z0-9_-]+", text)
    assert match, text
    first_key = match.group(0)
    user = await db.get_user_by_tg(6101)
    active = await db.get_active_api_key_for_user(int(user["id"]))
    assert active and str(active["key_prefix"]) in text
    assert await authenticate_api_key(first_key)

    text2, created2 = await _api_payload(user_obj)
    assert created2 is False
    assert first_key not in text2
    assert str(active["key_prefix"]) in text2
    assert api_link_keyboard().inline_keyboard[0][0].callback_data == "api:regen:ask"
    assert api_regenerate_confirm_keyboard().inline_keyboard[0][0].callback_data == "api:regen:yes"

    new_key = await regenerate_api_key_for_user(int(user["id"]))
    assert await authenticate_api_key(first_key) is None
    assert await authenticate_api_key(new_key.key)
    print("OK reseller API bot key creation, masking, and regeneration")


async def test_reseller_api_http_flow():
    from fastapi.testclient import TestClient

    from services.reseller_api import regenerate_api_key_for_user
    from webapp.app import create_app

    user = await db.get_or_create_user(6201, "reseller", "Reseller")
    uid = int(user["id"])
    await db.add_balance(uid, 50.0)
    key = await regenerate_api_key_for_user(uid)
    headers = {"Authorization": f"Bearer {key.key}"}

    pid = await db.create_product("API Product", "", "For API", 10.0, unit_label="code")
    await db.set_pricing_tier(pid, 2, 9.0)
    await db.add_stock_items(pid, ["api-code-1", "api-code-2", "api-code-3"])
    inactive = await db.create_product("Inactive API Product", "", "", 1.0)
    await db.update_product(inactive, active=0)

    client = TestClient(create_app())
    assert client.get("/docs").status_code == 404
    assert client.get("/api/docs").status_code == 200
    openapi = client.get("/api/openapi.json")
    assert openapi.status_code == 200
    paths = openapi.json()["paths"]
    assert "/v1/products" in paths and "/dashboard" not in paths

    assert client.get("/api/v1/me").status_code == 401
    assert client.get("/api/v1/me", headers={"Authorization": "Bearer bad"}).status_code == 401
    me = client.get("/api/v1/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["api_key_prefix"] == key.prefix

    products = client.get("/api/v1/products", headers=headers)
    assert products.status_code == 200
    product_names = [p["name"] for p in products.json()]
    assert "API Product" in product_names
    assert "Inactive API Product" not in product_names
    api_product = next(p for p in products.json() if p["id"] == pid)
    assert api_product["stock"] == 3
    assert api_product["bulk_tiers"][0]["unit_price"] == 9.0

    order_resp = client.post(
        "/api/v1/orders",
        headers={**headers, "X-Idempotency-Key": "idem-1"},
        json={"product_id": pid, "quantity": 2},
    )
    assert order_resp.status_code == 200, order_resp.text
    order_data = order_resp.json()
    assert order_data["status"] == "delivered"
    assert order_data["charged"] == 18.0
    assert order_data["items"] == ["api-code-1", "api-code-2"]
    assert float((await db.get_user(uid))["balance"]) == 32.0
    assert await db.stock_count(pid) == 1

    replay = client.post(
        "/api/v1/orders",
        headers={**headers, "X-Idempotency-Key": "idem-1"},
        json={"product_id": pid, "quantity": 2},
    )
    assert replay.status_code == 200
    assert replay.json()["id"] == order_data["id"]
    assert replay.json()["idempotent"] is True
    assert float((await db.get_user(uid))["balance"]) == 32.0
    assert await db.stock_count(pid) == 1

    expensive = await db.create_product("Expensive API Product", "", "", 40.0)
    await db.add_stock_items(expensive, ["expensive-code"])
    low_balance = client.post(
        "/api/v1/orders",
        headers={**headers, "X-Idempotency-Key": "idem-2"},
        json={"product_id": expensive, "quantity": 1},
    )
    assert low_balance.status_code == 402
    assert low_balance.json()["detail"]["short"] == 8.0
    assert await db.stock_count(expensive) == 1
    assert float((await db.get_user(uid))["balance"]) == 32.0

    listed = client.get("/api/v1/orders", headers=headers)
    assert listed.status_code == 200
    assert any(o["id"] == order_data["id"] for o in listed.json())
    export = client.get(f"/api/v1/orders/{order_data['id']}/export?format=txt", headers=headers)
    assert export.status_code == 200
    assert b"api-code-1" in export.content

    other_user = await db.get_or_create_user(6202, "other_api", "Other")
    other_pid = await db.create_product("Other API Product", "", "", 5.0)
    await db.add_stock_items(other_pid, ["other-code"])
    other_order = await db.create_order(
        int(other_user["id"]), other_pid, 1, 5.0, 5.0, None, 0.0, 5.0, status="paid"
    )
    await deliver_order(other_order)
    assert client.get(f"/api/v1/orders/{other_order}", headers=headers).status_code == 404
    assert client.get(f"/api/v1/orders/{other_order}/export?format=txt", headers=headers).status_code == 404
    assert any(int(o["id"]) == order_data["id"] for o in await db.recent_orders(uid, 10))
    print("OK reseller API docs/auth/products/orders/idempotency/export/access control")


async def test_profile_current_tab_acknowledges_refresh():
    from aiogram.exceptions import TelegramBadRequest
    from types import SimpleNamespace
    import handlers.profile as profile_handlers
    import i18n

    await db.get_or_create_user(tg_id=7701, username="samepage", first_name="Same")

    class FakeState:
        def __init__(self) -> None:
            self.cleared = False

        async def clear(self) -> None:
            self.cleared = True

    class FakeMessage:
        def __init__(self) -> None:
            self.edit_attempts = 0
            self.sent = []

        async def edit_text(self, *_args, **_kwargs) -> None:
            self.edit_attempts += 1
            raise TelegramBadRequest(
                method=None,
                message="Bad Request: message is not modified",
            )

        async def answer(self, *args, **kwargs) -> None:
            self.sent.append((args, kwargs))

    class FakeCallback:
        def __init__(self) -> None:
            self.from_user = SimpleNamespace(
                id=7701,
                username="samepage",
                first_name="Same",
            )
            self.message = FakeMessage()
            self.answers = []

        async def answer(self, text=None, show_alert=None) -> None:
            self.answers.append((text, show_alert))

    state = FakeState()
    cb = FakeCallback()
    with i18n.use_language("en"):
        expected = i18n.t("profile.refreshed")
        await profile_handlers.cb_profile(cb, state)
    assert state.cleared
    assert cb.message.edit_attempts == 1
    assert cb.message.sent == []
    assert cb.answers == [(expected, None)]
    print("✓ profile current-tab button answers instead of looking dead")


async def test_coupons_basic():
    await db.create_coupon("PCT10", "percent", 10.0)
    res, err = await validate_and_compute("PCT10", 100.0)
    assert err == "" and res.discount == 10.0 and res.new_payable == 90.0
    print("✓ percent coupon math")

    await db.create_coupon("FIX5", "fixed", 5.0)
    res, err = await validate_and_compute("FIX5", 12.0)
    assert err == "" and res.discount == 5.0 and res.new_payable == 7.0
    print("✓ fixed coupon math")

    _, err = await validate_and_compute("NONEXISTENT", 10.0)
    assert err == "Coupon not found."
    print("✓ bad coupon rejected")


async def test_admin_unlimited_wallet_and_cost_clear():
    """Admin (unlimited) wallet: never debited, charged 0, still delivers, and
    the consumed code's cost is wiped to NULL on delivery."""
    admin = await db.get_or_create_user(tg_id=555, username="boss", first_name="Boss")
    admin_id = int(admin["id"])
    bal0 = float((await db.get_user(admin_id))["balance"])
    assert bal0 == 0.0
    pid = await db.create_product("Admin Test", "", "", 10.0)
    await db.add_stock_items(pid, ["adminkey"], cost=4.0)  # costed code

    order_id = await db.create_order(
        user_id=admin_id, product_id=pid, quantity=1,
        unit_price=10.0, subtotal=10.0,
        coupon_code=None, discount=0.0, payable=10.0, status="draft",
    )
    contents, charged = await pay_from_wallet(admin_id, order_id, unlimited=True)
    assert charged == 0.0, f"admin must be charged 0, got {charged}"
    assert contents == ["adminkey"], f"admin order should still deliver, got {contents}"
    bal1 = float((await db.get_user(admin_id))["balance"])
    assert bal1 == bal0, f"admin balance must be unchanged ({bal0} -> {bal1})"

    from db import get_db
    async with get_db() as d:
        cur = await d.execute(
            "SELECT COUNT(*) AS n FROM stock_items "
            "WHERE order_id = ? AND cost IS NOT NULL", (order_id,),
        )
        assert int((await cur.fetchone())["n"]) == 0, "admin order's cost must be cleared"
    print("✓ admin wallet: no debit, charged 0, delivered, cost cleared")


async def test_profit_for_period():
    """profit_for_period counts only delivered, fully-costed, non-admin orders;
    uncosted codes and admin orders are excluded."""
    member = await db.get_or_create_user(tg_id=999, username="alice", first_name="A")
    uid = int(member["id"])
    await db.add_balance(uid, 1000.0)
    admin = await db.get_or_create_user(tg_id=555, username="boss", first_name="B")
    aid = int(admin["id"])

    pid = await db.create_product("P", "", "", 10.0)
    await db.add_stock_items(pid, ["a", "b"], cost=5.0)   # costed (ids 1,2)
    await db.add_stock_items(pid, ["c"])                  # uncosted (id 3)
    await db.add_stock_items(pid, ["d"], cost=5.0)        # costed (id 4, admin takes)

    async def buy(user_id, unlimited=False):
        oid = await db.create_order(
            user_id=user_id, product_id=pid, quantity=1,
            unit_price=10.0, subtotal=10.0,
            coupon_code=None, discount=0.0, payable=10.0, status="draft",
        )
        await pay_from_wallet(user_id, oid, unlimited=unlimited)
        return oid

    await buy(uid)                 # claims a (cost 5) -> profit 5
    await buy(uid)                 # claims b (cost 5) -> profit 5
    await buy(uid)                 # claims c (NULL)   -> excluded
    await buy(aid, unlimited=True)  # claims d (cost 5) -> admin + cost cleared

    day = await db.profit_for_period("day", exclude_admin_ids=[555])
    assert day == 10.0, f"day profit expected 10.0, got {day}"
    month = await db.profit_for_period("month", exclude_admin_ids=[555])
    assert month == 10.0, f"month profit expected 10.0, got {month}"
    # Even with no admin filter, the admin order is still excluded because its
    # cost was cleared to NULL on delivery (belt-and-suspenders).
    day_noadmin = await db.profit_for_period("day")
    assert day_noadmin == 10.0, f"day profit (no admin filter) expected 10.0, got {day_noadmin}"
    print("✓ profit_for_period: costed member sales only; uncosted + admin excluded")


async def test_profit_for_period_negative_margin():
    """Net profit can go negative when COGS exceeds revenue on delivered orders."""
    member = await db.get_or_create_user(tg_id=999, username="alice", first_name="A")
    uid = int(member["id"])
    await db.add_balance(uid, 100.0)

    pid = await db.create_product("Loss Leader", "", "", 10.0)
    await db.add_stock_items(pid, ["lossy"], cost=25.0)
    oid = await db.create_order(
        user_id=uid, product_id=pid, quantity=1,
        unit_price=10.0, subtotal=10.0,
        coupon_code=None, discount=0.0, payable=10.0, status="draft",
    )
    await pay_from_wallet(uid, oid)

    b = await db.profit_breakdown_for_period("day")
    assert b["raw_profit"] == -15.0
    assert b["displayed_profit"] == 0.0
    assert b["order_losses"] == 15.0
    day = await db.profit_for_period("day")
    assert day == -15.0, f"day profit expected -15.0, got {day}"
    print("✓ profit_for_period: negative net margin reported as raw profit")


async def test_profit_breakdown_matches_legacy():
    """profit_breakdown_for_period aligns with profit_for_period + revenue."""
    member = await db.get_or_create_user(tg_id=999, username="alice", first_name="A")
    uid = int(member["id"])
    await db.add_balance(uid, 500.0)
    pid = await db.create_product("Bulk", "", "", 10.0)
    await db.add_stock_items(pid, ["q1", "q2", "q3"], cost=5.0)
    oid = await db.create_order(
        user_id=uid, product_id=pid, quantity=3,
        unit_price=10.0, subtotal=30.0,
        coupon_code=None, discount=0.0, payable=30.0, status="draft",
    )
    await pay_from_wallet(uid, oid)

    b = await db.profit_breakdown_for_period("day")
    assert b["eligible_revenue"] == 30.0
    assert b["cogs"] == 15.0
    assert b["raw_profit"] == 15.0
    assert b["displayed_profit"] == 15.0
    assert await db.profit_for_period("day") == 15.0
    assert b["period_revenue"] == 30.0
    assert b["excluded_revenue"] == 0.0
    print("✓ profit_breakdown: qty>1 COGS and reconciliation fields")


async def test_profit_uses_payable_not_subtotal():
    """Coupon discount: profit = payable − COGS, not subtotal − COGS."""
    member = await db.get_or_create_user(tg_id=998, username="bob", first_name="B")
    uid = int(member["id"])
    await db.add_balance(uid, 100.0)
    pid = await db.create_product("Disc", "", "", 10.0)
    await db.add_stock_items(pid, ["d1"], cost=5.0)
    oid = await db.create_order(
        user_id=uid, product_id=pid, quantity=1,
        unit_price=10.0, subtotal=10.0,
        coupon_code="SAVE", discount=2.0, payable=8.0, status="draft",
    )
    await pay_from_wallet(uid, oid)
    b = await db.profit_breakdown_for_period("day")
    assert b["eligible_revenue"] == 8.0
    assert b["cogs"] == 5.0
    assert b["raw_profit"] == 3.0
    print("✓ profit_breakdown: coupon uses payable")


async def test_profit_excludes_null_delivered_at():
    """Delivered orders without delivered_at do not count in period profit."""
    member = await db.get_or_create_user(tg_id=997, username="carl", first_name="C")
    uid = int(member["id"])
    await db.add_balance(uid, 100.0)
    pid = await db.create_product("Old", "", "", 10.0)
    await db.add_stock_items(pid, ["old1"], cost=5.0)
    oid = await db.create_order(
        user_id=uid, product_id=pid, quantity=1,
        unit_price=10.0, subtotal=10.0,
        coupon_code=None, discount=0.0, payable=10.0, status="draft",
    )
    await pay_from_wallet(uid, oid)
    from db import get_db
    async with get_db() as conn:
        await conn.execute(
            "UPDATE orders SET delivered_at = NULL WHERE id = ?", (oid,)
        )
        await conn.commit()
    b = await db.profit_breakdown_for_period("day")
    assert b["period_revenue"] == 0.0
    assert b["eligible_revenue"] == 0.0
    assert b["displayed_profit"] == 0.0
    print("✓ profit_breakdown: null delivered_at excluded from period")


async def test_list_profit_order_rows():
    """list_profit_order_rows reconciles with profit_breakdown_for_period."""
    member = await db.get_or_create_user(tg_id=999, username="alice", first_name="A")
    uid = int(member["id"])
    await db.add_balance(uid, 500.0)
    pid = await db.create_product("RowTest", "🧪", "", 10.0)
    await db.add_stock_items(pid, ["r1", "r2"], cost=5.0)
    await db.add_stock_items(pid, ["r3"])

    async def buy():
        oid = await db.create_order(
            user_id=uid, product_id=pid, quantity=1,
            unit_price=10.0, subtotal=10.0,
            coupon_code=None, discount=0.0, payable=10.0, status="draft",
        )
        await pay_from_wallet(uid, oid)
        return oid

    await buy()
    await buy()
    await buy()

    rows = await db.list_profit_order_rows("day")
    assert len(rows) == 3, f"expected 3 rows, got {len(rows)}"
    included = [r for r in rows if r["reason"] == "included"]
    assert len(included) == 2
    assert all(r["product_name"] == "RowTest" for r in rows)
    sum_rev = sum(r["payable"] for r in included)
    sum_cogs = sum(r["cogs"] for r in included)
    sum_margin = sum(r["margin"] for r in included)
    b = await db.profit_breakdown_for_period("day")
    assert sum_rev == b["eligible_revenue"]
    assert round(sum_cogs, 2) == b["cogs"]
    assert round(sum_margin, 2) == b["raw_profit"]
    audit = await db.profit_orders_audit("day", limit=10)
    assert len(audit) == 3
    assert {a["order_id"] for a in audit} == {r["order_id"] for r in rows}
    print("✓ list_profit_order_rows: included totals match breakdown")


async def test_user_blocked_from_buying():
    """blocked users are flagged; admins cannot be blocked; unblock clears flag."""
    member = await db.get_or_create_user(tg_id=999, username="buyer", first_name="B")
    uid = int(member["id"])
    admin = await db.get_or_create_user(tg_id=555, username="boss", first_name="Boss")
    aid = int(admin["id"])

    assert not await db.is_user_blocked(uid)
    await db.set_user_blocked(uid, True, admin_tg_ids=[555])
    assert await db.is_user_blocked(uid)
    assert await db.is_user_blocked_by_tg(999)

    try:
        await db.set_user_blocked(aid, True, admin_tg_ids=[555])
        assert False, "expected admin block to fail"
    except ValueError as exc:
        assert "admin" in str(exc).lower()

    await db.set_user_blocked(uid, False, admin_tg_ids=[555])
    assert not await db.is_user_blocked(uid)
    print("✓ user block: toggle, tg lookup, admin protected, unblock")


async def test_checkout_rate_limit_blocks_order_spam():
    """Repeated unpaid checkout attempts should stop before creating more orders."""
    from handlers.shop import (
        CHECKOUT_RATE_LIMIT_MAX_ATTEMPTS,
        CHECKOUT_RATE_LIMIT_WINDOW_SECONDS,
        _create_and_show_order,
    )

    user = await db.get_or_create_user(tg_id=777001, username="flood", first_name="Flood")
    uid = int(user["id"])
    pid = await db.create_product("Flood Guard Product", "", "", 1.0)
    await db.add_stock_items(pid, [f"flood-code-{i}" for i in range(20)])

    for i in range(CHECKOUT_RATE_LIMIT_MAX_ATTEMPTS):
        await db.create_order(
            user_id=uid, product_id=pid, quantity=1,
            unit_price=1.0, subtotal=1.0,
            coupon_code=None, discount=0.0, payable=1.0,
            status="cancelled",
        )

    before = len(await db.recent_orders(uid, limit=50))

    class FakeMessage:
        def __init__(self):
            self.text = ""
            self.reply_markup = None

        async def answer(self, text, reply_markup=None):
            self.text = text
            self.reply_markup = reply_markup

        async def edit_text(self, text, reply_markup=None):
            self.text = text
            self.reply_markup = reply_markup

    msg = FakeMessage()
    await _create_and_show_order(msg, 777001, pid, 1)
    after = len(await db.recent_orders(uid, limit=50))
    assert after == before, "rate-limited checkout must not create another order"
    assert "checkout attempts" in msg.text

    old = await db.recent_orders(uid, limit=50)
    old_ids = [int(r["id"]) for r in old[:CHECKOUT_RATE_LIMIT_MAX_ATTEMPTS]]
    async with db.get_db() as d:
        await d.executemany(
            "UPDATE orders SET created_at = ? WHERE id = ?",
            [
                (int(time.time()) - CHECKOUT_RATE_LIMIT_WINDOW_SECONDS - 5, oid)
                for oid in old_ids
            ],
        )
        await d.commit()

    msg = FakeMessage()
    await _create_and_show_order(msg, 777001, pid, 1)
    after_window = len(await db.recent_orders(uid, limit=50))
    assert after_window == before + 1, "checkout should work again after the window"
    print("✓ checkout rate limit blocks unpaid order spam and recovers after window")


async def test_negative_quantity_exploit_blocked():
    """review C2: a crafted negative quantity must NOT credit the wallet or
    drain stock. Covers the db-level money/stock sinks + the wallet-pay path."""
    user_id, pid = await _setup_user_and_product()
    await db.add_balance(user_id, 50.0)
    bal_before = float((await db.get_user(user_id))["balance"])

    # Sink 1: a negative debit must be rejected, never CREDIT the wallet.
    assert await db.try_debit_balance(user_id, -25.0) is False
    assert float((await db.get_user(user_id))["balance"]) == bal_before
    # Sink 2: a negative quantity must claim ZERO stock (not "unlimited").
    assert await db.claim_stock_items(pid, -5, 1) == []
    assert await db.stock_count(pid) == 20

    # End-to-end: an order with negative payable can't be wallet-paid.
    bad_order = await db.create_order(
        user_id=user_id, product_id=pid, quantity=-5,
        unit_price=10.0, subtotal=-50.0,
        coupon_code=None, discount=0.0, payable=-50.0, status="draft",
    )
    try:
        await pay_from_wallet(user_id, bad_order)
        assert False, "negative-payable order must not be payable"
    except ValueError:
        pass
    assert float((await db.get_user(user_id))["balance"]) == bal_before
    assert await db.stock_count(pid) == 20
    print("✓ negative quantity/payable: no wallet credit, no stock drain")


async def test_credit_topup_once_idempotent():
    """review H6: a confirmed top-up credits exactly once even if retried."""
    user = await db.get_or_create_user(tg_id=7777, username="t", first_name="T")
    uid = int(user["id"])
    pay_id, amt = await db.create_payment(
        user_id=uid, order_id=None, method="usdt_trc20", network="TRX",
        coin="USDT", base_amount=15.0, note="TOP", address="TX",
        binance_uid=None, ttl_seconds=600,
    )
    assert await db.confirm_payment(pay_id, "0xcredit-once", "{}")
    assert await db.credit_topup_once(pay_id, uid, amt) is True
    assert float((await db.get_user(uid))["balance"]) == 15.0
    assert await db.credit_topup_once(pay_id, uid, amt) is False   # idempotent
    assert float((await db.get_user(uid))["balance"]) == 15.0
    print("✓ credit_topup_once credits exactly once")


async def test_reconcile_unsettled():
    """review H6: startup sweep credits confirmed-uncredited top-ups and
    re-delivers orders stuck in 'paid'."""
    from services.delivery import reconcile_unsettled_payments
    user_id, pid = await _setup_user_and_product()

    # (a) Confirmed top-up never credited (simulates a confirm→credit crash).
    pay_id, amt = await db.create_payment(
        user_id=user_id, order_id=None, method="usdt_trc20", network="TRX",
        coin="USDT", base_amount=12.0, note="RC", address="TX",
        binance_uid=None, ttl_seconds=600,
    )
    assert await db.confirm_payment(pay_id, "0xrecon-topup", "{}")
    bal0 = float((await db.get_user(user_id))["balance"])

    # (b) Order stuck in 'paid' (delivery never finished).
    stuck = await db.create_order(
        user_id=user_id, product_id=pid, quantity=1,
        unit_price=10.0, subtotal=10.0,
        coupon_code=None, discount=0.0, payable=10.0, status="paid",
    )

    summary = await reconcile_unsettled_payments()
    assert summary["topups_credited"] == 1, summary
    assert summary["orders_redelivered"] == 1, summary
    assert float((await db.get_user(user_id))["balance"]) == bal0 + amt
    assert (await db.get_order(stuck))["status"] == "delivered"
    print(f"✓ reconcile recovers crash-window deposits: {summary}")


async def test_sold_out_auto_deactivate():
    """feature: a sale that empties stock auto-deactivates the product (active=0,
    NOT deleted); a sale leaving stock keeps it active; reactivation is manual."""
    user_id, _ = await _setup_user_and_product()
    await db.add_balance(user_id, 1000.0)

    pid = await db.create_product("Limited Edition", "", "", 5.0)
    await db.add_stock_items(pid, ["k1", "k2"])
    assert int((await db.get_product(pid))["active"]) == 1

    async def buy_one():
        oid = await db.create_order(
            user_id=user_id, product_id=pid, quantity=1,
            unit_price=5.0, subtotal=5.0, coupon_code=None, discount=0.0,
            payable=5.0, status="draft",
        )
        await pay_from_wallet(user_id, oid)

    # 1 of 2 sold → stock remains → stays active.
    await buy_one()
    assert await db.stock_count(pid) == 1
    assert int((await db.get_product(pid))["active"]) == 1, "stays active with stock left"

    # Last unit sold → sold out → auto-deactivated (but NOT deleted).
    await buy_one()
    assert await db.stock_count(pid) == 0
    p = await db.get_product(pid)
    assert p is not None, "must NOT be deleted"
    assert int(p["active"]) == 0, "must auto-deactivate when sold out"

    # Reactivation is manual and works (admin choice), and re-deactivation only
    # happens on a fresh sold-out sale, not spuriously.
    await db.update_product(pid, active=1)
    assert int((await db.get_product(pid))["active"]) == 1
    # No sale happened, so deactivate_if_sold_out fires only when called on a
    # zero-stock product — confirm it does flip an out-of-stock active product.
    assert await db.deactivate_if_sold_out(pid) is True
    assert int((await db.get_product(pid))["active"]) == 0
    # Idempotent: calling again does nothing (already inactive).
    assert await db.deactivate_if_sold_out(pid) is False
    print("✓ sold-out product auto-deactivates (not deletes); manual reactivate works")


async def test_tickets():
    """feature: in-app support tickets — create (order-linked), list/search,
    status transitions, open-count, and admin reply."""
    user_id, pid = await _setup_user_and_product()
    order_id = await db.create_order(
        user_id=user_id, product_id=pid, quantity=1,
        unit_price=10.0, subtotal=10.0, coupon_code=None, discount=0.0,
        payable=10.0, status="delivered",
    )

    tid = await db.create_ticket(user_id, order_id, "My code didn't work")
    t = await db.get_ticket(tid)
    assert t and t["status"] == "open" and int(t["order_id"]) == order_id
    assert t["message"] == "My code didn't work"

    # Lists + status filter + admin-exclusion (no admins here so all show).
    rows = await db.all_tickets()
    assert any(int(r["id"]) == tid for r in rows)
    assert len(await db.all_tickets(status="open")) == 1
    assert len(await db.all_tickets(status="closed")) == 0
    # joined order/product columns are present for the dashboard
    assert rows[0]["product_name"] == "Mobbin Pro 1 Year"
    assert await db.count_open_tickets() == 1
    assert await db.count_active_tickets_for_user(user_id) == 1
    assert len(await db.recent_tickets_for_user(user_id)) == 1

    # Admin reply -> 'answered', records reply + replied_at, still counts as open.
    updated = await db.reply_ticket(tid, "Sent you a fresh code, sorry!")
    assert updated and updated["status"] == "answered"
    assert updated["admin_reply"] == "Sent you a fresh code, sorry!"
    assert updated["replied_at"] is not None
    assert await db.count_open_tickets() == 1
    assert len(await db.all_tickets(status="answered")) == 1

    # Close it -> drops out of the open queue.
    assert await db.set_ticket_status(tid, "closed") is True
    assert (await db.get_ticket(tid))["status"] == "closed"
    assert await db.count_open_tickets() == 0
    assert await db.count_active_tickets_for_user(user_id) == 0  # closed drops out
    # Invalid status is rejected.
    assert await db.set_ticket_status(tid, "bogus") is False
    print("✓ tickets: create/list/filter/reply(answered)/close/open-count all work")


async def test_batch_query_helpers():
    """perf: batched stock/sold/tier/available helpers match the per-product
    versions they replace."""
    p1 = await db.create_product("P1", "", "", 5.0)
    p2 = await db.create_product("P2", "", "", 5.0)
    await db.add_stock_items(p1, ["a", "b", "c"])
    await db.add_stock_items(p2, ["d"])
    await db.set_pricing_tier(p1, 10, 4.0)
    # Sell one from p1 so sold/unsold differ.
    oid = await db.create_order(
        user_id=int((await db.get_or_create_user(123, "u", "U"))["id"]),
        product_id=p1, quantity=1, unit_price=5.0, subtotal=5.0,
        coupon_code=None, discount=0.0, payable=5.0, status="paid",
    )
    await db.claim_stock_items(p1, 1, oid)

    ids = [p1, p2]
    counts = await db.stock_counts(ids)
    sold = await db.sold_counts(ids)
    tiers = await db.pricing_tiers_for_products(ids)
    avail = await db.available_stock_for_products(ids, per_limit=200)

    assert counts == {p1: await db.stock_count(p1), p2: await db.stock_count(p2)}
    assert counts[p1] == 2 and counts[p2] == 1
    assert sold[p1] == 1 and sold[p2] == 0
    assert len(tiers[p1]) == 1 and len(tiers[p2]) == 0
    assert len(avail[p1]) == 2 and len(avail[p2]) == 1
    print("✓ batch query helpers match per-product results")


async def test_deliver_idempotent_recovery():
    """review H6: a 'paid' order whose stock was already claimed (crash before
    the delivered flip) finishes WITHOUT claiming more stock."""
    user_id, pid = await _setup_user_and_product()   # 20 unsold
    order_id = await db.create_order(
        user_id=user_id, product_id=pid, quantity=2,
        unit_price=10.0, subtotal=20.0, coupon_code=None, discount=0.0,
        payable=20.0, status="paid",
    )
    # Simulate the crash window: stock claimed, status still 'paid'.
    claimed = await db.claim_stock_items(pid, 2, order_id)
    assert len(claimed) == 2
    sold_before = await db.count_sold_stock(pid)
    contents = await deliver_order(order_id)
    assert contents == claimed                                  # same codes
    assert await db.count_sold_stock(pid) == sold_before        # no extra claim
    assert (await db.get_order(order_id))["status"] == "delivered"
    print("✓ deliver_order idempotent: no double-claim on crash recovery")


async def test_add_balance_rejects_negative():
    user = await db.get_or_create_user(tg_id=8888, username="n", first_name="N")
    uid = int(user["id"])
    await db.add_balance(uid, 10.0)
    res = await db.add_balance(uid, -5.0)   # the credit primitive never reduces
    assert res == 10.0
    assert float((await db.get_user(uid))["balance"]) == 10.0
    print("✓ add_balance ignores negative amounts")


async def test_add_stock_dedup():
    pid = await db.create_product("Dedup", "", "", 1.0)
    n1 = await db.add_stock_items(pid, ["a", "b", "a", "", "c"])
    assert n1 == 3, n1   # a, b, c (duplicate 'a' + blank dropped)
    n2 = await db.add_stock_items(pid, ["c", "d"])   # 'c' already unsold
    assert n2 == 1, n2   # only 'd'
    assert await db.stock_count(pid) == 4
    print("✓ add_stock_items dedups within batch + against existing unsold")


async def test_bump_coupon_conditional():
    await db.create_coupon("LIM", "fixed", 1.0, max_uses=2)
    assert await db.bump_coupon_uses("LIM") is True    # 1
    assert await db.bump_coupon_uses("LIM") is True    # 2
    assert await db.bump_coupon_uses("LIM") is False   # capped
    assert int((await db.get_coupon("LIM"))["used_count"]) == 2
    print("✓ bump_coupon_uses stops at max_uses")


async def test_coupon_value_clamped():
    await db.create_coupon("BIG", "percent", 150.0)     # >100 → clamp to 100
    res, err = await validate_and_compute("BIG", 20.0)
    assert err == "" and res.discount == 20.0 and res.new_payable == 0.0
    await db.create_coupon("NEG", "percent", -50.0)     # negative → clamp to 0
    res, err = await validate_and_compute("NEG", 20.0)
    assert err == "" and res.discount == 0.0 and res.new_payable == 20.0
    print("✓ coupon value clamped to [0,100]% — no overcharge / negative payable")


async def test_analytics_helpers():
    """Statistics tab: event logging + series / funnel / KPI / top-products /
    retention helpers, including admin exclusion."""
    ADMIN = 555
    u1 = await db.get_or_create_user(tg_id=901, username="u1", first_name="U1")
    await db.get_or_create_user(tg_id=902, username="u2", first_name="U2")
    await db.get_or_create_user(tg_id=ADMIN, username="boss", first_name="B")
    excl = [ADMIN]

    # Funnel events (one admin start must be excluded everywhere).
    await db.log_event(901, "start")
    await db.log_event(902, "start")
    await db.log_event(ADMIN, "start")
    await db.log_event(901, "shop_view")
    await db.log_event(902, "shop_view")
    await db.log_event(901, "product_view", 1)

    starts = await db.events_series("day", 30, "start", exclude_admin_ids=excl)
    assert len(starts["labels"]) == 30 and sum(starts["values"]) == 2, starts
    shop = await db.events_series("day", 30, "shop_view", exclude_admin_ids=excl)
    assert sum(shop["values"]) == 2
    nu = await db.new_users_series("day", 30, exclude_admin_ids=excl)
    assert sum(nu["values"]) == 2, nu          # u1, u2 (admin excluded)
    print("✓ analytics: events_series + new_users_series + admin exclusion")

    # A delivered order for u1 → revenue / top-products / funnel.
    pid = await db.create_product("Stat P", "", "", 10.0)
    await db.add_stock_items(pid, ["s1", "s2", "s3"])
    o1 = await db.create_order(user_id=int(u1["id"]), product_id=pid, quantity=2,
        unit_price=10.0, subtotal=20.0, coupon_code=None, discount=0.0, payable=20.0, status="paid")
    await db.claim_stock_items(pid, 2, o1)
    await db.mark_order_delivered(o1)

    rev = await db.revenue_series("day", 30, exclude_admin_ids=excl)
    assert sum(rev["values"]) == 20.0, rev
    funnel = await db.funnel_counts(days=30, exclude_admin_ids=excl)
    assert funnel["started"] == 2 and funnel["shop_view"] == 2 and funnel["product_view"] == 1
    assert funnel["ordered"] == 1 and funnel["paid"] == 1 and funnel["delivered"] == 1
    top = await db.top_products(days=30, limit=8, exclude_admin_ids=excl)
    assert top and top[0]["name"] == "Stat P" and int(top[0]["units"]) == 2
    kpi = await db.kpi_summary("day", exclude_admin_ids=excl)
    assert kpi["total_users"] == 2 and kpi["new_users"]["current"] == 2
    assert kpi["revenue"]["current"] == 20.0
    print("✓ analytics: revenue_series + funnel_counts + top_products + kpi_summary")

    # Second delivered order for u1 → repeat buyer.
    o2 = await db.create_order(user_id=int(u1["id"]), product_id=pid, quantity=1,
        unit_price=10.0, subtotal=10.0, coupon_code=None, discount=0.0, payable=10.0, status="paid")
    await db.mark_order_delivered(o2)
    ret = await db.repeat_buyer_stats(exclude_admin_ids=excl)
    assert ret["buyers"] == 1 and ret["repeat"] == 1 and ret["repeat_rate"] == 100.0
    print("✓ analytics: repeat_buyer_stats")


async def test_i18n():
    """i18n: catalog parity (same keys + matching {placeholders} across all
    languages), fallback (unknown lang → en, unknown key → the key), safe
    interpolation, and the users.language column round-trip."""
    import re
    from collections import Counter
    import i18n

    def names(s: str) -> set:
        return set(re.findall(r"{(\w+)}", s))

    def open_tags(s: str) -> Counter:
        return Counter(t.lower() for t in re.findall(r"<\s*([a-zA-Z][a-zA-Z0-9-]*)", s))

    def close_tags(s: str) -> Counter:
        return Counter(t.lower() for t in re.findall(r"</\s*([a-zA-Z][a-zA-Z0-9-]*)", s))

    en = i18n.TRANSLATIONS["en"]
    assert set(i18n.SUPPORTED) == set(i18n.TRANSLATIONS), "SUPPORTED vs TRANSLATIONS mismatch"
    for lang, cat in i18n.TRANSLATIONS.items():
        assert set(cat) == set(en), (
            f"{lang} key set differs: missing={set(en) - set(cat)} "
            f"extra={set(cat) - set(en)}"
        )
        for k, en_val in en.items():
            assert names(cat[k]) == names(en_val), (
                f"{lang}:{k} placeholders {names(cat[k])} != en {names(en_val)}"
            )
            # HTML-tag balance + parity: an unclosed/extra/diverging tag makes a
            # translation unsendable (Telegram rejects malformed HTML). Assert
            # each string is self-balanced AND uses the same tags as English.
            o, c = open_tags(cat[k]), close_tags(cat[k])
            assert o == c, f"{lang}:{k} unbalanced HTML tags: open={dict(o)} close={dict(c)}"
            assert o == open_tags(en_val), (
                f"{lang}:{k} HTML tags differ from en: {dict(o)} vs {dict(open_tags(en_val))}"
            )
    print(f"✓ i18n catalog: {len(i18n.TRANSLATIONS)} langs × {len(en)} keys, "
          f"parity + placeholders + HTML-tag balance OK")

    must_localize_keys = {
        "btn.shop",
        "btn.wallet",
        "btn.profile",
        "btn.support",
        "btn.history",
        "btn.language",
        "btn.download_txt",
        "btn.download_excel",
        "btn.download_yes",
        "btn.back_orders",
        "btn.refer",
        "btn.copy_link",
        "btn.api_link",
        "btn.api_regenerate",
        "btn.api_regenerate_yes",
        "sum.referral_discount",
        "download.confirm_title",
        "download.product",
        "download.order",
        "download.format",
        "download.items",
        "download.prompt",
        "download.sent",
        "refer.screen",
        "api.screen_new",
        "api.screen_existing",
        "api.regen_confirm",
        "api.regenerated",
        "start.welcome",
    }
    for lang in ("ar", "hi", "vi", "zh"):
        untranslated = [
            key for key in sorted(must_localize_keys)
            if i18n.TRANSLATIONS[lang][key] == en[key]
        ]
        assert not untranslated, f"{lang} untranslated buyer keys: {untranslated}"
    print("✓ i18n buyer buttons/screens are localized across non-English languages")

    # Fallback: unknown language resolves to English; unknown key returns itself.
    with i18n.use_language("xx"):
        assert i18n.current_language() == "en"
        assert i18n.t("btn.shop") == en["btn.shop"]
    with i18n.use_language("ar"):
        assert i18n.t("btn.shop") == i18n.TRANSLATIONS["ar"]["btn.shop"]
        assert i18n.t("no.such.key") == "no.such.key"
    # Contextvar resets to the default (en) outside any use_language block.
    assert i18n.current_language() == "en"
    print("✓ i18n fallback (lang→en, key→key)")

    # Interpolation works; a missing kwarg degrades to the raw template (no crash).
    with i18n.use_language("en"):
        assert i18n.t("btn.page", page=2, total=5) == "Page 2/5"
        assert i18n.t("btn.page") == en["btn.page"]
    print("✓ i18n interpolation + missing-arg safety")

    # users.language: NULL on create (→ picker), set_user_language persists it.
    u = await db.get_or_create_user(tg_id=4321, username="poly", first_name="Poly")
    assert u["language"] is None, "new users must start with NULL language"
    assert await db.set_user_language(4321, "vi") is True
    assert (await db.get_user_by_tg(4321))["language"] == "vi"
    print("✓ language column round-trips (NULL default, set_user_language persists)")


async def test_config_security_helpers():
    """config: is_admin + using_default_web_secrets (security-critical, were untested)."""
    from config import Settings
    strong = Settings(BOT_TOKEN="x", WEB_ADMIN_PASSWORD="a-strong-secret",
                      WEB_SESSION_SECRET="another-strong-secret", ADMIN_IDS=[111])
    assert strong.is_admin(111) is True
    assert strong.is_admin(222) is False
    assert strong.is_admin(None) is False
    assert strong.using_default_web_secrets() == []
    weak = Settings(BOT_TOKEN="x", WEB_ADMIN_PASSWORD="change-me-now",
                    WEB_SESSION_SECRET="change-me-too")
    assert weak.using_default_web_secrets() == ["WEB_ADMIN_PASSWORD", "WEB_SESSION_SECRET"]
    print("✓ config helpers: is_admin + using_default_web_secrets")


async def test_balance_rejects_non_finite():
    """review follow-up: adjust/set_user_balance must reject NaN/Inf so a crafted
    dashboard form can't persist an infinite wallet."""
    user = await db.get_or_create_user(tg_id=9911, username="fin", first_name="Fin")
    uid = int(user["id"])
    for bad in (float("inf"), float("-inf"), float("nan")):
        try:
            await db.adjust_user_balance(uid, bad)
            assert False, f"adjust_user_balance accepted {bad}"
        except ValueError:
            pass
        try:
            await db.set_user_balance(uid, bad)
            assert False, f"set_user_balance accepted {bad}"
        except ValueError:
            pass
    assert float((await db.get_user(uid))["balance"]) == 0.0
    print("✓ balance helpers reject NaN/Inf (no infinite-wallet corruption)")


async def test_order_overpay_surplus_credited():
    """review follow-up: when an order invoice is cent-bumped for uniqueness, the
    buyer pays a few cents over payable; confirm_and_settle must credit the
    surplus back to their wallet instead of silently overcharging."""
    user_id, pid = await _setup_user_and_product()
    order_id = await db.create_order(
        user_id=user_id, product_id=pid, quantity=1,
        unit_price=10.0, subtotal=10.0, coupon_code=None, discount=0.0,
        payable=10.0, status="awaiting_payment",
    )
    pay_id, _ = await db.create_payment(
        user_id=user_id, order_id=order_id, method="usdt_trc20", network="TRX",
        coin="USDT", base_amount=10.0, note="OVP", address="TX",
        binance_uid=None, ttl_seconds=600,
    )
    bal_before = float((await db.get_user(user_id))["balance"])
    # Buyer paid the bumped invoice amount (10.04) — 0.04 over the 10.00 payable.
    ev = await confirm_and_settle(await db.get_payment(pay_id), "0xovp", "{}", 10.04)
    assert ev and ev.get("contents"), ev
    assert ev.get("surplus_credited") == 0.04, ev
    bal_after = float((await db.get_user(user_id))["balance"])
    assert round(bal_after - bal_before, 2) == 0.04, (bal_before, bal_after)
    assert (await db.get_order(order_id))["status"] == "delivered"
    print("✓ order cent-bump surplus credited to wallet (no silent overcharge)")


async def test_deposits_series_excludes_order_payments():
    """review follow-up: deposits_series counts wallet top-ups (order_id IS NULL)
    only — a crypto payment that settled an order must NOT show as a deposit."""
    user_id, pid = await _setup_user_and_product()
    # A wallet top-up (order_id NULL).
    top_id, top_amt = await db.create_payment(
        user_id=user_id, order_id=None, method="usdt_trc20", network="TRX",
        coin="USDT", base_amount=12.0, note="TOP", address="TX",
        binance_uid=None, ttl_seconds=600,
    )
    assert await db.confirm_payment(top_id, "0xtop", "{}")
    # An order-settling crypto payment (order_id set) — must be excluded.
    order_id = await db.create_order(
        user_id=user_id, product_id=pid, quantity=1, unit_price=10.0, subtotal=10.0,
        coupon_code=None, discount=0.0, payable=10.0, status="awaiting_payment",
    )
    ord_id, _ = await db.create_payment(
        user_id=user_id, order_id=order_id, method="usdt_bep20", network="BSC",
        coin="USDT", base_amount=10.0, note="ORD", address="0x",
        binance_uid=None, ttl_seconds=600,
    )
    assert await db.confirm_payment(ord_id, "0xord", "{}")
    series = await db.deposits_series("day", 1)
    assert round(sum(series["values"]), 2) == round(float(top_amt), 2), series
    print("✓ deposits_series excludes order-settling payments (top-ups only)")


async def test_buyer_html_escapes_admin_product_prefix():
    """Admin-editable product emoji/prefix data must not break Telegram HTML."""
    from handlers.payment import _payment_text
    from handlers.profile import _order_card
    from handlers.shop import _product_card

    product = {
        "id": 1,
        "name": "HTML Safety",
        "emoji": "<bad&",
        "description": "",
        "features": "",
        "unit_label": "code",
        "base_price": 10.0,
        "delivery_instructions": "",
    }
    order = {
        "id": 10,
        "product_id": 1,
        "quantity": 1,
        "subtotal": 10.0,
        "discount": 0.0,
        "payable": 10.0,
        "status": "delivered",
        "created_at": int(time.time()),
        "product_name": "HTML Safety",
        "product_emoji": "<bad&",
    }
    payment = {
        "id": 20,
        "method": "binance_pay",
        "coin": "USDT",
        "unique_amount": 10.0,
        "address": "",
        "binance_uid": "123456",
    }

    rendered = "\n\n".join([
        _product_card(product, 5, []),
        _payment_text(payment, order, product),
        _order_card(order, []),
    ])
    assert "&lt;bad&amp;" in rendered or "<tg-emoji" in rendered
    assert "<bad&" not in rendered
    print("✓ buyer-facing HTML escapes admin product emoji/prefix data")


async def test_callback_int_handles_malformed_data():
    from handlers.security import callback_int

    class FakeCallback:
        def __init__(self, data):
            self.data = data
            self.answers: list[tuple[str, bool]] = []

        async def answer(self, text, show_alert=False):
            self.answers.append((text, show_alert))

    good = FakeCallback("pay:check:123")
    assert await callback_int(good, 2) == 123
    assert good.answers == []

    for raw in ("pay:check:not-an-int", "pay:check", None):
        bad = FakeCallback(raw)
        assert await callback_int(bad, 2) is None
        assert bad.answers and bad.answers[-1][1] is True
    print("✓ malformed callback integer fields are rejected without handler exceptions")


async def test_unmatched_deposit_legacy_schema_migrates():
    """Existing live DBs may have the old script-created unmatched_deposits
    table. init_db must upgrade it before code queries the new status fields."""
    async with db.get_db() as d:
        await d.execute("DROP INDEX IF EXISTS idx_unmatched_status")
        await d.execute("DROP TABLE IF EXISTS unmatched_deposits")
        await d.execute(
            """
            CREATE TABLE unmatched_deposits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                coin TEXT NOT NULL,
                network TEXT NOT NULL,
                amount REAL NOT NULL,
                txid TEXT NOT NULL UNIQUE,
                raw TEXT,
                created_at INTEGER NOT NULL,
                resolved INTEGER NOT NULL DEFAULT 0,
                resolved_user_id INTEGER,
                resolved_at INTEGER
            )
            """
        )
        now = int(time.time())
        await d.execute(
            "INSERT INTO unmatched_deposits "
            "(coin, network, amount, txid, raw, created_at, resolved) "
            "VALUES ('USDT', 'BINANCE_INTERNAL', 5, 'OLD_OPEN', '{}', ?, 0)",
            (now,),
        )
        await d.execute(
            "INSERT INTO unmatched_deposits "
            "(coin, network, amount, txid, raw, created_at, resolved, resolved_user_id, resolved_at) "
            "VALUES ('USDT', 'BINANCE_INTERNAL', 7, 'OLD_CREDITED', '{}', ?, 1, 42, ?)",
            (now, now),
        )
        await d.commit()

    await db.init_db()
    async with db.get_db() as d:
        cur = await d.execute("PRAGMA table_info(unmatched_deposits)")
        cols = {row[1] for row in await cur.fetchall()}
        assert {"status", "user_id", "resolved_at"}.issubset(cols)
        cur = await d.execute(
            "SELECT txid, status, user_id FROM unmatched_deposits ORDER BY id"
        )
        rows = await cur.fetchall()
    assert rows[0]["txid"] == "OLD_OPEN" and rows[0]["status"] == "open"
    assert rows[1]["txid"] == "OLD_CREDITED"
    assert rows[1]["status"] == "credited" and int(rows[1]["user_id"]) == 42
    assert await db.record_unmatched_deposit(
        "USDT", "BINANCE_INTERNAL", 9, "OLD_SCHEMA_NEW", "{}"
    )
    assert len(await db.open_unmatched_deposits()) == 2
    print("legacy unmatched_deposits table migrates in place")


async def main() -> int:
    # Each test creates its own user/product; init the DB once.
    await db.init_db()

    print("\n── happy paths ──")
    await test_happy_paths()

    # Reset DB between independent test groups by simply wiping rows.
    print("\n── concurrent reservations (fix #1) ──")
    await _reset_db()
    await test_concurrent_amount_reservations()

    print("\n── double-payment for same order (fix #2) ──")
    await _reset_db()
    await test_double_payment_for_same_order()

    print("\n── cancel siblings on method change ──")
    await _reset_db()
    await test_cancel_siblings_on_method_change()

    print("\n── payment switch preserves existing invoice on failure ──")
    await _reset_db()
    await test_create_order_payment_preserves_old_on_collision()

    print("\n── failed wallet payment preserves crypto invoice ──")
    await _reset_db()
    await test_wallet_insufficient_keeps_pending_crypto()

    print("\n── coupon used_count + max_uses (fix #3) ──")
    await _reset_db()
    await test_coupon_used_count_and_max_uses()

    print("\n── wallet pay status gate (fix #4) ──")
    await _reset_db()
    await test_pay_from_wallet_status_gate()

    print("\n── deliver_order status gate ──")
    await _reset_db()
    await test_deliver_order_status_gate()

    print("\n── cleanup stale drafts ──")
    await _reset_db()
    await test_cleanup_stale_drafts()

    print("\n── delete_product always hard-deletes ──")
    await _reset_db()
    await test_delete_product_always_hard()

    print("\n── Binance Pay matching ──")
    await _reset_db()
    await test_binance_pay_matching()

    print("\n── stale tx time window ──")
    await _reset_db()
    await test_rejects_stale_binance_pay_tx()

    print("\nTrust Wallet on-chain scanner settlement")
    await _reset_db()
    await test_trust_wallet_onchain_scanners_settle_topups()

    print("\n── code delivery chunking ──")
    await _reset_db()
    await test_code_delivery_chunking()

    print("\nbuyer order downloads")
    await _reset_db()
    await test_order_download_exports_and_keyboards()

    print("\nbuyer visual hype polish")
    await _reset_db()
    await test_buyer_visual_hype_polish()

    print("\nrefer & earn")
    await _reset_db()
    await test_refer_and_earn_feature()

    print("\nreseller API")
    await _reset_db()
    await test_reseller_api_key_bot_flow()
    await _reset_db()
    await test_reseller_api_http_flow()

    print("\nprofile current-tab callback")
    await _reset_db()
    await test_profile_current_tab_acknowledges_refresh()

    print("\n── coupon math ──")
    await _reset_db()
    await test_coupons_basic()

    print("\n── admin unlimited wallet + cost clear ──")
    await _reset_db()
    await test_admin_unlimited_wallet_and_cost_clear()

    print("\n── profit_for_period (cost + admin exclusion) ──")
    await _reset_db()
    await test_profit_for_period()

    print("\nprofit_for_period (negative net margin)")
    await _reset_db()
    await test_profit_for_period_negative_margin()

    print("\nprofit_breakdown reconciliation")
    await _reset_db()
    await test_profit_breakdown_matches_legacy()

    print("\nprofit_breakdown (coupon payable)")
    await _reset_db()
    await test_profit_uses_payable_not_subtotal()

    print("\nprofit_breakdown (null delivered_at)")
    await _reset_db()
    await test_profit_excludes_null_delivered_at()

    print("\nlist_profit_order_rows (statistics table)")
    await _reset_db()
    await test_list_profit_order_rows()

    print("\nuser blocked from buying")
    await _reset_db()
    await test_user_blocked_from_buying()

    print("\ncheckout spam rate limit")
    await _reset_db()
    await test_checkout_rate_limit_blocks_order_spam()

    print("\n── negative-quantity exploit blocked (fix C2) ──")
    await _reset_db()
    await test_negative_quantity_exploit_blocked()

    print("\n── credit_topup_once idempotent (fix H6) ──")
    await _reset_db()
    await test_credit_topup_once_idempotent()

    print("\n── reconcile unsettled payments (fix H6) ──")
    await _reset_db()
    await test_reconcile_unsettled()

    print("\n── deliver_order idempotent recovery (fix H6) ──")
    await _reset_db()
    await test_deliver_idempotent_recovery()

    print("\n── batch query helpers (perf) ──")
    await _reset_db()
    await test_batch_query_helpers()

    print("\n── support tickets (feature) ──")
    await _reset_db()
    await test_tickets()

    print("\n── sold-out auto-deactivate (feature) ──")
    await _reset_db()
    await test_sold_out_auto_deactivate()

    print("\n── analytics helpers (statistics tab) ──")
    await _reset_db()
    await test_analytics_helpers()

    print("\n── add_balance rejects negative ──")
    await _reset_db()
    await test_add_balance_rejects_negative()

    print("\n── add_stock dedup ──")
    await _reset_db()
    await test_add_stock_dedup()

    print("\n── coupon bump conditional (fix M4) ──")
    await _reset_db()
    await test_bump_coupon_conditional()

    print("\n── coupon value clamp ──")
    await _reset_db()
    await test_coupon_value_clamped()

    print("\n── i18n catalog + fallback + DB round-trip ──")
    await _reset_db()
    await test_i18n()

    print("\n── config security helpers ──")
    await _reset_db()
    await test_config_security_helpers()

    print("\n── balance helpers reject NaN/Inf ──")
    await _reset_db()
    await test_balance_rejects_non_finite()

    print("\n── order cent-bump surplus credited ──")
    await _reset_db()
    await test_order_overpay_surplus_credited()

    print("\n── deposits_series excludes order payments ──")
    await _reset_db()
    await test_deposits_series_excludes_order_payments()

    print("\nbuyer-facing HTML escaping")
    await _reset_db()
    await test_buyer_html_escapes_admin_product_prefix()

    print("\ncallback-data parsing")
    await _reset_db()
    await test_callback_int_handles_malformed_data()

    print("\nunmatched deposit legacy migration")
    await _reset_db()
    await test_unmatched_deposit_legacy_schema_migrates()

    print("\nunrelated deposit ignored once")
    await _reset_db()
    await test_unrelated_deposit_ignored_and_consumed_once()

    print("\nunmatched deposit manual credit")
    await _reset_db()
    await test_unmatched_deposit_credit_resolves_once()

    print("\nlate deposit recovered")
    await _reset_db()
    await test_late_deposit_recovered_to_wallet()

    print("\nambiguous late deposit flagged")
    await _reset_db()
    await test_late_deposit_ambiguous_is_flagged_not_credited()

    print("\nreused amount fresh active invoice")
    await _reset_db()
    await test_fresh_active_invoice_wins_reused_amount()

    print("\nrefund lookup calculates remaining window")
    await _reset_db()
    await test_refund_lookup_calculates_remaining_window()

    print("\nrefund lookup excludes expired and unfinished orders")
    await _reset_db()
    await test_refund_lookup_excludes_expired_and_unfinished_orders()

    print("\nrefund lookup does not expose stock contents")
    await _reset_db()
    await test_refund_lookup_never_exposes_stock_contents()

    print("\nrefund lookup accepts username and groups by product")
    await _reset_db()
    await test_refund_lookup_accepts_username_and_groups_by_product()

    print("\nrefund web route does not apply global fallback")
    await _reset_db()
    await test_refund_web_route_does_not_apply_global_fallback()

    print("\nrefund web route renders product breakdown")
    await _reset_db()
    await test_refund_web_route_renders_product_breakdown()

    if TEST_DB.exists():
        TEST_DB.unlink()
    print("\nAll smoke tests passed.")
    return 0


async def test_unrelated_deposit_ignored_and_consumed_once():
    """A verified deposit with no bot-created invoice is ignored. Its txid is
    still consumed so it cannot hijack a later invoice or alert repeatedly."""
    await _setup_user_and_product()
    txid = "PAY_ORPHAN_1"
    ev = await _recover_or_flag("USDT", "BINANCE_INTERNAL", 5.0, txid, "{}")
    assert ev is None, ev
    assert await db.txid_already_used(txid), "ignored txid must read as used"
    assert await db.record_unmatched_deposit("USDT", "BINANCE_INTERNAL", 5.0, txid, "{}") is None
    assert await db.open_unmatched_deposits() == []
    async with db.get_db() as d:
        cur = await d.execute(
            "SELECT status FROM unmatched_deposits WHERE txid = ?", (txid,),
        )
        row = await cur.fetchone()
    assert row and row["status"] == "ignored"
    print("✓ unrelated deposit ignored once + txid consumed")


async def test_unmatched_deposit_credit_resolves_once():
    """Admin one-tap credit lands in the right wallet, exactly once."""
    user = await db.get_or_create_user(tg_id=2002, username="carol", first_name="Carol")
    uid = int(user["id"])
    before = await db.get_balance(uid)
    dep_id = await db.record_unmatched_deposit("USDT", "BINANCE_INTERNAL", 7.5, "PAY_ORPHAN_2", "{}")
    assert dep_id
    credited, amount = await db.resolve_unmatched_deposit(dep_id, uid)
    assert credited and amount == 7.5, (credited, amount)
    assert await db.get_balance(uid) == round(before + 7.5, 4)
    # Double-tap / race must not double-credit.
    again, amt2 = await db.resolve_unmatched_deposit(dep_id, uid)
    assert not again and amt2 == 0.0
    assert await db.get_balance(uid) == round(before + 7.5, 4)
    print("✓ unmatched deposit credits the wallet exactly once")


async def test_late_deposit_recovered_to_wallet():
    """A deposit landing just after its top-up invoice expired is auto-credited
    to the correct user when it unambiguously maps to one expired invoice."""
    user = await db.get_or_create_user(tg_id=2003, username="dave", first_name="Dave")
    uid = int(user["id"])
    before = await db.get_balance(uid)
    pid, amt = await db.create_payment(
        user_id=uid, order_id=None, method="binance_pay",
        network="BINANCE_INTERNAL", coin="USDT", base_amount=9.0,
        note="LATE", address=None, binance_uid="x", ttl_seconds=-5,
    )
    expired = await db.expire_old_payments()
    assert any(int(e["id"]) == pid for e in expired), "invoice should have expired"
    ev = await _recover_or_flag("USDT", "BINANCE_INTERNAL", amt, "PAY_LATE_1", "{}")
    assert ev and ev.get("recovered") and int(ev["user_id"]) == uid, ev
    assert await db.get_balance(uid) == round(before + amt, 4)
    row = await db.get_payment(pid)
    assert row["status"] == "confirmed" and int(row["credited"]) == 1 and row["txid"] == "PAY_LATE_1"
    # txid consumed → a replayed poll cannot credit it again.
    assert await db.txid_already_used("PAY_LATE_1")
    print("✓ late deposit recovered to the right wallet, once")


async def test_late_deposit_ambiguous_is_flagged_not_credited():
    """Two expired invoices at the same amount = ambiguous → do NOT auto-credit;
    flag for manual review so we never credit the wrong user."""
    u1 = await db.get_or_create_user(tg_id=2004, username="erin", first_name="Erin")
    u2 = await db.get_or_create_user(tg_id=2005, username="finn", first_name="Finn")
    b1, b2 = await db.get_balance(int(u1["id"])), await db.get_balance(int(u2["id"]))
    # Expire each before creating the next so both land at the SAME amount (4.0):
    # the pending-uniqueness bump only disambiguates concurrently-pending rows.
    for u, note in ((u1, "AMB1"), (u2, "AMB2")):
        _pid, amt = await db.create_payment(
            user_id=int(u["id"]), order_id=None, method="binance_pay",
            network="BINANCE_INTERNAL", coin="USDT", base_amount=4.0,
            note=note, address=None, binance_uid="x", ttl_seconds=-5,
        )
        assert amt == 4.0, amt
        await db.expire_old_payments()
    assert await db.find_recently_expired_payment_by_amount(4.0, "USDT", "BINANCE_INTERNAL") is None
    ev = await _recover_or_flag("USDT", "BINANCE_INTERNAL", 4.0, "PAY_AMB_1", "{}")
    assert ev and ev.get("unmatched"), ev
    # Neither user was auto-credited.
    assert await db.get_balance(int(u1["id"])) == b1
    assert await db.get_balance(int(u2["id"])) == b2
    print("✓ ambiguous late deposit flagged for manual credit, not auto-credited")


async def test_fresh_active_invoice_wins_reused_amount():
    """If an expired invoice and a new active invoice share the amount, a
    transfer after the active invoice was created confirms the active invoice."""
    from config import get_settings

    old_user = await db.get_or_create_user(tg_id=2006, username="old", first_name="Old")
    new_user = await db.get_or_create_user(tg_id=2007, username="new", first_name="New")
    old_uid = int(old_user["id"])
    new_uid = int(new_user["id"])
    old_before = await db.get_balance(old_uid)
    new_before = await db.get_balance(new_uid)

    expired_pid, expired_amt = await db.create_payment(
        user_id=old_uid, order_id=None, method="binance_pay",
        network="BINANCE_INTERNAL", coin="USDT", base_amount=6.0,
        note="OLD", address=None, binance_uid="x", ttl_seconds=-5,
    )
    await db.expire_old_payments()
    active_pid, active_amt = await db.create_payment(
        user_id=new_uid, order_id=None, method="binance_pay",
        network="BINANCE_INTERNAL", coin="USDT", base_amount=6.0,
        note="NEW", address=None, binance_uid="x", ttl_seconds=600,
    )
    assert expired_amt == active_amt == 6.0
    active = await db.get_payment(active_pid)
    expired = await db.get_payment(expired_pid)
    shop_uid = get_settings().BINANCE_PAY_UID

    class FakeBinance:
        async def pay_transactions(self, *, limit: int, start_time_ms: int):
            return [{
                "transactionId": "PAY_REUSED_AMOUNT",
                "orderType": "C2C",
                "amount": "6.0",
                "currency": "USDT",
                "fundsDetail": [{"currency": "USDT", "amount": "6.0"}],
                "transactionTime": (int(active["created_at"]) + 1) * 1000,
                "receiverInfo": {"binanceId": shop_uid},
            }]

    events = await _poll_binance_pay(FakeBinance(), [active, expired])
    assert len(events) == 1 and events[0]["payment_id"] == active_pid, events
    assert (await db.get_payment(active_pid))["status"] == "confirmed"
    assert (await db.get_payment(expired_pid))["status"] == "expired"
    assert await db.get_balance(old_uid) == old_before
    assert await db.get_balance(new_uid) == round(new_before + 6.0, 4)
    assert await db.txid_already_used("PAY_REUSED_AMOUNT")
    print("✓ reused amount confirms the fresh active invoice")


async def test_trust_wallet_onchain_scanners_settle_topups():
    """TRC20/BEP20 scanners settle Trust Wallet address payments without using
    Binance deposit history. Binance Pay is covered separately above."""
    import services.payments as payments

    user = await db.get_or_create_user(tg_id=2008, username="chain", first_name="Chain")
    uid = int(user["id"])
    before = await db.get_balance(uid)

    old_tron = payments._fetch_tron_usdt_deposits
    old_bsc = payments._fetch_bsc_usdt_deposits
    old_trc_addr = payments.settings.USDT_TRC20_ADDRESS
    old_bep_addr = payments.settings.USDT_BEP20_ADDRESS
    try:
        payments.settings.USDT_TRC20_ADDRESS = "TRUST_TRC20_NEW"
        payments.settings.USDT_BEP20_ADDRESS = "0x887CeC206FD696829ec7a7ad344Acf5E7FacfA0d"
        trx_address = "TRUST_TRC20_PENDING"
        bsc_address = "0xd1addf1addc81981f95cd8fd198a047a91681826"
        trx_pid, trx_amt = await db.create_payment(
            user_id=uid, order_id=None, method="usdt_trc20",
            network="TRX", coin="USDT", base_amount=3.5,
            note="TRX", address=trx_address,
            binance_uid=None, ttl_seconds=600,
        )
        bsc_pid, bsc_amt = await db.create_payment(
            user_id=uid, order_id=None, method="usdt_bep20",
            network="BSC", coin="USDT", base_amount=1.3,
            note="BSC", address=bsc_address,
            binance_uid=None, ttl_seconds=600,
        )
        trx = await db.get_payment(trx_pid)
        bsc = await db.get_payment(bsc_pid)

        seen_fetch_addresses: list[str] = []

        async def fake_tron(min_created: int, address: str | None = None):
            seen_fetch_addresses.append(address or "")
            return [{
                "amount": str(trx_amt),
                "coin": "USDT",
                "network": "TRX",
                "address": address,
                "txId": "TRON_TRUST_1",
                "insertTime": (int(trx["created_at"]) + 1) * 1000,
            }]

        async def fake_bsc(min_created: int, address: str | None = None):
            seen_fetch_addresses.append(address or "")
            return [{
                "amount": str(bsc_amt),
                "coin": "USDT",
                "network": "BSC",
                "address": address,
                "txId": "BSC_TRUST_1",
                "insertTime": (int(bsc["created_at"]) + 1) * 1000,
            }]

        payments._fetch_tron_usdt_deposits = fake_tron
        payments._fetch_bsc_usdt_deposits = fake_bsc

        events = await _poll_onchain_deposits([trx, bsc])
        assert seen_fetch_addresses == [trx_address, bsc_address]
        assert {e["payment_id"] for e in events} == {trx_pid, bsc_pid}, events
        assert (await db.get_payment(trx_pid))["status"] == "confirmed"
        assert (await db.get_payment(bsc_pid))["status"] == "confirmed"
        assert await db.txid_already_used("TRON_TRUST_1")
        assert await db.txid_already_used("BSC_TRUST_1")
        assert await db.get_balance(uid) == round(before + trx_amt + bsc_amt, 4)
        print("✓ Trust Wallet TRC20/BEP20 scanner payments settle wallet top-ups")
    finally:
        payments._fetch_tron_usdt_deposits = old_tron
        payments._fetch_bsc_usdt_deposits = old_bsc
        payments.settings.USDT_TRC20_ADDRESS = old_trc_addr
        payments.settings.USDT_BEP20_ADDRESS = old_bep_addr


async def test_refund_lookup_calculates_remaining_window():
    """Refund calculator uses each product's warranty_days window."""
    user = await db.get_or_create_user(tg_id=3030, username="refund", first_name="Refund")
    uid = int(user["id"])
    pid = await db.create_product("Refundable Product", "R", "Demo", 25.0, warranty_days=365)
    six_month_pid = await db.create_product("Six Month Product", "S", "Demo", 18.0, warranty_days=180)
    now = int(time.time())

    fresh = await db.create_order(uid, pid, 1, 25.0, 25.0, None, 0.0, 25.0, status="paid")
    ten_days = await db.create_order(uid, pid, 1, 25.0, 25.0, None, 0.0, 25.0, status="paid")
    six_month = await db.create_order(uid, six_month_pid, 1, 18.0, 18.0, None, 0.0, 18.0, status="paid")
    async with db.get_db() as d:
        await d.execute(
            "UPDATE orders SET status='delivered', delivered_at=? WHERE id=?",
            (now, fresh),
        )
        await d.execute(
            "UPDATE orders SET status='delivered', delivered_at=? WHERE id=?",
            (now - 10 * 86400, ten_days),
        )
        await d.execute(
            "UPDATE orders SET status='delivered', delivered_at=? WHERE id=?",
            (now - 30 * 86400, six_month),
        )
        await d.commit()

    result = await refund_lookup_for_tg(3030, now=now)
    assert result["found_user"] is True
    by_id = {r["order_id"]: r for r in result["eligible_orders"]}
    assert by_id[fresh]["estimated_refund"] == 25.0
    assert by_id[fresh]["remaining_days"] == 365
    assert by_id[fresh]["warranty_days"] == 365
    assert by_id[ten_days]["estimated_refund"] == round(25.0 * 355 / 365, 2)
    assert by_id[ten_days]["remaining_days"] == 355
    assert by_id[six_month]["estimated_refund"] == 15.0
    assert by_id[six_month]["remaining_days"] == 150
    assert by_id[six_month]["warranty_days"] == 180
    print("✓ refund lookup prorates by each product's warranty_days")


async def test_refund_lookup_excludes_expired_and_unfinished_orders():
    user = await db.get_or_create_user(tg_id=3031, username="refund2", first_name="Refund2")
    uid = int(user["id"])
    pid = await db.create_product("Refundable Product 2", "R", "Demo", 50.0, warranty_days=25)
    no_warranty_pid = await db.create_product("No Warranty Product", "N", "Demo", 50.0)
    now = int(time.time())

    expired = await db.create_order(uid, pid, 1, 50.0, 50.0, None, 0.0, 50.0, status="paid")
    paid = await db.create_order(uid, pid, 1, 50.0, 50.0, None, 0.0, 50.0, status="paid")
    cancelled = await db.create_order(uid, pid, 1, 50.0, 50.0, None, 0.0, 50.0, status="cancelled")
    no_warranty = await db.create_order(uid, no_warranty_pid, 1, 50.0, 50.0, None, 0.0, 50.0, status="paid")
    async with db.get_db() as d:
        await d.execute(
            "UPDATE orders SET status='delivered', delivered_at=? WHERE id=?",
            (now - 26 * 86400, expired),
        )
        await d.execute(
            "UPDATE orders SET status='delivered', delivered_at=? WHERE id=?",
            (now, no_warranty),
        )
        await d.commit()

    result = await refund_lookup_for_tg(3031, now=now)
    assert result["eligible_orders"] == []
    expired_ids = {r["order_id"] for r in result["expired_orders"]}
    assert expired in expired_ids
    assert paid not in expired_ids and cancelled not in expired_ids and no_warranty not in expired_ids
    print("✓ refund lookup excludes expired, unfinished, and zero-warranty orders")


async def test_refund_lookup_never_exposes_stock_contents():
    user = await db.get_or_create_user(tg_id=3032, username="refund3", first_name="Refund3")
    uid = int(user["id"])
    pid = await db.create_product("Secret Product", "S", "Demo", 10.0, warranty_days=30)
    await db.add_stock_items(pid, ["SECRET-CODE-SHOULD-NOT-LEAK"])
    order_id = await db.create_order(uid, pid, 1, 10.0, 10.0, None, 0.0, 10.0, status="paid")
    await db.claim_stock_items(pid, 1, order_id)
    await db.mark_order_delivered(order_id)

    result = await refund_lookup_for_tg(3032, now=int(time.time()))
    serialized = repr(result)
    assert "SECRET-CODE-SHOULD-NOT-LEAK" not in serialized
    assert result["eligible_orders"][0]["product_name"] == "Secret Product"
    print("✓ refund lookup exposes order metadata, never stock contents")


async def test_refund_lookup_accepts_username_and_groups_by_product():
    """Buyer can enter @username instead of numeric ID. Multiple eligible
    orders for the same product collapse into one product-level refund line."""
    user = await db.get_or_create_user(tg_id=3033, username="refund_user", first_name="Refund4")
    uid = int(user["id"])
    pid = await db.create_product("Grouped Product", "G", "Demo", 99.0, warranty_days=365)
    other_pid = await db.create_product("Other Product", "O", "Demo", 5.0, warranty_days=365)
    now = int(time.time())

    first = await db.create_order(uid, pid, 2, 7.80, 15.60, None, 0.0, 15.60, status="paid")
    second = await db.create_order(uid, pid, 1, 5.00, 5.00, None, 0.0, 5.00, status="paid")
    other = await db.create_order(uid, other_pid, 1, 5.0, 5.0, None, 0.0, 5.0, status="paid")
    async with db.get_db() as d:
        for oid in (first, second, other):
            await d.execute(
                "UPDATE orders SET status='delivered', delivered_at=? WHERE id=?",
                (now, oid),
            )
        await d.commit()

    result = await refund_lookup_for_identifier("@refund_user", now=now)
    assert result["found_user"] is True
    assert result["lookup_value"] == "@refund_user"
    products = {p["product_name"]: p for p in result["eligible_products"]}
    assert set(products) == {"Grouped Product", "Other Product"}
    grouped = products["Grouped Product"]
    assert grouped["order_count"] == 2
    assert grouped["order_ids"] == [second, first] or grouped["order_ids"] == [first, second]
    assert grouped["quantity"] == 3
    assert grouped["payable"] == 20.60
    assert grouped["estimated_refund"] == 20.60
    assert grouped["warranty_days"] == 365
    assert len(grouped["orders"]) == 2
    order_sum = round(sum(o["estimated_refund"] for o in grouped["orders"]), 2)
    assert order_sum == grouped["estimated_refund"]
    assert {o["order_id"] for o in grouped["orders"]} == set(grouped["order_ids"])
    assert len(result["eligible_orders"]) == 3
    print("✓ refund lookup accepts @username and groups same-product historical prices")


async def test_refund_web_route_does_not_apply_global_fallback():
    """The public refund route must not pass the old global 25-day fallback.
    Products with warranty_days=0 stay hidden from the refund page."""
    from fastapi.testclient import TestClient
    from refund_app.app import create_app

    user = await db.get_or_create_user(tg_id=3034, username="refund_web", first_name="RefundWeb")
    uid = int(user["id"])
    pid = await db.create_product("Zero Warranty Product", "Z", "Demo", 10.0)
    order_id = await db.create_order(uid, pid, 1, 10.0, 10.0, None, 0.0, 10.0, status="paid")
    async with db.get_db() as d:
        await d.execute(
            "UPDATE orders SET status='delivered', delivered_at=? WHERE id=?",
            (int(time.time()), order_id),
        )
        await d.commit()

    client = TestClient(create_app())
    resp = client.post("/refund", data={"telegram_id": "3034"})
    assert resp.status_code == 200
    assert "Zero Warranty Product" not in resp.text
    assert "No eligible refunds right now" in resp.text
    print("✓ refund web route does not apply global fallback to zero-warranty products")


async def test_refund_web_route_renders_product_breakdown():
    """Eligible products show a per-order breakdown table on the refund page."""
    from fastapi.testclient import TestClient
    from refund_app.app import create_app

    user = await db.get_or_create_user(tg_id=3035, username="refund_breakdown", first_name="RefundBD")
    uid = int(user["id"])
    pid = await db.create_product("Breakdown Product", "B", "Demo", 12.0, warranty_days=25)
    order_id = await db.create_order(uid, pid, 1, 5.50, 5.50, None, 0.0, 5.50, status="paid")
    now = int(time.time()) - (2 * 86400)
    async with db.get_db() as d:
        await d.execute(
            "UPDATE orders SET status='delivered', delivered_at=? WHERE id=?",
            (now, order_id),
        )
        await d.commit()

    client = TestClient(create_app())
    resp = client.post("/refund", data={"telegram_id": "3035"})
    assert resp.status_code == 200
    text = resp.text
    assert "Show refund breakdown" in text
    assert "Each row: paid amount × remaining days ÷ warranty days." in text
    assert f"#{order_id}" in text
    assert "$5.50" in text
    assert "Breakdown Product" in text
    print("✓ refund web route renders per-product breakdown table")


async def _reset_db():
    """Truncate everything between test groups for isolation."""
    from db import get_db
    async with get_db() as d:
        for t in ("tickets", "events", "unmatched_deposits", "payments",
                  "api_orders", "referral_rewards", "orders", "stock_items",
                  "pricing_tiers", "coupons", "products",
                  "balance_adjustments", "api_keys", "users"):
            await d.execute(f"DELETE FROM {t}")
        await d.commit()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
