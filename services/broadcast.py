"""
Broadcast helpers — used by both the Telegram admin handler (/addstock,
/notifyrestock, /setprice) and the web dashboard's stock-add / edit-product
routes. Keeping it here avoids duplicating the message template + send loop
in two places.

Rate-limited at 25 msgs/sec (below Telegram's 30/sec global broadcast cap)
so a restock or price-drop announcement to a few-thousand-user base doesn't
get late recipients dropped by the platform.
"""
from __future__ import annotations

import asyncio
import logging
from html import escape
from typing import Any, Mapping

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import db
from config import get_settings
from i18n import t, use_language
from keyboards import hype_emoji as hype
from keyboards import mix_icons
from keyboards.product_icons import icon_id_for_product


def _shop_bot_username() -> str:
    return (get_settings().BOT_USERNAME or "").strip().lstrip("@")


def _product_deep_link(product_id: int) -> str:
    """Deep link that opens the bot chat and triggers /start prod{id} —
    handlers/start.py renders the product detail card on landing. URL buttons
    render with the theme's accent color (green on most dark themes), which
    is the visual difference between the reference shop's Buy button and a
    plain callback_data button."""
    return f"https://t.me/{_shop_bot_username()}?start=prod{int(product_id)}"

log = logging.getLogger(__name__)

RATE_LIMIT_DELAY = 1 / 25  # 25 msgs/sec, leaves headroom under Telegram's 30/sec cap

# Threshold below which we surface the exact remaining count as a scarcity
# signal. Above it, we use a generic urgency line. The marketing principle:
# scarcity claims only persuade when they read as genuine — a concrete
# "Only 12 left" lands; a "limited!!!" tag on a fresh 200-unit batch doesn't.
LOW_STOCK_THRESHOLD = 30
ICON_NEW = "\U0001f195"
ICON_SPARKLE = "\u2728"
ICON_BOOM = "\U0001f4a5"
ICON_DOWN = "\u2b07\ufe0f"
ICON_FIRE = "\U0001f525"
ICON_MONEY = "\U0001f4b8"
ICON_ROCKET = "\U0001f680"
ICON_MEGAPHONE = "\U0001f4e3"
ICON_CHECK = "\u2705"


def _ce(char: str, fallback: str | None = None) -> str:
    return mix_icons.tge(char, fallback=fallback or char)


def _rule() -> str:
    return "\u2501" * 16


def _trim_button_text(text: str) -> str:
    text = " ".join((text or "").split())
    if len(text) <= 64:
        return text
    return text[:61].rstrip() + "..."


def _product_visuals(product: Mapping[str, Any], stock_count: int) -> dict[str, str | None]:
    emoji_id = (icon_id_for_product(product, stock_count) or "").strip() or None
    if not emoji_id:
        emoji_id = hype.custom_id_for_char("\U0001f4e6")
    fallback_raw = str(product["emoji"] or "").strip() or "\U0001f4e6"
    fallback_html = escape(fallback_raw)
    if emoji_id:
        brand_inline = f'<tg-emoji emoji-id="{emoji_id}">{fallback_html}</tg-emoji> '
    else:
        brand_inline = ""
    return {
        "emoji_id": emoji_id,
        "brand_inline": brand_inline,
        "name_html": escape(str(product["name"])),
        "name_disp": f"{brand_inline}{escape(str(product['name']))}",
        "button_name": str(product["name"]),
    }


def _product_button(product: Mapping[str, Any], stock_count: int,
                    *, callback_data: str | None = None,
                    url: str | None = None) -> InlineKeyboardButton:
    visuals = _product_visuals(product, stock_count)
    button = InlineKeyboardButton(
        text=_trim_button_text(hype.clean_button_text(t("btn.buy_named", name=visuals["button_name"]))),
        callback_data=callback_data,
        url=url,
        icon_custom_emoji_id=visuals["emoji_id"],
        style="primary",
    )
    return button


def _scarcity_line(stock_count: int) -> str:
    """Localized via the i18n contextvar — call inside a use_language() block."""
    if stock_count <= LOW_STOCK_THRESHOLD:
        return t("bc.scarcity_left", n=stock_count)
    return t("bc.scarcity_limited")


