from __future__ import annotations

import logging

import db

log = logging.getLogger(__name__)


class OutOfStock(Exception):
    pass


class OrderNotPayable(Exception):
    """Raised when an order is in a status that can't be paid (already paid,
    cancelled, expired, delivered, failed)."""


async def deliver_order(order_id: int, *, clear_cost: bool = False) -> list[str]:
    """
    Claim stock items for an already-paid order. Returns delivered contents.
    Refuses to deliver an order whose status isn't 'paid' — this is the gate
    that prevents a second confirmed payment from re-claiming stock.

    `clear_cost` wipes the cost on the codes this order consumes (used for
    admin test orders so their stock never carries a counted cost).

    Raises:
      OutOfStock      — insufficient unsold stock at claim time
      OrderNotPayable — caller asked to deliver an order that isn't 'paid'
    """
    order = await db.get_order(order_id)
    if not order:
        raise ValueError(f"Order {order_id} not found")
    if order["status"] != "paid":
        raise OrderNotPayable(
            f"Order {order_id} is in status {order['status']!r}, not 'paid'."
        )
    # Idempotent recovery: if stock was ALREADY claimed for this order (a crash
    # between the claim and the status flip), finish without claiming again —
    # otherwise the startup reconcile sweep would hand out duplicate codes. (review H6)
    contents = await db.claimed_contents_for_order(order_id)
    if not contents:
        contents = await db.claim_stock_items(int(order["product_id"]),
                                              int(order["quantity"]), order_id)
        if not contents:
            await db.update_order(order_id, status="failed")
            raise OutOfStock(f"Out of stock for order {order_id}")
    # Flip paid→delivered and bump the order's coupon atomically (the bump now
    # lives inside mark_order_delivered so it can't be lost in a crash window
    # and fires exactly once per order). (review: coupon-bump atomicity)
    delivered_now = await db.mark_order_delivered(order_id)
    if clear_cost:
        await db.clear_stock_cost_for_order(order_id)

    if delivered_now and not clear_cost:
        try:
            from services.referrals import maybe_apply_order_referral_reward
            await maybe_apply_order_referral_reward(order_id)
        except Exception:
            log.exception("referral reward failed for order #%s (non-fatal)", order_id)

    # If that sale emptied the product's stock, auto-deactivate it (hide from
    # the shop) until the admin restocks and reactivates by choice. Best-effort
    # admin ping; never blocks delivery. (feature: sold-out auto-deactivate)
    try:
        if await db.deactivate_if_sold_out(int(order["product_id"])):
            await _notify_admins_sold_out(int(order["product_id"]))
    except Exception:
        log.exception("sold-out auto-deactivate failed (non-fatal)")

    return contents


async def _notify_admins_sold_out(product_id: int) -> None:
    """DM every admin that a product auto-deactivated because it sold out.
    Best-effort: the bot/config layer is lazy-imported (kept out of
    deliver_order's core so it stays testable). Skips quietly when no bot is
    running (tests/standalone); only real send failures are logged."""
    try:
        from bot import get_running_bot
        from config import get_settings
    except Exception:
        return  # bot/config layer unavailable (e.g. test harness) — skip
    bot = get_running_bot()
    if bot is None:
        return
    try:
        from html import escape
        product = await db.get_product(product_id)
        name = escape(str(product["name"])) if product else f"#{product_id}"
        text = (
            f"📦 <b>Sold out:</b> {name} is out of stock and was automatically "
            f"deactivated. Restock it, then tap <b>Activate</b> in the dashboard "
            f"to put it back on sale."
        )
        for tg_id in (get_settings().ADMIN_IDS or []):
            try:
                await bot.send_message(int(tg_id), text)
            except Exception:
                pass
    except Exception:
        log.exception("notify admins sold-out failed (non-fatal)")


