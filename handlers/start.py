from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import db
from config import get_settings
from keyboards import hype_emoji as hype
from keyboards.main import main_menu
from i18n import t

router = Router(name="start")
settings = get_settings()


def welcome_text(first_name: str) -> str:
    """Localized welcome (rendered in the current language via the i18n contextvar)."""
    return t("start.welcome",
             shop=escape(settings.SHOP_NAME),
             name=escape(first_name or "there"),
             crown=hype.crown(),
             sparkle=hype.sparkle(),
             fire=hype.fire(),
             rocket=hype.rocket(),
             diamond=hype.diamond(),
             arrow=hype.down_arrow())


@router.message(CommandStart(deep_link=True))
async def start_with_ref(message: Message, command: CommandObject, state: FSMContext) -> None:
    await state.clear()
    arg = (command.args or "").strip()
    ref_tg_id: int | None = None
    prod_id: int | None = None

    # Supported deep-link payloads:
    #   refN  → referrer attribution (N = referrer's tg_id)
    #   prodN → land directly on product N's detail card (used by
    #           price-drop / restock broadcasts so the Buy button can render
    #           as a URL deep-link, which Telegram clients style with the
    #           theme accent color — green on most dark themes — instead of
    #           the muted callback-button color).
    if arg.startswith("ref"):
        try:
            ref_tg_id = int(arg[3:])
        except ValueError:
            ref_tg_id = None
    elif arg.startswith("prod"):
        try:
            prod_id = int(arg[4:])
        except ValueError:
            prod_id = None

    user = await db.get_or_create_user(message.from_user.id, message.from_user.username,
                                       message.from_user.first_name, referrer_tg_id=ref_tg_id)
    await db.log_event(message.from_user.id, "start")  # funnel: reach (analytics)

    if prod_id is not None:
        product = await db.get_product(prod_id)
        if product and product["active"]:
            # Brand-new buyer arriving via a product deep-link: show the language
            # picker FIRST (stashing the product) so the card lands in their
            # chosen language instead of being skipped straight to English.
            # cb_set_language consumes pending_prod_id. (review: deep-link picker)
            if not user["language"]:
                await state.update_data(pending_prod_id=prod_id)
                await _show_language_picker(message)
                return
            await _show_product_card(message, prod_id)
            return

    if not user["language"]:
        await _show_language_picker(message)
        return
    await message.answer(welcome_text(message.from_user.first_name), reply_markup=main_menu())


async def _show_product_card(target, prod_id: int, *, edit: bool = False) -> None:
    """Render a product detail card (mirrors handlers.shop.cb_product). Late
    imports avoid a circular module load — shop imports security … and start.py
    is the first router registered."""
    from handlers.shop import _product_card
    from keyboards.shop import product_detail_keyboard
    product = await db.get_product(prod_id)
    if not product or not product["active"]:
        return
    tiers = await db.get_pricing_tiers(prod_id)
    stock_n = await db.stock_count(prod_id)
    text = _product_card(product, stock_n, tiers)
    kb = product_detail_keyboard(prod_id, in_stock=stock_n > 0)
    if edit and hasattr(target, "edit_text"):
        from aiogram.exceptions import TelegramBadRequest
        try:
            await target.edit_text(text, reply_markup=kb)
        except TelegramBadRequest:
            await target.answer(text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    user = await db.get_or_create_user(message.from_user.id, message.from_user.username,
                                       message.from_user.first_name)
    await db.log_event(message.from_user.id, "start")  # funnel: reach (analytics)
    # Brand-new buyer (no language chosen yet) → show the language picker first.
    if not user["language"]:
        await _show_language_picker(message)
        return
    await message.answer(welcome_text(message.from_user.first_name), reply_markup=main_menu())


async def _show_language_picker(message: Message) -> None:
    # Late import to avoid a circular module load at startup.
    from handlers.language import show_picker
    await show_picker(message, edit=False, include_back=False)


@router.callback_query(F.data == "menu:home")
async def cb_home(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.message.edit_text(welcome_text(cb.from_user.first_name), reply_markup=main_menu())
    await cb.answer()


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(welcome_text(message.from_user.first_name), reply_markup=main_menu())
