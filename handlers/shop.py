from __future__ import annotations

import asyncio
import time
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import db
from config import get_settings
from handlers.security import (
    answer_if_blocked, callback_int, ensure_order_owner, reject_if_blocked,
)
from i18n import t
from keyboards import hype_emoji as hype
from keyboards.main import back_to_menu
from keyboards.shop import (PAGE_SIZE, QTY_PRESETS, checkout_summary_keyboard,
                            product_detail_keyboard, products_keyboard,
                            quantity_keyboard)
from services.pricing import effective_unit_price, format_tier_summary
from states import CheckoutFlow

router = Router(name="shop")
settings = get_settings()


# ------------------- Product list (paginated) -------------------

async def _render_shop_page(target, page: int, edit: bool) -> None:
    total = await db.count_active_products()
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    products = await db.list_products(active_only=True, offset=page * PAGE_SIZE,
                                      limit=PAGE_SIZE)
    head = t("shop.list_title")
    if not products:
        text = f"{head}\n\n{t('shop.none')}"
        if edit:
            await target.edit_text(text, reply_markup=back_to_menu())
        else:
            await target.answer(text, reply_markup=back_to_menu())
        return

    # One grouped query for all products on the page instead of one per product. (perf)
    stock_counts = await db.stock_counts([int(p["id"]) for p in products])
    if total_pages > 1:
        text = f"{head}\n\n{t('shop.tap_page', page=page + 1, total=total_pages)}"
    else:
        text = f"{head}\n\n{t('shop.tap')}"
    kb = products_keyboard(products, stock_counts, page=page, total_pages=total_pages)
    if edit:
        try:
            await target.edit_text(text, reply_markup=kb)
        except TelegramBadRequest:
            # "message is not modified" when user taps Refresh and nothing changed
            pass
    else:
        await target.answer(text, reply_markup=kb)


