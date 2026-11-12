from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from keyboards.buttons import button, icon_id_for_key, text_for_key


def refer_keyboard(link: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=text_for_key("btn.copy_link"),
                copy_text={"text": link},
                icon_custom_emoji_id=icon_id_for_key("btn.copy_link"),
            )
        ],
        [button("btn.main_menu", callback_data="menu:home")],
    ])