async def pay_from_wallet(
    user_id: int, order_id: int, *, unlimited: bool = False
) -> tuple[list[str], float]:
    """
    Charge the user's wallet for the order's payable amount, transition order
    to 'paid', then deliver.

    Sequencing: debit wallet → transition order → deliver. We debit BEFORE
    transitioning so a crash between the two steps doesn't leave a 'paid'
    order with no actual payment recorded. If the
    transition loses a race to a concurrent crypto confirmation, we refund
    the debit before raising — same shape as the crypto-confirm refund path.

    `unlimited` (admin only) skips the balance debit entirely — the wallet is
    treated as bottomless and is never charged. Admin-consumed stock also has
    its cost cleared on delivery. Returns the amount actually charged (0 for
    an unlimited/admin wallet).

    Raises:
      OrderNotPayable     — order is in a status that can't be paid
      ValueError          — insufficient balance
      OutOfStock          — stock ran out between order and fulfilment
    """
    order = await db.get_order(order_id)
    if not order:
        raise ValueError(f"Order {order_id} not found")
    if order["status"] not in ("draft", "awaiting_payment"):
        raise OrderNotPayable(
            f"Order {order_id} cannot be paid (status={order['status']!r})."
        )
    payable = float(order["payable"])

    # 1. Debit balance first. If the user is short, the order and pending
    #    crypto invoices stay untouched. An unlimited (admin) wallet skips
    #    this — nothing is debited, so `charged` is 0 and refund paths below
    #    must not credit a debit that never happened.
    debited = False
    if not unlimited:
        if not await db.try_debit_balance(user_id, payable):
            raise ValueError("Insufficient balance")
        debited = True

    # 2. Transition draft|awaiting_payment -> paid. If a concurrent crypto
    #    confirmation beat us, refund the debit and raise.
    transitioned = await db.try_transition_order_status(
        order_id,
        from_statuses=("draft", "awaiting_payment"),
        to_status="paid",
        payment_method="wallet",
    )
    if not transitioned:
        if debited:
            await db.add_balance(user_id, payable)
        raise OrderNotPayable(
            f"Order {order_id} was settled by a concurrent payment; wallet refunded."
        )

    # 3. Deliver. If stock is gone, refund the wallet. Admin orders clear the
    #    consumed stock's cost.
    try:
        contents = await deliver_order(order_id, clear_cost=unlimited)
    except OutOfStock:
        if debited:
            await db.add_balance(user_id, payable)
        raise
    return contents, (0.0 if unlimited else payable)