@router.callback_query(F.data == "menu:shop")
async def cb_shop(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _render_shop_page(cb.message, 0, edit=True)
    await db.log_event(cb.from_user.id, "shop_view")  # funnel (analytics)
    await cb.answer()


@router.callback_query(F.data.startswith("shop:page:"))
async def cb_shop_page(cb: CallbackQuery, state: FSMContext) -> None:
    page = await callback_int(cb, 2)
    if page is None:
        return
    await state.clear()
    await _render_shop_page(cb.message, page, edit=True)
    await cb.answer()


@router.message(Command("shop"))
async def cmd_shop(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _render_shop_page(message, 0, edit=False)
    await db.log_event(message.from_user.id, "shop_view")  # funnel (analytics)


# ------------------- Product detail card -------------------

def _features_block(product) -> str:
    raw = (product["features"] or "").strip() or settings.DEFAULT_DELIVERY_PROMISE
    # Render as a single block-quote-like card
    lines = [escape(ln.strip()) for ln in raw.splitlines() if ln.strip()]
    if not lines:
        return ""
    inner = "\n".join(f"  {ln}" for ln in lines)
    return f"<blockquote>{inner}</blockquote>"


def _product_card(product, stock_n: int, tiers) -> str:
    icon = hype.product_icon_for(product, stock_n)
    head = f"{icon} <b>{escape(product['name'])}</b> {hype.sparkle()}"
    unit = product["unit_label"] or "code"
    body = [
        head,
        "━━━━━━━━━━━━━━━━",
        "",
        t("shop.card_price", price=f"{float(product['base_price']):.2f}", unit=escape(unit)),
    ]
    feats = _features_block(product)
    if feats:
        body.append("")
        body.append(feats)
    body.append("")
    body.append(t("shop.card_stock", n=stock_n))
    desc = (product["description"] or "").strip()
    if desc:
        body.append("")
        body.append(escape(desc))
    if tiers:
        body.append("")
        body.append(t("shop.card_bulk"))
        body.append(format_tier_summary(float(product['base_price']), tiers, unit))
    body.append("")
    body.append(t("shop.card_auto"))
    return "\n".join(body)


@router.callback_query(F.data.startswith("prod:"))
async def cb_product(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    product_id = await callback_int(cb, 1)
    if product_id is None:
        return
    product = await db.get_product(product_id)
    if not product or not product["active"]:
        await cb.answer(t("shop.not_found"), show_alert=True)
        return
    tiers = await db.get_pricing_tiers(product_id)
    stock_n = await db.stock_count(product_id)
    await cb.message.edit_text(
        _product_card(product, stock_n, tiers),
        reply_markup=product_detail_keyboard(product_id, in_stock=stock_n > 0),
    )
    await db.log_event(cb.from_user.id, "product_view", product_id)  # funnel (analytics)
    await cb.answer()


# ------------------- Quantity picker -------------------

BUY_LOADING_FLASH_SECONDS = 0.45


def _buy_loading_flash_text() -> str:
    return hype.loading_flash()


async def _flash_buy_loading(message: Message) -> None:
    try:
        await message.edit_text(_buy_loading_flash_text())
        await asyncio.sleep(BUY_LOADING_FLASH_SECONDS)
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("buy:"))
async def cb_buy(cb: CallbackQuery, state: FSMContext) -> None:
    if await answer_if_blocked(cb):
        return
    product_id = await callback_int(cb, 1)
    if product_id is None:
        return
    product = await db.get_product(product_id)
    if not product or not product["active"]:
        await cb.answer(t("shop.not_found"), show_alert=True)
        return
    await _flash_buy_loading(cb.message)
    unit = product["unit_label"] or "code"
    text = t("shop.buy_header",
             name=escape(product['name']),
             price=f"{float(product['base_price']):.2f}",
             unit=escape(unit))
    await cb.message.edit_text(text, reply_markup=quantity_keyboard(product_id))
    await cb.answer()


@router.callback_query(F.data.startswith("qtyc:"))
async def cb_custom_qty(cb: CallbackQuery, state: FSMContext) -> None:
    if await answer_if_blocked(cb):
        return
    product_id = await callback_int(cb, 1)
    if product_id is None:
        return
    await state.set_state(CheckoutFlow.waiting_custom_quantity)
    await state.update_data(product_id=product_id)
    await cb.message.answer(t("shop.ask_qty"))
    await cb.answer()


@router.message(CheckoutFlow.waiting_custom_quantity)
async def msg_custom_qty(message: Message, state: FSMContext) -> None:
    if await reject_if_blocked(message.from_user.id):
        await message.answer(t("shop.blocked"))
        await state.clear()
        return
    try:
        qty = int(message.text.strip())
        if qty < 1 or qty > 1000:
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer(t("shop.qty_whole"))
        return
    data = await state.get_data()
    product_id = int(data["product_id"])
    await state.clear()
    await _create_and_show_order(message, message.from_user.id, product_id, qty)


QTY_MIN = 1
QTY_MAX = 1000
CHECKOUT_RATE_LIMIT_WINDOW_SECONDS = 10 * 60
CHECKOUT_RATE_LIMIT_MAX_ATTEMPTS = 8


@router.callback_query(F.data.startswith("qty:"))
async def cb_pick_qty(cb: CallbackQuery, state: FSMContext) -> None:
    if await answer_if_blocked(cb):
        return
    # callback_data is client-controlled: a crafted `qty:<pid>:-5` would
    # otherwise reach _create_and_show_order with a negative quantity, creating
    # an order with a NEGATIVE payable (which try_debit_balance would turn into
    # a wallet CREDIT) and a negative claim LIMIT (which drains all stock).
    # Validate the range at the boundary — the critical fix. (review C2)
    try:
        _, pid, qty = cb.data.split(":")
        product_id = int(pid)
        quantity = int(qty)
    except (ValueError, IndexError):
        await cb.answer(t("ticket.invalid"), show_alert=True)
        return
    if quantity < QTY_MIN or quantity > QTY_MAX:
        await cb.answer(t("shop.qty_range", min=QTY_MIN, max=QTY_MAX),
                        show_alert=True)
        return
    await state.clear()
    await _create_and_show_order(cb.message, cb.from_user.id, product_id, quantity,
                                 edit=True)
    await cb.answer()


# ------------------- Create-or-show order summary -------------------

async def _checkout_rate_limited(user_id: int, tg_id: int) -> int:
    """Return wait seconds when a buyer is creating unpaid orders too fast."""
    if settings.is_admin(tg_id):
        return 0
    now = int(time.time())
    stats = await db.checkout_attempt_stats(
        user_id,
        since_ts=now - CHECKOUT_RATE_LIMIT_WINDOW_SECONDS,
    )
    if stats["count"] < CHECKOUT_RATE_LIMIT_MAX_ATTEMPTS:
        return 0
    oldest = int(stats["oldest"] or now)
    return max(1, oldest + CHECKOUT_RATE_LIMIT_WINDOW_SECONDS - now)


async def _create_and_show_order(target_message, tg_id: int, product_id: int,
                                 quantity: int, edit: bool = False) -> None:
    # Defense-in-depth: never create an order with an out-of-range quantity,
    # regardless of which path called us. (review C2)
    if await reject_if_blocked(tg_id):
        await target_message.answer(t("shop.blocked"))
        return
    if not isinstance(quantity, int) or quantity < QTY_MIN or quantity > QTY_MAX:
        await target_message.answer(
            t("shop.qty_range", min=QTY_MIN, max=QTY_MAX))
        return
    product = await db.get_product(product_id)
    if not product:
        await target_message.answer(t("shop.product_gone"))
        return
    # A deactivated product must not be purchasable, even via a stale id from an
    # old broadcast deep-link or chat message. (review M3)
    if not product["active"]:
        await target_message.answer(t("shop.unavailable"))
        return
    user = await db.get_user_by_tg(tg_id)
    if not user:
        await target_message.answer(t("common.start_first"))
        return

    wait_seconds = await _checkout_rate_limited(int(user["id"]), tg_id)
    if wait_seconds:
        wait_minutes = max(1, (wait_seconds + 59) // 60)
        msg = t("shop.rate_limited", minutes=wait_minutes)
        if edit:
            try:
                await target_message.edit_text(msg, reply_markup=back_to_menu())
            except TelegramBadRequest:
                await target_message.answer(msg, reply_markup=back_to_menu())
        else:
            await target_message.answer(msg, reply_markup=back_to_menu())
        return

    stock_n = await db.stock_count(product_id)
    if stock_n < quantity:
        msg = t("shop.only_n", n=stock_n)
        if edit:
            await target_message.edit_text(msg, reply_markup=back_to_menu())
        else:
            await target_message.answer(msg, reply_markup=back_to_menu())
        return

    tiers = await db.get_pricing_tiers(product_id)
    unit, _ = effective_unit_price(float(product["base_price"]), quantity, tiers)
    subtotal = round(unit * quantity, 2)
    from services.referrals import referral_discount_for_user
    referral_discount = await referral_discount_for_user(int(user["id"]), subtotal)
    payable = round(max(0.0, subtotal - referral_discount), 2)

    order_id = await db.create_order(
        user_id=int(user["id"]),
        product_id=product_id,
        quantity=quantity,
        unit_price=unit,
        subtotal=subtotal,
        coupon_code=None,
        discount=referral_discount,
        payable=payable,
        status="draft",
        referral_discount=referral_discount,
    )
    await _render_order_summary(target_message, order_id, edit=edit)


async def _render_order_summary(target_message, order_id: int, edit: bool) -> None:
    order = await db.get_order(order_id)
    if not order:
        return
    product = await db.get_product(int(order["product_id"]))
    user = await db.get_user(int(order["user_id"]))
    balance = float(user["balance"]) if user else 0.0
    is_admin = bool(user) and settings.is_admin(user["tg_id"])
    unit = product["unit_label"] or "code"
    icon = hype.product_icon_for(product, 1)

    lines = [
        f"{icon} <b>{escape(product['name'])}</b> 🛒",
        "━━━━━━━━━━━━━━━━",
        "",
        t("sum.qty", qty=order['quantity'], unit=escape(unit)),
        t("sum.subtotal", subtotal=f"{float(order['subtotal']):.2f}"),
    ]
    referral_discount = float(order["referral_discount"] or 0.0)
    if referral_discount > 0:
        lines.append(t("sum.referral_discount", discount=f"{referral_discount:.2f}"))
    elif float(order["discount"]) > 0:
        lines.append(t("sum.discount", discount=f"{float(order['discount']):.2f}"))
    lines.append(t("sum.payable", payable=f"{float(order['payable']):.2f}"))
    lines.append("")
    if is_admin:
        lines.append(t("sum.wallet_admin"))
    else:
        lines.append(t("sum.wallet", balance=f"{balance:.2f}"))
        if balance < float(order['payable']):
            short = round(float(order['payable']) - balance, 2)
            lines.append(t("sum.short", short=f"{short:.2f}"))
    lines.append("")
    lines.append(t("sum.choose"))

    kb = checkout_summary_keyboard(order_id)
    if edit:
        try:
            await target_message.edit_text("\n".join(lines), reply_markup=kb)
        except TelegramBadRequest:
            await target_message.answer("\n".join(lines), reply_markup=kb)
    else:
        await target_message.answer("\n".join(lines), reply_markup=kb)


@router.callback_query(F.data.startswith("order:show:"))
async def cb_show_order(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    order_id = await callback_int(cb, 2)
    if order_id is None:
        return
    order = await db.get_order(order_id)
    if not order:
        await cb.answer(t("common.order_not_found"), show_alert=True)
        return
    if not await ensure_order_owner(cb, order):
        return
    await _render_order_summary(cb.message, order_id, edit=True)
    await cb.answer()


@router.callback_query(F.data.startswith("order:cancel:"))
async def cb_cancel_order(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    order_id = await callback_int(cb, 2)
    if order_id is None:
        return
    order = await db.get_order(order_id)
    if not order:
        await cb.answer(t("common.order_not_found"), show_alert=True)
        return
    if not await ensure_order_owner(cb, order):
        return
    cancelled = await db.try_transition_order_status(
        order_id,
        from_statuses=("draft", "awaiting_payment"),
        to_status="cancelled",
    )
    if not cancelled:
        await cb.answer(t("common.order_closed"), show_alert=True)
        return
    await cb.message.edit_text(t("common.cancelled"), reply_markup=back_to_menu())
    await cb.answer()
