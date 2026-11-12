from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

import db
from config import get_settings
from i18n import t
from keyboards.buttons import button

router = Router(name="support")
settings = get_settings()


@router.callback_query(F.data == "menu:support")
async def cb_support(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    user = settings.SUPPORT_USERNAME.lstrip("@") or "admin"

    # Tickets are strictly order-linked — only offer "Open a ticket" to buyers
    # who actually have an order to report on. Everyone else just gets the
    # direct-message option, so nobody can open a ticket "for nothing".
    buyer = await db.get_user_by_tg(cb.from_user.id)
    has_orders = bool(buyer) and await db.user_has_orders(int(buyer["id"]))

    rows = []
    if has_orders:
        text = f"{t('support.title')}\n\n{t('support.with_orders')}"
        rows.append([button("btn.open_ticket", callback_data="ticket:pick")])
    else:
        text = f"{t('support.title')}\n\n{t('support.no_orders')}"
    rows.append([button("btn.message_us", url=f"https://t.me/{user}", user=user)])
    rows.append([button("btn.back_menu", callback_data="menu:home")])

    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await cb.answer()