async def broadcast_restock(
    bot: Bot,
    product: Mapping[str, Any],
    items_added: int,
) -> tuple[int, int]:
    """Notify every user that `items_added` new stock items just landed for
    `product`. Returns (ok, fail) counts.

    Skips silently when items_added <= 0 (e.g. dashboard pasted only blank
    lines), so callers can always fire-and-forget without a guard."""
    if items_added <= 0:
        return 0, 0

    sc = await db.stock_count(int(product["id"]))
    visuals = _product_visuals(product, sc)
    # Product name + price stay as the owner entered them (data); only the
    # surrounding chrome ("new stock added", "Available", "Buy") is localized.
    name_disp = visuals["name_disp"]
    price_disp = f"{float(product['base_price']):.2f}"

    from db import get_db
    async with get_db() as d:
        cur = await d.execute("SELECT tg_id, language FROM users")
        rows = await cur.fetchall()

    ok = fail = 0
    for r in rows:
        with use_language(r["language"]):
            text = (
                f"{_ce(ICON_NEW)} {t('bc.restock', n=items_added, name=name_disp)} {_ce(ICON_SPARKLE)}\n"
                f"{_rule()}\n"
                f"{_ce(ICON_BOOM)} <b>Fresh batch:</b> {items_added} new codes\n"
                f"{t('bc.available', n=sc)}\n"
                f"{t('bc.price', price=price_disp)}\n\n"
                f"{_scarcity_line(sc)}\n"
                f"{_ce(ICON_DOWN)} {t('bc.open_shop')}"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                _product_button(
                    product,
                    sc,
                    callback_data=f"prod:{product['id']}",
                )
            ]])
        try:
            await bot.send_message(int(r["tg_id"]), hype.hype_text(text), reply_markup=kb)
            ok += 1
        except Exception as e:
            log.info("restock broadcast: failed to %s: %s", r["tg_id"], e)
            fail += 1
        await asyncio.sleep(RATE_LIMIT_DELAY)
    log.info("restock broadcast for product %s: %d ok / %d fail",
             product["id"], ok, fail)
    return ok, fail


def _fmt_usdt(amount: float) -> str:
    """`95` for whole values, `17.50` when cents matter — matches the look
    of price drop cards in the reference shop."""
    a = round(float(amount), 2)
    if a == int(a):
        return str(int(a))
    return f"{a:.2f}"


