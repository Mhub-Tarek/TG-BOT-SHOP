from __future__ import annotations

import logging
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery

import db
from config import PAYMENT_METADATA, get_settings
from handlers.security import (
    answer_if_blocked, callback_int, ensure_order_owner, ensure_payment_owner,
)
from i18n import t
from keyboards import hype_emoji as hype
from keyboards import icons as ic
from keyboards.main import back_to_menu
from keyboards.payment import (delivery_keyboard, download_confirm_keyboard,
                               payment_methods_keyboard,
                               payment_methods_text, pending_payment_keyboard)
from keyboards.shop import format_coin_amount
from services.delivery import OrderNotPayable, OutOfStock, pay_from_wallet
from services.order_exports import build_order_export, normalize_export_format
from services.payments import create_order_payment
from services.telegram_delivery import code_delivery_messages

router = Router(name="payment")
log = logging.getLogger(__name__)
settings = get_settings()


# ------------------- Payment method picker -------------------

@router.callback_query(F.data.startswith("pay:methods:"))
async def cb_payment_methods(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if await answer_if_blocked(cb):
        return
    order_id = await callback_int(cb, 2)
    if order_id is None:
        return
    order = await db.get_order(order_id)
    if not order or order["status"] not in ("draft", "awaiting_payment"):
        await cb.answer(t("common.order_closed"), show_alert=True)
        return
    if not await ensure_order_owner(cb, order):
        return
    product = await db.get_product(int(order["product_id"]))
    user = await db.get_user(int(order["user_id"]))
    balance = float(user["balance"]) if user else 0.0
    text = payment_methods_text(
        product_name=escape(product["name"]),
        quantity=int(order["quantity"]),
        subtotal=float(order["subtotal"]),
        discount=float(order["discount"]),
        payable=float(order["payable"]),
        wallet_balance=balance,
    )
    await cb.message.edit_text(text, reply_markup=payment_methods_keyboard(order_id))
    await cb.answer()


# ------------------- "Do" payment -------------------

@router.callback_query(F.data.startswith("pay:do:"))
async def cb_do_payment(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if await answer_if_blocked(cb):
        return
    parts = cb.data.split(":")
    order_id = await callback_int(cb, 2)
    if order_id is None:
        return
    if len(parts) < 4:
        await cb.answer(t("ticket.invalid"), show_alert=True)
        return
    method = parts[3]

    order = await db.get_order(order_id)
    if not order or order["status"] not in ("draft", "awaiting_payment"):
        await cb.answer(t("common.order_closed"), show_alert=True)
        return
    if not await ensure_order_owner(cb, order):
        return

    if method == "wallet":
        await _do_wallet_payment(cb, order)
        return

    if method not in settings.enabled_methods():
        await cb.answer(t("pay.disabled"), show_alert=True)
        return

    try:
        payment_id = await create_order_payment(
            user_id=int(order["user_id"]),
            order_id=order_id,
            method=method,
            amount=float(order["payable"]),
        )
    except RuntimeError as e:
        await cb.answer(str(e), show_alert=True)
        return
    transitioned = await db.try_transition_order_status(
        order_id,
        from_statuses=("draft", "awaiting_payment"),
        to_status="awaiting_payment",
        payment_method=method,
    )
    if not transitioned:
        await db.cancel_payment(payment_id)
        await cb.answer(t("common.order_closed"), show_alert=True)
        return
    await _render_payment_screen(cb, payment_id)


async def _do_wallet_payment(cb: CallbackQuery, order) -> None:
    user = await db.get_user(int(order["user_id"]))
    if not user:
        await cb.answer(t("pay.user_missing"), show_alert=True)
        return
    # Admin wallet is unlimited: never charged, and consumed stock cost cleared.
    # cb_do_payment already verified the presser owns this order via
    # ensure_order_owner, so the order owner == the authenticated actor. (review M5)
    is_admin = settings.is_admin(user["tg_id"])
    try:
        contents, charged = await pay_from_wallet(
            int(user["id"]), int(order["id"]), unlimited=is_admin
        )
    except OrderNotPayable:
        await cb.message.edit_text(
            t("pay.not_payable"),
            reply_markup=back_to_menu(),
        )
        await cb.answer()
        return
    except ValueError:
        await cb.message.edit_text(
            t("pay.insufficient"),
            reply_markup=back_to_menu(),
        )
        await cb.answer()
        return
    except OutOfStock:
        await cb.message.edit_text(
            t("pay.oos"),
            reply_markup=back_to_menu(),
        )
        await cb.answer()
        return

    product = await db.get_product(int(order["product_id"]))
    instructions = (product["delivery_instructions"] or "") if product else ""

    await cb.message.edit_text(
        t("pay.delivered", id=order['id'], charged=f"{charged:.2f}"),
        reply_markup=delivery_keyboard(int(order["id"])),
    )
    head = t("pay.codes_head", id=order['id'])
    for msg in code_delivery_messages(head, contents, instructions=instructions):
        await cb.message.answer(msg)
    await cb.answer(t("pay.delivered_alert"))


# ------------------- Pending-payment screen -------------------

def _payment_text(payment, order, product) -> str:
    method = payment["method"]
    coin = payment["coin"]
    amount = format_coin_amount(float(payment["unique_amount"]))
    address = payment["address"] or ""
    binance_uid = payment["binance_uid"] or ""
    prefix = f"{hype.product_icon_for(product, 1)} "
    payment_fallback = "\U0001f4b3"
    method_icon_id = ic.method_custom_emoji_id(method) or hype.custom_id_for_char(payment_fallback)
    method_title = escape(ic.method_button_text(method))
    method_head = (
        f"{ic.tg_emoji(method_icon_id, payment_fallback)} <b>{method_title}</b>"
        if method_icon_id else f"<b>{method_title}</b>"
    )

    lines = [
        method_head,
        "━━━━━━━━━━━━━━━━",
        f"<i>{t('pay.auto')}</i>",
        "",
        t("pay.product_line", prefix=prefix, name=escape(product['name'])),
        t("pay.qty_line", qty=order['quantity']),
        t("pay.order_total", payable=ic.money(float(order['payable']))),
        "",
        t("pay.send_exact", amount=amount, coin=coin),
    ]
    if binance_uid:
        lines.append(t("pay.uid", uid=escape(binance_uid)))
    if address:
        lines.append(t("pay.address", address=escape(address)))
    lines.append("")
    lines.append(t("pay.important"))
    lines.append(t("pay.send_exact_amount"))
    if method == "binance_pay":
        lines.append(t("pay.via_binance"))
    elif method in ("usdt_bep20", "usdt_trc20"):
        net = "BEP20 (BSC)" if method == "usdt_bep20" else "TRC20 (TRON)"
        lines.append(t("pay.use_network", net=net))
    lines.append(f"• {t('pay.expires', minutes=settings.PAYMENT_TTL_MINUTES)}")
    lines.append("")
    lines.append(t("pay.payment_id", id=payment['id']))
    lines.append(t("pay.auto_deliver"))
    return "\n".join(lines)


async def _render_payment_screen(cb: CallbackQuery, payment_id: int) -> None:
    payment = await db.get_payment(payment_id)
    if not payment:
        await cb.answer(t("pay.payment_not_found"), show_alert=True)
        return
    order = await db.get_order(int(payment["order_id"])) if payment["order_id"] else None
    if not order:
        await cb.answer(t("pay.order_missing"), show_alert=True)
        return
    product = await db.get_product(int(order["product_id"]))
    try:
        await cb.message.edit_text(
            _payment_text(payment, order, product),
            reply_markup=_pending_payment_markup(payment),
        )
    except TelegramBadRequest:
        await cb.message.answer(
            _payment_text(payment, order, product),
            reply_markup=_pending_payment_markup(payment),
        )
    await cb.answer()


def _pending_payment_markup(payment):
    return pending_payment_keyboard(
        int(payment["id"]),
        binance_uid=str(payment["binance_uid"] or "").strip(),
        address=str(payment["address"] or "").strip(),
        exact_amount=format_coin_amount(float(payment["unique_amount"])),
    )


# ------------------- Delivered order downloads -------------------

def _download_format_label(fmt: str) -> str:
    if fmt == "txt":
        return t("download.format_txt")
    return t("download.format_xlsx")


def _download_confirm_text(order, product, contents: list[str], fmt: str) -> str:
    name = escape(str(product["name"])) if product else f"Order #{order['id']}"
    return "\n".join([
        t("download.confirm_title"),
        "",
        t("download.product", name=name),
        t("download.order", id=order["id"]),
        t("download.format", format=_download_format_label(fmt)),
        t("download.items", n=len(contents)),
        "",
        t("download.prompt"),
    ])


async def _download_context(cb: CallbackQuery, order_id: int):
    order = await db.get_order(order_id)
    if not order:
        await cb.answer(t("common.order_not_found"), show_alert=True)
        return None
    if not await ensure_order_owner(cb, order):
        return None
    if order["status"] != "delivered":
        await cb.answer(t("download.not_delivered"), show_alert=True)
        return None
    contents = await db.claimed_contents_for_order(order_id)
    if not contents:
        await cb.answer(t("download.empty"), show_alert=True)
        return None
    product = await db.get_product(int(order["product_id"]))
    return order, product, contents


def _download_callback_parts(cb: CallbackQuery, *, action: str) -> tuple[int, str] | None:
    parts = (cb.data or "").split(":")
    if len(parts) != 4 or parts[1] != action:
        return None
    try:
        order_id = int(parts[2])
    except ValueError:
        return None
    fmt = normalize_export_format(parts[3])
    if fmt is None:
        return None
    return order_id, fmt


@router.callback_query(F.data.startswith("order:download:"))
async def cb_download_order(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    parsed = _download_callback_parts(cb, action="download")
    if parsed is None:
        await cb.answer(t("ticket.invalid"), show_alert=True)
        return
    order_id, fmt = parsed
    context = await _download_context(cb, order_id)
    if context is None:
        return
    order, product, contents = context
    await cb.message.answer(
        _download_confirm_text(order, product, contents, fmt),
        reply_markup=download_confirm_keyboard(order_id, fmt),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("order:download_send:"))
async def cb_download_order_send(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    parsed = _download_callback_parts(cb, action="download_send")
    if parsed is None:
        await cb.answer(t("ticket.invalid"), show_alert=True)
        return
    order_id, fmt = parsed
    context = await _download_context(cb, order_id)
    if context is None:
        return
    order, product, contents = context
    export = build_order_export(
        order=order,
        product=product,
        contents=contents,
        fmt=fmt,
    )
    name = escape(str(product["name"])) if product else f"Order #{order_id}"
    await cb.message.answer_document(
        BufferedInputFile(export.data, filename=export.filename),
        caption=t("download.caption", name=name, id=order_id, n=len(contents)),
    )
    await cb.answer(t("download.sent"))


@router.callback_query(F.data.startswith("order:download_cancel:"))
async def cb_download_order_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    try:
        await cb.message.edit_text(t("download.cancelled"))
    except TelegramBadRequest:
        pass
    await cb.answer()


@router.callback_query(F.data.startswith("pay:check:"))
async def cb_check(cb: CallbackQuery, state: FSMContext) -> None:
    payment_id = await callback_int(cb, 2)
    if payment_id is None:
        return
    payment = await db.get_payment(payment_id)
    if not payment:
        await cb.answer(t("pay.payment_not_found"), show_alert=True)
        return
    if not await ensure_payment_owner(cb, payment):
        return
    if payment["status"] == "confirmed":
        if payment["order_id"]:
            await cb.answer(t("pay.confirmed_order"), show_alert=True)
        else:
            await cb.answer(t("pay.confirmed_deposit"), show_alert=True)
        return
    if payment["status"] == "expired":
        await cb.message.edit_text(t("pay.expired"), reply_markup=back_to_menu())
        await cb.answer()
        return
    if payment["status"] == "cancelled":
        await cb.message.edit_text(t("pay.cancelled_msg"), reply_markup=back_to_menu())
        await cb.answer()
        return
    await cb.answer(t("pay.still_pending", seconds=settings.POLL_INTERVAL_SECONDS),
                    show_alert=True)


@router.callback_query(F.data.startswith("pay:cancel:"))
async def cb_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    payment_id = await callback_int(cb, 2)
    if payment_id is None:
        return
    payment = await db.get_payment(payment_id)
    if not payment:
        await cb.answer(t("pay.payment_not_found"), show_alert=True)
        return
    if not await ensure_payment_owner(cb, payment):
        return
    if payment and payment["order_id"]:
        await db.try_transition_order_status(
            int(payment["order_id"]),
            from_statuses=("draft", "awaiting_payment"),
            to_status="cancelled",
        )
    await db.cancel_payment(payment_id)
    await cb.message.edit_text(t("pay.cancelled_done"), reply_markup=back_to_menu())
    await cb.answer()
