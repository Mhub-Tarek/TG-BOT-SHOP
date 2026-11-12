"""
Language picker. Shown automatically after /start for a brand-new buyer (no
language chosen yet) and reachable anytime via the 🌐 Language menu button.
Order-independent of FSM — plain `lang:set:{code}` callbacks.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup)

import db
import i18n
from i18n import t
from keyboards.buttons import button

router = Router(name="language")

_LANGUAGE_FLAGS: dict[str, str] = {
    "en": "\U0001f1ec\U0001f1e7",
    "ar": "\U0001f1f8\U0001f1e6",
    "hi": "\U0001f1ee\U0001f1f3",
    "vi": "\U0001f1fb\U0001f1f3",
    "zh": "\U0001f1e8\U0001f1f3",
}


def language_keyboard(*, include_back: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text=f"{_LANGUAGE_FLAGS.get(code, '')} {name}".strip(),
            callback_data=f"lang:set:{code}",
        )]
        for code, name in i18n.SUPPORTED.items()
    ]
    if include_back:
        rows.append([button("btn.back_menu", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_picker(message, *, edit: bool, include_back: bool) -> None:
    """Render the language picker. The prompt is shown in the current language
    (English for a first-time user, the chosen one when changing later)."""
    text = t("lang.choose")
    kb = language_keyboard(include_back=include_back)
    if edit and hasattr(message, "edit_text"):
        try:
            await message.edit_text(text, reply_markup=kb)
        except TelegramBadRequest:
            await message.answer(text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "menu:language")
async def cb_language_menu(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show_picker(cb.message, edit=True, include_back=True)
    await cb.answer()


@router.callback_query(F.data.startswith("lang:set:"))
async def cb_set_language(cb: CallbackQuery, state: FSMContext) -> None:
    # Read any stashed product (set by start_with_ref when a brand-new buyer
    # arrives via a `prod` deep-link) BEFORE clearing state.
    data = await state.get_data()
    pending_prod_id = data.get("pending_prod_id")
    await state.clear()
    code = cb.data.split(":")[2]
    if code not in i18n.SUPPORTED:
        await cb.answer(t("lang.invalid"), show_alert=True)
        return
    await db.set_user_language(cb.from_user.id, code)
    # Render the landing screen in the JUST-chosen language (the middleware ran
    # with the previous/None language for this callback). If the buyer came in
    # on a product deep-link, land them on that product card; otherwise the
    # welcome + main menu.
    with i18n.use_language(code):
        confirm = t("lang.changed")
        if pending_prod_id is not None:
            from handlers.start import _show_product_card
            await _show_product_card(cb.message, int(pending_prod_id), edit=True)
            await cb.answer(confirm)
            return
        from handlers.start import welcome_text
        from keyboards.main import main_menu
        text = welcome_text(cb.from_user.first_name)
        kb = main_menu()
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        await cb.message.answer(text, reply_markup=kb)
    await cb.answer(confirm)