async def confirm_and_settle(payment_row, txid: str, raw_json: str,
                              actual_amount: float, *,
                              is_admin_order: bool = False) -> dict | None:
    """
    Run the "deposit confirmed → either deliver order or credit wallet" pipeline
    with status gates that prevent double-delivery and double-credit.

    Returns an event dict describing what happened, or None if confirm_payment
    raced and lost. Kept in delivery.py (not payments.py) so it can be tested
    without the pydantic-settings config layer.

    `is_admin_order` (resolved by the caller, which has the config layer) makes
    the crypto-confirm path clear the consumed stock's cost on delivery, exactly
    like the unlimited wallet path — so admin handling is consistent across
    payment methods. (review M6)
    """
    ok = await db.confirm_payment(int(payment_row["id"]), txid, raw_json)
    if not ok:
        return None

    result: dict = {
        "payment_id": int(payment_row["id"]),
        "user_id": int(payment_row["user_id"]),
        "order_id": payment_row["order_id"],
        "amount": actual_amount,
        "coin": payment_row["coin"],
        "network": payment_row["network"],
        "txid": txid,
    }

    if payment_row["order_id"]:
        # Only the FIRST confirmed payment may move awaiting_payment → paid.
        # Subsequent payments lose the race and refund to wallet.
        transitioned = await db.try_transition_order_status(
            int(payment_row["order_id"]),
            from_statuses=("awaiting_payment", "draft"),
            to_status="paid",
            payment_method=payment_row["method"],
        )
        if not transitioned:
            await db.add_balance(int(payment_row["user_id"]), actual_amount)
            result["refunded"] = True
            result["refund_reason"] = "order_already_settled"
            return result

        try:
            contents = await deliver_order(int(payment_row["order_id"]),
                                           clear_cost=is_admin_order)
            result["contents"] = contents
            # The unique-amount cent bump (invoice disambiguation) can make the
            # buyer pay a few cents over the order's payable. Credit that surplus
            # back to their wallet so they're never silently overcharged. Admin
            # orders are charged 0 / excluded, so skip them. add_balance ignores
            # non-positive amounts, and we only credit a strictly positive delta.
            # (review: order invoice overpay)
            if not is_admin_order:
                settled = await db.get_order(int(payment_row["order_id"]))
                if settled:
                    surplus = round(actual_amount - float(settled["payable"]), 2)
                    if surplus > 0:
                        await db.add_balance(int(payment_row["user_id"]), surplus)
                        result["surplus_credited"] = surplus
        except OutOfStock:
            await db.add_balance(int(payment_row["user_id"]), actual_amount)
            result["refunded"] = True
            result["refund_reason"] = "out_of_stock"
        return result

    # Wallet top-up. Credit atomically + exactly once (credit_topup_once flips
    # the payment's `credited` flag in the same transaction as the balance
    # bump), so a crash can't split confirm from credit and the reconcile sweep
    # can re-run safely. The referrer credit lives in services.payments because
    # it touches settings; we lazy-import to keep this module config-free.
    credited = await db.credit_topup_once(
        int(payment_row["id"]), int(payment_row["user_id"]), actual_amount
    )
    if credited:
        try:
            from services.payments import maybe_credit_referrer
            await maybe_credit_referrer(int(payment_row["user_id"]), actual_amount)
        except Exception:
            log.exception("maybe_credit_referrer failed (non-fatal)")
    return result


async def reconcile_unsettled_payments() -> dict:
    """Startup recovery for the deposit-confirm crash window (review H6).

    Two idempotent fixes:
      - Confirmed wallet top-ups whose credit never applied (credited=0) are
        credited via credit_topup_once.
      - Orders stuck in 'paid' (payment confirmed but delivery never finished)
        are re-delivered via deliver_order, which is gated on status=='paid'
        and so is a no-op for anything already delivered.

    Safe to run on every boot. Returns a small summary for logging."""
    summary = {
        "topups_credited": 0,
        "orders_redelivered": 0,
        "referral_rewards": 0,
        "errors": 0,
    }

    for p in await db.confirmed_topups_uncredited():
        try:
            if await db.credit_topup_once(int(p["id"]), int(p["user_id"]),
                                          float(p["unique_amount"])):
                summary["topups_credited"] += 1
                # Mirror the live confirm path: a recovered FIRST deposit must
                # still pay the referrer bonus. credit_topup_once returns True
                # exactly once (it flips `credited`), so gating here is
                # idempotent across reboots. Without this, any deposit caught in
                # the confirm→credit crash window silently drops the referrer's
                # commission forever. (review: reconcile referrer)
                try:
                    from services.payments import maybe_credit_referrer
                    await maybe_credit_referrer(int(p["user_id"]),
                                                float(p["unique_amount"]))
                except Exception:
                    log.exception("reconcile: referrer credit failed for #%s", p["id"])
        except Exception:
            summary["errors"] += 1
            log.exception("reconcile: failed to credit top-up payment #%s", p["id"])

    for order in await db.stuck_paid_orders():
        try:
            await deliver_order(int(order["id"]))
            summary["orders_redelivered"] += 1
        except OutOfStock:
            log.warning("reconcile: order #%s stuck 'paid' but out of stock", order["id"])
        except OrderNotPayable:
            pass  # already moved on (delivered) between the query and now
        except Exception:
            summary["errors"] += 1
            log.exception("reconcile: failed to re-deliver order #%s", order["id"])

    try:
        from services.referrals import reconcile_referral_rewards
        summary["referral_rewards"] = await reconcile_referral_rewards()
    except Exception:
        summary["errors"] += 1
        log.exception("reconcile: referral rewards sweep failed")

    return summary
