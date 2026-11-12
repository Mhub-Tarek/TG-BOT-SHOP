from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import db
from i18n import t
from keyboards.api import api_link_keyboard, api_regenerate_confirm_keyboard
from services.reseller_api import ensure_api_key_for_user, regenerate_api_key_for_user

router = Router(name="api")

API_DOCS_URL = "https://akunding.shop/api/docs"


async def _api_payload(from_user) -> tuple[str, bool]:
    user = await db.get_or_create_user(
        int(from_user.id),
        getattr(from_user, "username", None),
        getattr(from_user, "first_name", None),
    )
    created = await ensure_api_key_for_user(int(user["id"]))
    if created:
        return (
            t(
                "api.screen_new",
                key=escape(created.key),
                prefix=escape(created.prefix),
                docs=API_DOCS_URL,
            ),
            True,
        )
    row = await db.get_active_api_key_for_user(int(user["id"]))
    prefix = str(row["key_prefix"]) if row else ""
    return (
        t("api.screen_existing", prefix=escape(prefix), docs=API_DOCS_URL),
        False,
    )


@router.callback_query(F.data == "menu:api")
async def cb_api_link(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text, _created = await _api_payload(cb.from_user)
    await cb.message.edit_text(text, reply_markup=api_link_keyboard())
    await cb.answer()


@router.message(Command("api"))
async def cmd_api_link(message: Message, state: FSMContext) -> None:
    await state.clear()
    text, _created = await _api_payload(message.from_user)
    await message.answer(text, reply_markup=api_link_keyboard())


@router.callback_query(F.data == "api:regen:ask")
async def cb_api_regen_ask(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.message.edit_text(
        t("api.regen_confirm", docs=API_DOCS_URL),
        reply_markup=api_regenerate_confirm_keyboard(),
    )
    await cb.answer()


@router.callback_query(F.data == "api:regen:yes")
async def cb_api_regen_yes(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    user = await db.get_or_create_user(
        int(cb.from_user.id),
        cb.from_user.username,
        cb.from_user.first_name,
    )
    material = await regenerate_api_key_for_user(int(user["id"]))
    await cb.message.edit_text(
        t(
            "api.screen_new",
            key=escape(material.key),
            prefix=escape(material.prefix),
            docs=API_DOCS_URL,
        ),
        reply_markup=api_link_keyboard(),
    )
    await cb.answer(t("api.regenerated"))