async def broadcast_price_drop(
    bot: Bot,
    product: Mapping[str, Any],
    old_price: float,
    new_price: float,
) -> tuple[int, int]:
    """Notify every user that `product` just dropped from `old_price` to
    `new_price` USDT. Returns (ok, fail).

    Skips silently when the new price is not actually lower than the old
    price (e.g. an admin saved the edit form without changing the number, or
    raised it). Same fire-and-forget contract as broadcast_restock so the
    web dashboard's redirect isn't blocked while sends trickle out."""
    old_price = round(float(old_price), 2)
    new_price = round(float(new_price), 2)
    if new_price <= 0 or new_price >= old_price:
        return 0, 0

    # The Buy button below is a URL deep-link of the form
    # https://t.me/<bot_username>?start=prodN. If BOT_USERNAME is blank
    # (admin cleared it from .env) the resulting URL would be
    # https://t.me/?start=prodN, which Telegram rejects with
    # BUTTON_URL_INVALID — failing the WHOLE broadcast for every user.
    # Bail loudly so the operator notices in logs instead.
    if not _shop_bot_username():
        log.warning(
            "price-drop broadcast aborted for product %s: BOT_USERNAME is "
            "blank in .env, so the Buy deep-link URL would be invalid.",
            product["id"],
        )
        return 0, 0

    sc = await db.stock_count(int(product["id"]))
    emoji_id = (icon_id_for_product(product, sc) or "").strip() or None
    fallback_glyph = escape((product["emoji"] or "").strip() or "📦")

    # Inline product logo via <tg-emoji> so the card mirrors the reference
    # bot — brand icon sits right before the product name in the body and on
    # the buy button.
    if emoji_id:
        brand_inline = f'<tg-emoji emoji-id="{emoji_id}">{fallback_glyph}</tg-emoji> '
    else:
        brand_inline = f"{fallback_glyph} " if fallback_glyph else ""

    name_html = escape(product["name"])
    visuals = _product_visuals(product, sc)
    brand_inline = visuals["brand_inline"]
    name_html = visuals["name_html"]
    old_disp = _fmt_usdt(old_price)
    new_disp = _fmt_usdt(new_price)
    # Buy-button name (product data); the localized "Buy" word is added below.
    buy_name = product["name"] if emoji_id else f"{(f'{fallback_glyph} ' if fallback_glyph else '')}{product['name']}"

    # URL button (deep-link) instead of callback_data: Telegram renders URL
    # buttons with the theme accent color (bright green on most dark themes,
    # matching the reference shop's Buy button). The deep-link payload is
    # parsed by handlers/start.py's start_with_ref → product detail card.
    deep_link = _product_deep_link(int(product["id"]))

    from db import get_db
    async with get_db() as d:
        cur = await d.execute("SELECT tg_id, language FROM users")
        rows = await cur.fetchall()

    ok = fail = 0
    for r in rows:
        with use_language(r["language"]):
            text = (
                f"{_ce(ICON_FIRE)} {t('bc.pricedrop')} {_ce(ICON_SPARKLE)}\n"
                f"{_rule()}\n"
                f"{brand_inline}<b>{name_html}</b>\n"
                f"{t('bc.was', old=old_disp)}\n"
                f"{t('bc.now', new=new_disp)}\n\n"
                f"{_ce(ICON_MONEY)} {t('bc.limited_offer')}"
            )
            btn_text = t("btn.buy_named", name=buy_name)
            if len(btn_text) > 64:
                btn_text = btn_text[:63] + "…"
            button = InlineKeyboardButton(
                text=btn_text,
                url=deep_link,
                icon_custom_emoji_id=emoji_id,  # None is ignored by aiogram
            )
            button = _product_button(product, sc, url=deep_link)
            kb = InlineKeyboardMarkup(inline_keyboard=[[button]])
        try:
            await bot.send_message(int(r["tg_id"]), hype.hype_text(text), reply_markup=kb)
            ok += 1
        except Exception as e:
            log.info("price-drop broadcast: failed to %s: %s", r["tg_id"], e)
            fail += 1
        await asyncio.sleep(RATE_LIMIT_DELAY)
    log.info(
        "price-drop broadcast for product %s (%s → %s USDT): %d ok / %d fail",
        product["id"], _fmt_usdt(old_price), _fmt_usdt(new_price), ok, fail,
    )
    return ok, fail


