from __future__ import annotations

import asyncio
import logging
from html import escape
from typing import Any, Iterable, Mapping

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import db
from config import get_settings
from keyboards import hype_emoji as hype
from keyboards import mix_icons
from keyboards.product_icons import icon_id_for_product
from services.pricing import tier_rows_for_display

log = logging.getLogger("admin.broadcast_tools")

RATE_LIMIT_DELAY = 1 / 25

ICON_BULK = "\U0001f7e2"
ICON_FLASH = "\U0001f4a5"
ICON_MONEY = "\U0001f4b5"
ICON_BOX = "\U0001f4e6"
ICON_UP = "\U0001f4b9"
ICON_CLOCK = "\u23f1\ufe0f"
ICON_DOWN = "\U0001f447"


def _ce(char: str, fallback: str | None = None) -> str:
    return mix_icons.tge(char, fallback=fallback or char)


def _value(row: Mapping[str, Any], key: str, default: Any = "") -> Any:
    try:
        if hasattr(row, "keys") and key in row.keys():
            return row[key]
    except Exception:
        pass
    try:
        return row.get(key, default)  # type: ignore[attr-defined]
    except Exception:
        return default


def _fmt_usdt(amount: float) -> str:
    value = round(float(amount), 2)
    if value == int(value):
        return str(int(value))
    return f"{value:.2f}"


def _duration_label(value: str | None) -> str:
    raw = " ".join((value or "").split()).strip()
    if not raw:
        return "12 hours"
    cleaned = raw.lower()
    for suffix in ("hours", "hour", "hrs", "hr", "h"):
        if cleaned.endswith(suffix):
            number = cleaned[:-len(suffix)].strip()
            if number.replace(".", "", 1).isdigit():
                qty = float(number)
                shown = str(int(qty)) if qty == int(qty) else str(qty)
                unit = "hour" if qty == 1 else "hours"
                return f"{shown} {unit}"
    if cleaned.replace(".", "", 1).isdigit():
        qty = float(cleaned)
        shown = str(int(qty)) if qty == int(qty) else str(qty)
        unit = "hour" if qty == 1 else "hours"
        return f"{shown} {unit}"
    return raw[:40]


def _shop_bot_username() -> str:
    return (get_settings().BOT_USERNAME or "").strip().lstrip("@")


def _product_deep_link(product_id: int) -> str:
    return f"https://t.me/{_shop_bot_username()}?start=prod{int(product_id)}"


def _product_visuals(product: Mapping[str, Any], stock_count: int) -> dict[str, str | None]:
    emoji_id = (icon_id_for_product(product, stock_count) or "").strip() or None
    if not emoji_id:
        emoji_id = hype.custom_id_for_char(ICON_BOX)
    fallback_raw = str(_value(product, "emoji", "") or "").strip() or ICON_BOX
    fallback_html = escape(fallback_raw)
    if emoji_id:
        brand_inline = f'<tg-emoji emoji-id="{emoji_id}">{fallback_html}</tg-emoji> '
    else:
        brand_inline = f"{fallback_html} "
    name_html = escape(str(_value(product, "name", "Product")))
    return {
        "emoji_id": emoji_id,
        "brand_inline": brand_inline,
        "name_html": name_html,
        "name_disp": f"{brand_inline}<b>{name_html}</b>",
    }


def _product_button(product: Mapping[str, Any], stock_count: int) -> InlineKeyboardButton:
    visuals = _product_visuals(product, stock_count)
    name = " ".join(str(_value(product, "name", "Product")).split())
    label = f"Buy {name}"
    if len(label) > 64:
        label = label[:61].rstrip() + "..."
    return InlineKeyboardButton(
        text=hype.clean_button_text(label),
        url=_product_deep_link(int(_value(product, "id", 0))),
        icon_custom_emoji_id=visuals["emoji_id"],
        style="primary",
    )


def _markup(product: Mapping[str, Any], stock_count: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_product_button(product, stock_count)]])


async def active_product_choices() -> list[dict[str, Any]]:
    products = await db.list_products(active_only=True, limit=500)
    ids = [int(p["id"]) for p in products]
    counts = await db.stock_counts(ids)
    tiers_map = await db.pricing_tiers_for_products(ids)
    choices: list[dict[str, Any]] = []
    for product in products:
        pid = int(product["id"])
        choices.append({
            "id": pid,
            "name": str(product["name"]),
            "base_price": float(product["base_price"]),
            "stock": counts.get(pid, 0),
            "tier_count": len(tiers_map.get(pid, [])),
        })
    return choices


