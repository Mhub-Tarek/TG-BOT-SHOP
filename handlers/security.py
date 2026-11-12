from __future__ import annotations

from aiogram.types import CallbackQuery

import db
from i18n import t


async def callback_user(cb: CallbackQuery):
    """Resolve the Telegram callback sender to the internal user row."""
    if cb.message and cb.message.chat.type != "private":
        await cb.answer(t("common.private_only"), show_alert=True)
        return None
    user = await db.get_user_by_tg(cb.from_user.id)
    if not user:
        await cb.answer(t("common.start_first"), show_alert=True)
        return None
    return user


async def reject_if_blocked(tg_id: int) -> bool:
    """Return True if the user is blocked from purchasing."""
    return await db.is_user_blocked_by_tg(tg_id)


async def answer_if_blocked(cb: CallbackQuery) -> bool:
    """If blocked, alert the user and return True (caller should stop)."""
    if await reject_if_blocked(cb.from_user.id):
        await cb.answer(t("shop.blocked"), show_alert=True)
        return True
    return False


async def callback_int(cb: CallbackQuery, index: int, *, error_key: str = "ticket.invalid") -> int | None:
    """Parse one integer field from callback_data without throwing from handlers."""
    try:
        return int((cb.data or "").split(":")[index])
    except (ValueError, IndexError, TypeError):
        await cb.answer(t(error_key), show_alert=True)
        return None


async def ensure_order_owner(cb: CallbackQuery, order) -> bool:
    user = await callback_user(cb)
    if not user:
        return False
    if int(order["user_id"]) != int(user["id"]):
        await cb.answer(t("common.not_your_order"), show_alert=True)
        return False
    return True


async def ensure_payment_owner(cb: CallbackQuery, payment) -> bool:
    user = await callback_user(cb)
    if not user:
        return False
    if int(payment["user_id"]) != int(user["id"]):
        await cb.answer(t("common.not_your_payment"), show_alert=True)
        return False
    return True
