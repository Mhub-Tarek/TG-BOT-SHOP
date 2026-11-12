"""
In-app support tickets. A buyer raises a ticket against one of their orders
(from the post-delivery screen or order history); it is stored and surfaced in
the admin dashboard's Tickets queue, so issues are never missed in Telegram DMs.
Order-linked only. Admins reply from the dashboard (which DMs the buyer back).
"""
from __future__ import annotations

import logging
from html import escape

from aiogram import F, Router
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)
from aiogram.fsm.context import FSMContext

import db
from handlers.security import callback_user, ensure_order_owner
from i18n import t
from keyboards import hype_emoji as hype
from keyboards import product_icons as pi
from keyboards.buttons import button
from keyboards.main import back_to_menu
from states import TicketFlow

router = Router(name="ticket")
log = logging.getLogger(__name__)

MAX_TICKET_LEN = 1000
MAX_OPEN_TICKETS_PER_USER = 5  # cap active (open/answered) tickets to prevent flooding


def _order_label(o) -> str:
    """Plain (non-HTML) label for an order button — button text is NOT parsed as
    HTML, so it must not be escaped. The brand logo is attached as the button's
    icon (see cb_ticket_pick), so the text leads with the product name."""
    name = str(o["product_name"]) if ("product_name" in o.keys() and o["product_name"]) else "order"
    if len(name) > 24:
        name = name[:23].rstrip() + "…"
    return f"{name} ×{o['quantity']} · #{o['id']}"


@router.callback_query(F.data == "ticket:pick")
async def cb_ticket_pick(cb: CallbackQuery, state: FSMContext) -> None:
    """Order picker: choose which order the ticket is about. Each button carries
    the product's brand logo (same custom-emoji icons as the shop buttons), so it
    looks consistent with the rest of the bot."""
    await state.clear()
    user = await callback_user(cb)
    if not user:
        return
    orders = await db.recent_orders(int(user["id"]), limit=8)
    if not orders:
        await cb.answer(t("ticket.none"), show_alert=True)
        return
    rows: list[list[InlineKeyboardButton]] = []
    for o in orders:
        # recent_orders already carries the product's name/emoji/brand/icon, so
        # resolve the brand logo from a lightweight view — no per-order query.
        product_view = {
            "name": o["product_name"],
            "emoji": o["product_emoji"],
            "brand": o["product_brand"],
            "icon_custom_emoji_id": o["product_icon_emoji_id"],
        }
        label = _order_label(o)
        emoji_id = pi.icon_id_for_product(product_view, 1) or hype.custom_id_for_char("\U0001f4e6")
        if emoji_id:
            rows.append([InlineKeyboardButton(
                text=label, callback_data=f"ticket:new:{o['id']}",
                icon_custom_emoji_id=emoji_id)])
        else:
            rows.append([InlineKeyboardButton(
                text=label, callback_data=f"ticket:new:{o['id']}")])
    rows.append([button("btn.back_menu", callback_data="menu:home")])
    await cb.message.edit_text(
        t("ticket.pick_title"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("ticket:new:"))
async def cb_ticket_new(cb: CallbackQuery, state: FSMContext) -> None:
    """Start the ticket flow for a specific order."""
    try:
        order_id = int(cb.data.split(":")[2])
    except (ValueError, IndexError):
        await cb.answer(t("ticket.invalid"), show_alert=True)
        return
    order = await db.get_order(order_id)
    if not order:
        await cb.answer(t("common.order_not_found"), show_alert=True)
        return
    if not await ensure_order_owner(cb, order):
        return

    # Rate-limit: don't let one buyer pile up unanswered tickets (queue flooding).
    if await db.count_active_tickets_for_user(int(order["user_id"])) >= MAX_OPEN_TICKETS_PER_USER:
        await cb.answer(t("ticket.cap"), show_alert=True)
        return

    await state.update_data(order_id=order_id)
    await state.set_state(TicketFlow.waiting_message)
    product = await db.get_product(int(order["product_id"]))
    pname = escape(str(product["name"])) if product else f"order #{order_id}"
    await cb.message.edit_text(
        t("ticket.describe", id=order_id, name=pname),
        reply_markup=back_to_menu(),
    )
    await cb.answer()


@router.message(TicketFlow.waiting_message)
async def msg_ticket_text(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer(t("ticket.send_text"))
        return
    if len(text) > MAX_TICKET_LEN:
        await message.answer(t("ticket.too_long", max=MAX_TICKET_LEN))
        return

    data = await state.get_data()
    order_id = data.get("order_id")
    user = await db.get_user_by_tg(message.from_user.id)
    if not user or not order_id:
        await state.clear()
        await message.answer(t("ticket.expired"), reply_markup=back_to_menu())
        return

    # Re-verify ownership at submit time (the FSM only held an id).
    order = await db.get_order(int(order_id))
    if not order or int(order["user_id"]) != int(user["id"]):
        await state.clear()
        await message.answer(t("ticket.not_yours"), reply_markup=back_to_menu())
        return

    ticket_id = await db.create_ticket(int(user["id"]), int(order_id), text)
    await state.clear()
    await message.answer(
        t("ticket.received", id=ticket_id, order=order_id),
        reply_markup=back_to_menu(),
    )