def bulk_pricing_text(product: Mapping[str, Any],
                      stock_count: int,
                      tiers: Iterable[Mapping[str, Any]]) -> str:
    visuals = _product_visuals(product, stock_count)
    base = _fmt_usdt(float(_value(product, "base_price", 0)))
    rows = []
    for tier in tiers:
        min_qty = int(_value(tier, "min_qty", 0))
        unit = _fmt_usdt(float(_value(tier, "unit_price", 0)))
        if min_qty <= 0 or float(_value(tier, "unit_price", 0)) <= 0:
            continue
        rows.append(f"{_ce(ICON_UP)} Buy {min_qty}+ \u2014 <b>{unit} USDT</b> each")
    tiers_block = "\n".join(rows)
    return (
        f"{_ce(ICON_BULK)} <b>New Bulk Pricing!</b>\n\n"
        f"{visuals['name_disp']}\n"
        f"{_ce(ICON_MONEY)} Base price: <b>{base} USDT</b>\n\n"
        f"{_ce(ICON_BOX)} <b>Tiered discounts:</b>\n"
        f"{tiers_block}\n\n"
        f"<i>Stock up &amp; save more \u2014 limited time!</i>"
    )


def flash_sale_text(product: Mapping[str, Any],
                    stock_count: int,
                    was_price: float,
                    now_price: float,
                    duration_label: str) -> str:
    visuals = _product_visuals(product, stock_count)
    was = round(float(was_price), 2)
    now = round(float(now_price), 2)
    pct = int(round((1 - now / was) * 100)) if was > 0 else 0
    duration = escape(_duration_label(duration_label)[:40])
    return (
        f"{_ce(ICON_FLASH)} <b>FLASH SALE \u2014 LIMITED TIME!</b>\n\n"
        f"{visuals['name_disp']}\n\n"
        f"{_ce(ICON_MONEY)} Was: <s>${_fmt_usdt(was)}</s>\n"
        f"{_ce(ICON_FLASH)} Now: <b>${_fmt_usdt(now)} USDT</b> (-{pct}%)\n\n"
        f"{_ce(ICON_CLOCK)} <b>Hurry \u2014 ends in {duration}</b>\n"
        f"Grab it before the timer runs out {_ce(ICON_DOWN)}"
    )


async def _recipient_rows() -> list[Mapping[str, Any]]:
    from db import get_db
    async with get_db() as conn:
        cur = await conn.execute("SELECT tg_id FROM users")
        return await cur.fetchall()


async def _send_to_all(bot: Bot,
                       text: str,
                       reply_markup: InlineKeyboardMarkup,
                       *,
                       log_label: str) -> tuple[int, int]:
    ok = fail = 0
    rows = await _recipient_rows()
    for row in rows:
        try:
            await bot.send_message(
                int(_value(row, "tg_id")),
                hype.hype_text(text),
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
            ok += 1
        except Exception as exc:
            log.info("%s broadcast: failed to %s: %s",
                     log_label, _value(row, "tg_id"), exc)
            fail += 1
        await asyncio.sleep(RATE_LIMIT_DELAY)
    return ok, fail


async def _notify_admins(bot: Bot, title: str, ok: int, fail: int,
                         products: int) -> None:
    summary = (
        f"<b>{escape(title)} complete</b>\n\n"
        f"Products: {products}\n"
        f"Delivered: {ok}\n"
        f"Failed: {fail}"
    )
    for admin_id in get_settings().ADMIN_IDS:
        try:
            await bot.send_message(int(admin_id), summary, parse_mode="HTML")
        except Exception as exc:
            log.info("%s summary DM to %s failed: %s", title, admin_id, exc)


async def broadcast_bulk_pricing(bot: Bot,
                                 product_ids: list[int]) -> tuple[int, int, int]:
    ok = fail = sent_products = 0
    for product_id in product_ids:
        product = await db.get_product(int(product_id))
        if not product or not int(product["active"]):
            continue
        raw_tiers = await db.get_pricing_tiers(int(product_id))
        tiers = tier_rows_for_display(float(product["base_price"]), raw_tiers)
        if not tiers:
            continue
        stock = await db.stock_count(int(product_id))
        text = bulk_pricing_text(product, stock, tiers)
        msg_ok, msg_fail = await _send_to_all(
            bot, text, _markup(product, stock), log_label="bulk-pricing",
        )
        ok += msg_ok
        fail += msg_fail
        sent_products += 1
    await _notify_admins(bot, "Bulk pricing broadcast", ok, fail, sent_products)
    return ok, fail, sent_products


async def broadcast_flash_sale(bot: Bot,
                               product_id: int,
                               was_price: float,
                               now_price: float,
                               duration_label: str) -> tuple[int, int, int]:
    product = await db.get_product(int(product_id))
    if not product or not int(product["active"]):
        await _notify_admins(bot, "Flash sale broadcast", 0, 0, 0)
        return 0, 0, 0
    stock = await db.stock_count(int(product_id))
    text = flash_sale_text(product, stock, was_price, now_price, duration_label)
    ok, fail = await _send_to_all(
        bot, text, _markup(product, stock), log_label="flash-sale",
    )
    await _notify_admins(bot, "Flash sale broadcast", ok, fail, 1)
    return ok, fail, 1
