from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from i18n import t
from keyboards import hype_emoji as hype
from keyboards.buttons import button
from keyboards.payment import order_download_rows
from keyboards import product_icons as pi


def main_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    return InlineKeyboardMarkup(inline_keyboard=[
        [button("btn.shop", callback_data="menu:shop", style="primary")],
        [
            button("btn.wallet", callback_data="menu:wallet"),
            button("btn.profile", callback_data="menu:profile"),
        ],
        [
            button("btn.support", callback_data="menu:support"),
            button("btn.history", callback_data="menu:history"),
        ],
        [
            button("btn.refer", callback_data="menu:refer"),
            button("btn.api_link", callback_data="menu:api"),
        ],
        [button("btn.language", callback_data="menu:language")],
    ])


def profile_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [button("btn.purchase_history", callback_data="menu:history")],
        [button("btn.wallet", callback_data="menu:wallet")],
        [button("btn.back_menu", callback_data="menu:home")],
    ])


def _compact_button_text(value: str, limit: int = 36) -> str:
    value = " ".join(str(value or "").split())
    if len(value) <= limit:
        return value
    return value[:limit - 1].rstrip() + "..."


def _order_value(order, key: str, default: str = ""):
    try:
        return order[key] if key in order.keys() else default
    except AttributeError:
        return order.get(key, default)


def _order_button_text(order) -> str:
    name = _compact_button_text(_order_value(order, "product_name", "Order") or "Order")
    return t("history.order_button", id=_order_value(order, "id"), name=name)


def _order_product_view(order) -> dict:
    return {
        "name": _order_value(order, "product_name"),
        "emoji": _order_value(order, "product_emoji"),
        "brand": _order_value(order, "product_brand"),
        "icon_custom_emoji_id": _order_value(order, "product_icon_emoji_id"),
    }


def _history_order_button(order) -> InlineKeyboardButton:
    label = _order_button_text(order)
    emoji_id = pi.icon_id_for_product(_order_product_view(order), 1) or hype.custom_id_for_char("\U0001f4e6")
    if emoji_id:
        return InlineKeyboardButton(
            text=label,
            callback_data=f"history:order:{_order_value(order, 'id')}",
            icon_custom_emoji_id=emoji_id,
        )
    return InlineKeyboardButton(
        text=label,
        callback_data=f"history:order:{_order_value(order, 'id')}",
    )


def history_menu(orders: list | None = None) -> InlineKeyboardMarkup:
    """Purchase-history screen: recent order shortcuts + profile nav."""
    rows: list[list[InlineKeyboardButton]] = []
    for order in orders or []:
        rows.append([_history_order_button(order)])
    rows.extend([
        [button("btn.report_issue", callback_data="ticket:pick")],
        [
            button("btn.profile_short", callback_data="menu:profile"),
            button("btn.wallet", callback_data="menu:wallet"),
        ],
        [button("btn.back_menu", callback_data="menu:home")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def history_order_menu(order_id: int, *, delivered: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if delivered:
        rows.extend(order_download_rows(order_id))
    rows.extend([
        [button("btn.report_issue", callback_data=f"ticket:new:{order_id}")],
        [button("btn.back_orders", callback_data="menu:history")],
        [button("btn.main_menu", callback_data="menu:home")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def wallet_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [button("btn.deposit", callback_data="menu:deposit")],
        [button("btn.back_menu", callback_data="menu:home")],
    ])


def back_to_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        button("btn.back_menu", callback_data="menu:home")
    ]])