async def broadcast_new_product(bot: Bot, product: Mapping[str, Any]) -> tuple[int, int]:
    """Announce a brand-new product to every user. Returns (ok, fail).

    Sibling to broadcast_restock — fires once, when a product that never
    existed before is created (from the web dashboard or the Telegram
    /addproduct flow). Brand logo via custom emoji, green URL deep-link Buy
    button (same style as the price-drop card). Fire-and-forget friendly."""
    if not _shop_bot_username():
        log.warning(
            "new-product broadcast aborted for product %s: BOT_USERNAME is "
            "blank in .env, so the Buy deep-link URL would be invalid.",
            product["id"],
        )
        return 0, 0

    sc = await db.stock_count(int(product["id"]))
    emoji_id = (icon_id_for_product(product, sc) or "").strip() or None
    fallback_glyph = escape((product["emoji"] or "").strip() or "📦")
    if emoji_id:
        brand_inline = f'<tg-emoji emoji-id="{emoji_id}">{fallback_glyph}</tg-emoji> '
    else:
        brand_inline = f"{fallback_glyph} " if fallback_glyph else ""

    name_html = escape(product["name"])
    visuals = _product_visuals(product, sc)
    brand_inline = visuals["brand_inline"]
    name_html = visuals["name_html"]
    price_disp = _fmt_usdt(float(product["base_price"]))
    name_disp = f"{brand_inline}{name_html}"
    prefix = "" if emoji_id else (f"{fallback_glyph} " if fallback_glyph else "")
    buy_name = f"{prefix}{product['name']}"
    deep_link = _product_deep_link(int(product["id"]))

    from db import get_db
    async with get_db() as d:
        cur = await d.execute("SELECT tg_id, language FROM users")
        rows = await cur.fetchall()

    ok = fail = 0
    for r in rows:
        with use_language(r["language"]):
            text = (
                f"{_ce(ICON_ROCKET)} {t('bc.new_product')} {_ce(ICON_SPARKLE)}\n"
                f"{_rule()}\n"
                f"{t('bc.name', name=name_disp)}\n"
                f"{t('bc.price', price=price_disp)}\n\n"
                f"{_ce(ICON_DOWN)} {t('bc.open_shop')}"
            )
            btn_text = t("btn.buy_named", name=buy_name)
            if len(btn_text) > 64:
                btn_text = btn_text[:63] + "…"
            button = InlineKeyboardButton(
                text=btn_text,
                url=deep_link,
                icon_custom_emoji_id=emoji_id,  # None is ignored by aiogram
            )
            button = _product_button(product, sc, url=deep_link)
            kb = InlineKeyboardMarkup(inline_keyboard=[[button]])
        try:
            await bot.send_message(int(r["tg_id"]), hype.hype_text(text), reply_markup=kb)
            ok += 1
        except Exception as e:
            log.info("new-product broadcast: failed to %s: %s", r["tg_id"], e)
            fail += 1
        await asyncio.sleep(RATE_LIMIT_DELAY)
    log.info("new-product broadcast for product %s: %d ok / %d fail",
             product["id"], ok, fail)
    return ok, fail


async def broadcast_admin_notification(bot: Bot, body_text: str) -> tuple[int, int]:
    """Fan an admin-authored notification out to every user. Returns (ok, fail).

    The body is HTML-escaped before sending so admin-typed `<` / `>` can't
    abort the entire run on Telegram's parser. A bold "Admin Notification"
    header is auto-prepended (localized per recipient); the admin-typed body
    is sent as written, since it can't be auto-translated.

    When the run finishes, every admin in settings.ADMIN_IDS receives a DM
    summary — the web dashboard fires this as a background task and has
    already returned an HTTP response by then, so this is the only feedback
    channel for ok/fail counts."""
    body = (body_text or "").strip()
    if not body:
        return 0, 0

    body_html = escape(body)

    from db import get_db
    async with get_db() as d:
        cur = await d.execute("SELECT tg_id, language FROM users")
        rows = await cur.fetchall()

    ok = fail = 0
    for r in rows:
        with use_language(r["language"]):
            text = (
                f"{_ce(ICON_MEGAPHONE)} {t('bc.admin_notification')} {_ce(ICON_SPARKLE)}\n"
                f"{_rule()}\n"
                f"{body_html}\n"
                f"{_rule()}"
            )
        try:
            await bot.send_message(int(r["tg_id"]), hype.hype_text(text), parse_mode="HTML")
            ok += 1
        except Exception as e:
            log.info("admin broadcast: failed to %s: %s", r["tg_id"], e)
            fail += 1
        await asyncio.sleep(RATE_LIMIT_DELAY)
    log.info("admin broadcast: %d ok / %d fail", ok, fail)

    summary = f"✅ <b>Broadcast complete</b>\n\nDelivered: {ok}\nFailed: {fail}"
    summary = (
        f"{_ce(ICON_CHECK)} <b>Broadcast complete</b>\n\n"
        f"Delivered: {ok}\n"
        f"Failed: {fail}"
    )
    for admin_id in get_settings().ADMIN_IDS:
        try:
            await bot.send_message(int(admin_id), hype.hype_text(summary), parse_mode="HTML")
        except Exception as e:
            log.info("admin broadcast: summary DM to %s failed: %s", admin_id, e)

    return ok, fail
