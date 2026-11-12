from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import db
from i18n import t
from keyboards.refer import refer_keyboard
from services.referrals import (
    REFERRAL_MIN_SUBTOTAL_USDT,
    REFERRAL_REWARD_USDT,
    REFERRED_DISCOUNT_ORDERS,
    REFERRED_DISCOUNT_PCT,
    referral_link_for_tg,
)

router = Router(name="refer")


async def _refer_payload(from_user) -> tuple[str, str]:
    user = await db.get_or_create_user(
        int(from_user.id),
        getattr(from_user, "username", None),
        getattr(from_user, "first_name", None),
    )
    stats = await db.referral_stats(int(user["id"]))
    link = referral_link_for_tg(int(from_user.id))
    text = t(
        "refer.screen",
        earned=f"{float(stats['earned']):.2f}",
        referrals=int(stats["referrals"]),
        rewarded=int(stats["rewarded"]),
        pending=int(stats["pending"]),
        link=link,
        reward=f"{REFERRAL_REWARD_USDT:.0f}",
        discount=f"{REFERRED_DISCOUNT_PCT:.0f}",
        orders=REFERRED_DISCOUNT_ORDERS,
        min=f"{REFERRAL_MIN_SUBTOTAL_USDT:.0f}",
    )
    return text, link


@router.callback_query(F.data == "menu:refer")
async def cb_refer(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text, link = await _refer_payload(cb.from_user)
    await cb.message.edit_text(text, reply_markup=refer_keyboard(link))
    await cb.answer()


@router.message(Command("refer"))
async def cmd_refer(message: Message, state: FSMContext) -> None:
    await state.clear()
    text, link = await _refer_payload(message.from_user)
    await message.answer(text, reply_markup=refer_keyboard(link))
