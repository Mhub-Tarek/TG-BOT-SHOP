from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup

from keyboards.buttons import button


def api_link_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [button("btn.api_regenerate", callback_data="api:regen:ask")],
        [button("btn.main_menu", callback_data="menu:home")],
    ])


def api_regenerate_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [button("btn.api_regenerate_yes", callback_data="api:regen:yes")],
        [button("btn.cancel", callback_data="menu:api")],
    ])
