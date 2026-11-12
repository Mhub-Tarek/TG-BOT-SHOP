from __future__ import annotations

import time
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import db
from handlers.security import callback_int, ensure_order_owner
from i18n import t
from keyboards import hype_emoji as hype
from keyboards import icons as ic
from keyboards.main import history_menu, history_order_menu, profile_menu

router = Router(name="profile")


# Tier thresholds in USDT-of-total-spend. Mirrors what reference shops like
# Insight Store show — gives returning users a visible reason to spend more.
# Keep the highest tier reachable; "Diamond" at $1k is achievable for any
# returning customer buying multi-month subscriptions.
_TIERS = (
    (1000.0, "Diamond",   "💎", "🌟"),
    (500.0,  "Platinum",  "🏆", "⭐"),
    (200.0,  "Gold",      "🥇", "✨"),
    (50.0,   "Silver",    "🥈", "✨"),
    (0.0,    "Bronze",    "🥉", "·"),
)


def _tier_for(spent: float) -> tuple[str, str, str]:
    """Return (label, badge_emoji, accent_emoji) for a spend total."""
    for threshold, label, badge, accent in _TIERS:
        if spent >= threshold:
            return label, badge, accent
    return _TIERS[-1][1], _TIERS[-1][2], _TIERS[-1][3]


def _next_tier(spent: float) -> tuple[str, float] | None:
    """Return (label, remaining_to_reach) for the next tier up, or None when
    already at the top."""
    eligible = [(t, l) for t, l, _, _ in _TIERS if t > spent]
    if not eligible:
        return None
    threshold, label = min(eligible, key=lambda x: x[0])
    return label, max(0.0, threshold - spent)


def _fmt_ts(ts: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(ts))


def _display_name(user, tg_user) -> str:
    username = (user["username"] if user else None) or tg_user.username
    if username:
        return f"@{escape(username.lstrip('@'))}"
    return escape(tg_user.first_name or "Customer")


async def _profile_text(tg_user) -> str:
    user = await db.get_user_by_tg(tg_user.id)
    if not user:
        user = await db.get_or_create_user(
            tg_user.id, tg_user.username, tg_user.first_name,
        )
    total_spent = await db.user_total_spent(int(user["id"]))
    tier_label, tier_badge, accent = _tier_for(total_spent)
    next_up = _next_tier(total_spent)
    next_line = ""
    if next_up:
        label, remaining = next_up
        next_line = t("profile.next_tier",
                      remaining=ic.money(remaining),
                      tier=t(f"tier.{label}"))
    tier_display = f"{tier_badge} {t(f'tier.{tier_label}')} {accent}".strip()

    return "\n".join([
        t("profile.title"),
        "",
        t("profile.name", name=_display_name(user, tg_user)),
        t("profile.tier", tier=tier_display),
        t("profile.balance", balance=ic.money(float(user['balance']))),
        t("profile.spent", spent=ic.money(total_spent)) + next_line,
    ])


# status -> emoji for the history cards (label is translated via status.* keys).
_STATUS_EMOJI = {
    "delivered":        "✅",
    "paid":             "💳",
    "awaiting_payment": "⏳",
    "draft":            "📝",
    "cancelled":        "✖️",
    "expired":          "⏰",
    "failed":           "⚠️",
}
_MAX_CODES_PER_ORDER = 4      # show a few delivered codes inline, summarise the rest


def _is_message_not_modified(exc: TelegramBadRequest) -> bool:
    return "message is not modified" in str(exc).lower()


def _order_card(o, codes: list[str]) -> str:
    """Render one order as a multi-line card (status, product, price, date, and
    the delivered codes inline for completed orders)."""
    status = o["status"]
    if status in _STATUS_EMOJI:
        emoji = _STATUS_EMOJI[status]
        label = t(f"status.{status}")
    else:
        emoji, label = "•", str(status).title()
    prod = f"{hype.product_icon_for(o, 1)} "
    lines = [
        f"{emoji} {t('history.order_num', id=o['id'])} {hype.sparkle()}",
        f"{prod}<b>{escape(o['product_name'])}</b> ×{o['quantity']}",
        f"💵 <b>{ic.money(float(o['payable']))}</b> · {label}",
        f"🗓 <i>{_fmt_ts(int(o['created_at']))}</i>",
    ]
    if codes:
        shown = codes[:_MAX_CODES_PER_ORDER]
        if len(shown) == 1:
            lines.append(t("history.delivered_inline", code=escape(shown[0])))
        else:
            lines.append(t("history.delivered_label"))
            lines.extend(f"   <code>{escape(c)}</code>" for c in shown)
        extra = len(codes) - len(shown)
        if extra > 0:
            lines.append(t("history.more_codes", n=extra))
    return "\n".join(lines)


async def _history_payload(tg_user) -> tuple[str, list]:
    user = await db.get_user_by_tg(tg_user.id)
    if not user:
        return t("common.start_first"), []
    orders = await db.recent_orders(int(user["id"]), limit=10)
    head = t("history.title")
    if not orders:
        return f"{head}\n\n{t('history.none')}", []
    return head, list(orders)


async def _history_text(tg_user) -> str:
    text, _orders = await _history_payload(tg_user)
    return text


def _order_with_product(order, product) -> dict:
    data = {key: order[key] for key in order.keys()}
    data["product_name"] = product["name"] if product else f"Order #{order['id']}"
    data["product_emoji"] = product["emoji"] if product else ""
    data["product_brand"] = (
        product["brand"] if product and "brand" in product.keys() else ""
    )
    data["product_icon_emoji_id"] = (
        product["icon_custom_emoji_id"]
        if product and "icon_custom_emoji_id" in product.keys()
        else ""
    )
    return data


@router.callback_query(F.data == "menu:profile")
async def cb_profile(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text = await _profile_text(cb.from_user)
    try:
        await cb.message.edit_text(text, reply_markup=profile_menu())
    except TelegramBadRequest as exc:
        if _is_message_not_modified(exc):
            await cb.answer(t("profile.refreshed"))
            return
        try:
            await cb.message.answer(text, reply_markup=profile_menu())
        except TelegramBadRequest:
            pass
    await cb.answer()


@router.callback_query(F.data == "menu:history")
async def cb_history(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text, orders = await _history_payload(cb.from_user)
    try:
        await cb.message.edit_text(text, reply_markup=history_menu(orders))
    except TelegramBadRequest:
        pass
    await cb.answer()


@router.callback_query(F.data.startswith("history:order:"))
async def cb_history_order(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    order_id = await callback_int(cb, 2, error_key="common.order_not_found")
    if order_id is None:
        return
    order = await db.get_order(order_id)
    if not order:
        await cb.answer(t("common.order_not_found"), show_alert=True)
        return
    if not await ensure_order_owner(cb, order):
        return

    product = await db.get_product(int(order["product_id"]))
    codes = []
    if order["status"] == "delivered":
        codes = await db.claimed_contents_for_order(order_id)
    text = _order_card(_order_with_product(order, product), codes)
    try:
        await cb.message.edit_text(
            text,
            reply_markup=history_order_menu(
                order_id,
                delivered=order["status"] == "delivered",
            ),
        )
    except TelegramBadRequest:
        pass
    await cb.answer()


@router.message(Command("profile"))
async def cmd_profile(message: Message, state: FSMContext) -> None:
    await state.clear()
    text = await _profile_text(message.from_user)
    await message.answer(text, reply_markup=profile_menu())
