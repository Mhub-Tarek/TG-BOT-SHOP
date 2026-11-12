"""
Wallet top-up flow. Lets users add funds to their on-shop balance using the
same crypto methods used at checkout.
"""
from __future__ import annotations

import math
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import db
from config import PAYMENT_METADATA, get_settings
from handlers.security import callback_user
from i18n import t
from keyboards import icons as ic
from keyboards.main import back_to_menu, wallet_menu
from keyboards.payment import (deposit_methods_keyboard, deposit_methods_text,
                               pending_payment_keyboard)
from keyboards.shop import format_coin_amount
from services.payments import create_topup_payment
from states import DepositFlow

router = Router(name="deposit")
settings = get_settings()


async def _show_deposit_method_picker(target, *, amount: float, tg_id: int,
                                      edit: bool = False) -> None:
    user = await db.get_user_by_tg(tg_id)
    balance = float(user["balance"]) if user else 0.0
    text = deposit_methods_text(amount=amount, wallet_balance=balance)
    markup = deposit_methods_keyboard()
    if edit and hasattr(target, "edit_text"):
        await target.edit_text(text, reply_markup=markup)
    else:
        await target.answer(text, reply_markup=markup)


async def _wallet_text(tg_id: int) -> str:
    user = await db.get_user_by_tg(tg_id)
    if not user:
        user = await db.get_or_create_user(tg_id, None, None)
    balance = float(user["balance"])
    # Admin wallet is unlimited (never charged), so show ∞ instead of the
    # stored figure — consistent with the checkout summary.
    if settings.is_admin(tg_id):
        balance_line = t("deposit.balance_admin")
    else:
        balance_line = t("deposit.balance", balance=ic.money(balance))
    return "\n".join([
        t("deposit.wallet_title"),
        "",
        balance_line,
        "",
        t("deposit.tap_deposit"),
    ])


@router.callback_query(F.data == "menu:wallet")
async def cb_wallet(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text = await _wallet_text(cb.from_user.id)
    await cb.message.edit_text(text, reply_markup=wallet_menu())
    await cb.answer()


def _deposit_prompt_text() -> str:
    # MAX_DEPOSIT_USDT is still enforced in msg_amount below; we just don't
    # surface the ceiling to users — there's no value in showing it.
    return t("deposit.prompt", min=ic.money(settings.MIN_DEPOSIT_USDT))


@router.callback_query(F.data == "menu:deposit")
async def cb_deposit(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(DepositFlow.waiting_amount)
    await cb.message.edit_text(_deposit_prompt_text(), reply_markup=back_to_menu())
    await db.log_event(cb.from_user.id, "deposit_start")  # funnel (analytics)
    await cb.answer()


@router.message(Command("deposit"))
async def cmd_deposit(message: Message, state: FSMContext) -> None:
    await state.set_state(DepositFlow.waiting_amount)
    await message.answer(_deposit_prompt_text(), reply_markup=back_to_menu())


@router.message(DepositFlow.waiting_amount)
async def msg_amount(message: Message, state: FSMContext) -> None:
    try:
        amount = round(float(message.text.strip().replace(",", ".")), 2)
        # Reject NaN/inf explicitly: `nan < MIN` and `nan > MAX` are both False,
        # so a NaN would otherwise slip past both bounds into a malformed
        # pending payment row. (review L)
        if not math.isfinite(amount) or amount < settings.MIN_DEPOSIT_USDT:
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer(
            t("deposit.min_err", min=ic.money(settings.MIN_DEPOSIT_USDT)),
        )
        return
    if amount > settings.MAX_DEPOSIT_USDT:
        # Cap protects the pending-amount unique index: an absurdly large deposit
        # could otherwise sit reserving a round amount for the full TTL.
        await message.answer(
            t("deposit.max_err", max=ic.money(settings.MAX_DEPOSIT_USDT)),
        )
        return
    await state.update_data(amount=amount)
    await state.set_state(DepositFlow.waiting_method)
    await _show_deposit_method_picker(message, amount=amount, tg_id=message.from_user.id)


@router.callback_query(F.data.startswith("dep:do:"))
async def cb_do_topup(cb: CallbackQuery, state: FSMContext) -> None:
    try:
        method = (cb.data or "").split(":")[2]
    except IndexError:
        await cb.answer(t("ticket.invalid"), show_alert=True)
        return
    if await state.get_state() != DepositFlow.waiting_method.state:
        await cb.answer(t("deposit.start_over"), show_alert=True)
        return
    if method == "wallet" or method not in settings.enabled_methods():
        await cb.answer(t("pay.disabled"), show_alert=True)
        return
    data = await state.get_data()
    amount = float(data.get("amount", 0))
    if not amount:
        await cb.answer(t("deposit.start_over"), show_alert=True)
        return
    user = await callback_user(cb)
    if not user:
        return

    try:
        payment_id = await create_topup_payment(
            user_id=int(user["id"]), method=method, amount=amount,
        )
    except RuntimeError as e:
        await cb.answer(str(e), show_alert=True)
        return
    payment = await db.get_payment(payment_id)
    await state.clear()
    await cb.message.edit_text(_topup_text(payment),
                                reply_markup=_pending_payment_markup(payment))
    await cb.answer()


def _topup_text(payment) -> str:
    meta = PAYMENT_METADATA[payment["method"]]
    amount = format_coin_amount(float(payment["unique_amount"]))
    address = payment["address"] or ""
    binance_uid = payment["binance_uid"] or ""
    coin = payment["coin"]
    lines = [
        t("topup.title", method=ic.method_label(payment['method'])),
        f"<i>{t('pay.auto')}</i>",
        "",
        t("pay.send_exact", amount=amount, coin=coin),
    ]
    if binance_uid:
        lines.append(t("pay.uid", uid=escape(binance_uid)))
    if address:
        lines.append(t("pay.address", address=escape(address)))
    lines.append("")
    lines.append(t("pay.expires", minutes=settings.PAYMENT_TTL_MINUTES))
    lines.append(t("pay.payment_id", id=payment['id']))
    return "\n".join(lines)


def _pending_payment_markup(payment):
    return pending_payment_keyboard(
        int(payment["id"]),
        binance_uid=str(payment["binance_uid"] or "").strip(),
        address=str(payment["address"] or "").strip(),
        exact_amount=format_coin_amount(float(payment["unique_amount"])),
    )
