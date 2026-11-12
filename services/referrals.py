from __future__ import annotations

import logging

import db
from config import get_settings

log = logging.getLogger(__name__)

REFERRAL_REWARD_USDT = 2.0
REFERRED_DISCOUNT_PCT = 10.0
REFERRED_DISCOUNT_ORDERS = 3
REFERRAL_MIN_SUBTOTAL_USDT = 5.0
REFERRAL_FEATURE_STARTED_AT_KEY = "referral_feature_started_at"


async def feature_started_at() -> int:
    raw = await db.get_setting(REFERRAL_FEATURE_STARTED_AT_KEY)
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def referral_link_for_tg(tg_id: int) -> str:
    username = (get_settings().BOT_USERNAME or "").strip().lstrip("@")
    if not username:
        username = "Akunding_store_bot"
    return f"https://t.me/{username}?start=ref{int(tg_id)}"


async def referral_discount_for_user(user_id: int, subtotal: float) -> float:
    subtotal = round(float(subtotal or 0.0), 2)
    if subtotal <= 0:
        return 0.0
    user = await db.get_user(user_id)
    if not user or not user["referrer_id"]:
        return 0.0
    settings = get_settings()
    if settings.is_admin(int(user["tg_id"])):
        return 0.0
    started_at = await feature_started_at()
    if int(user["created_at"] or 0) < started_at:
        return 0.0
    used = await db.referral_discounted_order_count(user_id)
    if used >= REFERRED_DISCOUNT_ORDERS:
        return 0.0
    return round(subtotal * REFERRED_DISCOUNT_PCT / 100.0, 2)


async def maybe_apply_order_referral_reward(
    order_id: int,
    *,
    is_admin_order: bool = False,
) -> bool:
    if is_admin_order:
        return False
    order = await db.get_order(order_id)
    if not order or order["status"] != "delivered":
        return False
    user = await db.get_user(int(order["user_id"]))
    if not user or not user["referrer_id"]:
        return False
    if get_settings().is_admin(int(user["tg_id"])):
        return False
    started_at = await feature_started_at()
    if int(order["created_at"] or 0) < started_at:
        return False
    if int(user["created_at"] or 0) < started_at:
        return False
    if float(order["subtotal"] or 0.0) < REFERRAL_MIN_SUBTOTAL_USDT:
        return False
    referrer_id = int(user["referrer_id"])
    referred_id = int(user["id"])
    if referrer_id == referred_id:
        return False
    paid = await db.create_referral_reward_once(
        referrer_id=referrer_id,
        referred_id=referred_id,
        order_id=order_id,
        amount=REFERRAL_REWARD_USDT,
    )
    if paid:
        log.info(
            "referral reward credited: referrer=%s referred=%s order=%s amount=%.2f",
            referrer_id,
            referred_id,
            order_id,
            REFERRAL_REWARD_USDT,
        )
    return paid


async def reconcile_referral_rewards() -> int:
    started_at = await feature_started_at()
    rows = await db.referral_reward_reconcile_candidates(
        started_at=started_at,
        min_subtotal=REFERRAL_MIN_SUBTOTAL_USDT,
    )
    settings = get_settings()
    credited = 0
    for row in rows:
        if settings.is_admin(int(row["buyer_tg_id"])):
            continue
        try:
            if await maybe_apply_order_referral_reward(int(row["id"])):
                credited += 1
        except Exception:
            log.exception("reconcile: referral reward failed for order #%s", row["id"])
    return credited
