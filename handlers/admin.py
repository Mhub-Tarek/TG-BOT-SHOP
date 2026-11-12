from __future__ import annotations

import asyncio
import logging
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message,
                           MessageEntity)

import db
from config import get_settings
from services.broadcast import (broadcast_new_product, broadcast_price_drop,
                                 broadcast_restock)
from services.pricing import format_tier_summary
from states import AdminFlow

router = Router(name="admin")
log = logging.getLogger(__name__)
settings = get_settings()


def is_admin(tg_id: int) -> bool:
    # Delegate to the single source of truth so the admin rule can't drift. (review M5)
    return settings.is_admin(tg_id)


# Strong refs to fire-and-forget broadcast tasks so the event loop can't GC and
# silently cancel them mid-send. (review M2)
_BG_TASKS: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)

    def _done(t: asyncio.Task) -> None:
        _BG_TASKS.discard(t)
        if not t.cancelled() and t.exception() is not None:
            log.error("broadcast task failed", exc_info=t.exception())

    task.add_done_callback(_done)


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("This command is admin-only.")
        return
    await message.answer(
        "🛠 <b>Admin commands</b>\n\n"
        "/addproduct — guided product creation\n"
        "/products — list all products\n"
        "/addstock — upload stock items\n"
        "/addtier — add bulk pricing tier\n"
        "/addcoupon — create a coupon\n"
        "/coupons — list coupons\n"
        "/stats — sales &amp; revenue\n"
        "/setprice — drop a product's price (broadcasts to all users)\n"
        "/broadcast — send a message to all users\n"
        "/notifyrestock &lt;product_id&gt; &lt;items_added&gt;\n"
        "/grabemoji — snapshot a third-party emoji pack "
        "(send a custom emoji with the command)\n\n"
        "🌐 Or open the browser dashboard at "
        f"<code>http://&lt;host&gt;:{settings.WEB_PORT}</code>"
    )


# ------------------- Add product (extended) -------------------

@router.message(Command("addproduct"))
async def cmd_addproduct(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.set_state(AdminFlow.add_product_name)
    await message.answer("Product name? (e.g. 'Mobbin Pro 1 Year')")


@router.message(AdminFlow.add_product_name)
async def addp_name(message: Message, state: FSMContext) -> None:
    # message.text is None for non-text messages (sticker/photo). Guard so a
    # stray media message doesn't crash the FSM step with AttributeError. (review L)
    name = (message.text or "").strip()
    if not name:
        await message.answer("Send the product name as text.")
        return
    await state.update_data(name=name)
    await state.set_state(AdminFlow.add_product_emoji)
    await message.answer(
        "Product icon (emoji shown in the shop list).\n\n"
        "Examples: 🟢 Canva · 🔵 Replit · 🟣 Cursor · 🟠 Framer\n"
        "Send one emoji, or 'skip' for the default 📦 icon."
    )


@router.message(AdminFlow.add_product_emoji)
async def addp_emoji(message: Message, state: FSMContext) -> None:
    emoji = (message.text or "").strip()
    if emoji.lower() == "skip":
        emoji = ""
    await state.update_data(emoji=emoji)
    await state.set_state(AdminFlow.add_product_price)
    await message.answer("Base price in USDT? (e.g. 10.00)")


@router.message(AdminFlow.add_product_price)
async def addp_price(message: Message, state: FSMContext) -> None:
    try:
        price = round(float(message.text.strip().replace(",", ".")), 2)
        if price <= 0:
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer("Send a positive number.")
        return
    await state.update_data(base_price=price)
    await state.set_state(AdminFlow.add_product_unit)
    await message.answer("Unit label (singular: code / key / account)? Send 'skip' for 'code'.")


@router.message(AdminFlow.add_product_unit)
async def addp_unit(message: Message, state: FSMContext) -> None:
    unit = (message.text or "").strip().lower()
    if unit == "skip" or not unit:
        unit = "code"
    await state.update_data(unit_label=unit)
    await state.set_state(AdminFlow.add_product_features)
    await message.answer(
        "Features panel — one line per feature (shown inside the product card). "
        "Example:\n\n<code>💚 Instant Code Delivery\n🛡 Full Year Warranty</code>\n\n"
        "Send 'skip' to use the shop default."
    )


@router.message(AdminFlow.add_product_features)
async def addp_features(message: Message, state: FSMContext) -> None:
    feats = (message.text or "").strip()
    if feats.lower() == "skip":
        feats = ""
    await state.update_data(features=feats)
    await state.set_state(AdminFlow.add_product_description)
    await message.answer("Long description (or 'skip').")


@router.message(AdminFlow.add_product_description)
async def addp_desc(message: Message, state: FSMContext, bot: Bot) -> None:
    desc = (message.text or "").strip()
    if desc.lower() == "skip":
        desc = ""
    data = await state.get_data()
    await state.clear()
    pid = await db.create_product(
        name=data["name"], emoji=data["emoji"], description=desc,
        base_price=float(data["base_price"]),
        features=data.get("features", ""), unit_label=data.get("unit_label", "code"),
    )
    await message.answer(
        f"✅ Product created. ID: <code>{pid}</code>\n"
        f"📣 Announcing it to all users now.\n"
        f"Now add stock with /addstock."
    )
    # Fire-and-forget the "NEW PRODUCT ADDED!" broadcast (same pattern as
    # /addstock's restock announcement), with a strong task ref. (review M2)
    product = await db.get_product(pid)
    if product is not None:
        _spawn(broadcast_new_product(bot, product))


# ------------------- List products -------------------

@router.message(Command("products"))
async def cmd_products(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    products = await db.list_products(active_only=False, limit=200)
    if not products:
        await message.answer("No products yet.")
        return
    # Batch the per-product stock counts + tiers into two queries. (perf)
    ids = [int(p["id"]) for p in products]
    counts = await db.stock_counts(ids)
    tiers_by_pid = await db.pricing_tiers_for_products(ids)
    lines = ["<b>All products</b>"]
    for p in products:
        pid = int(p["id"])
        sc = counts.get(pid, 0)
        active = "" if p["active"] else " (inactive)"
        emoji = (p["emoji"] or "").strip()
        prefix = f"{emoji} " if emoji else ""
        lines.append(
            f"#{p['id']} — {prefix}{escape(p['name'])} — "
            f"${float(p['base_price']):.2f}/{p['unit_label']} — stock {sc}{active}"
        )
        tiers = tiers_by_pid.get(pid, [])
        if tiers:
            lines.append(format_tier_summary(float(p['base_price']), tiers))
    await message.answer("\n".join(lines))


# ------------------- Add stock -------------------

@router.message(Command("addstock"))
async def cmd_addstock(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    products = await db.list_products(active_only=False, limit=200)
    if not products:
        await message.answer("No products yet. Create one with /addproduct.")
        return
    lines = ["Reply with the product ID to add stock to:"]
    for p in products:
        sc = await db.stock_count(int(p["id"]))
        lines.append(f"#{p['id']} — {escape(p['name'])} (stock: {sc})")
    await state.set_state(AdminFlow.add_stock_pick_product)
    await message.answer("\n".join(lines))


@router.message(AdminFlow.add_stock_pick_product)
async def addstock_pick(message: Message, state: FSMContext) -> None:
    try:
        pid = int(message.text.strip().lstrip("#"))
    except (ValueError, AttributeError):
        await message.answer("Send the product ID number.")
        return
    product = await db.get_product(pid)
    if not product:
        await message.answer("Product not found.")
        return
    await state.update_data(product_id=pid)
    await state.set_state(AdminFlow.add_stock_paste)
    await message.answer(
        f"Send the stock items for <b>{escape(product['name'])}</b>, one per line."
    )


@router.message(AdminFlow.add_stock_paste)
async def addstock_paste(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    pid = int(data["product_id"])
    raw_lines = (message.text or "").splitlines()
    n = await db.add_stock_items(pid, raw_lines)
    product = await db.get_product(pid)
    await state.clear()
    if n == 0:
        await message.answer("Nothing added (no non-empty lines).")
        return
    await message.answer(f"✅ Added {n} stock items to {escape(product['name'])}.")
    await broadcast_restock(bot, product, n)


# ------------------- Pricing tiers -------------------

@router.message(Command("addtier"))
async def cmd_addtier(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    products = await db.list_products(active_only=False, limit=200)
    if not products:
        await message.answer("No products yet.")
        return
    lines = ["Reply with product ID:"]
    for p in products:
        lines.append(f"#{p['id']} — {escape(p['name'])}")
    await state.set_state(AdminFlow.add_tier_pick_product)
    await message.answer("\n".join(lines))


@router.message(AdminFlow.add_tier_pick_product)
async def addtier_pick(message: Message, state: FSMContext) -> None:
    try:
        pid = int(message.text.strip().lstrip("#"))
    except (ValueError, AttributeError):
        await message.answer("Send the product ID number.")
        return
    if not await db.get_product(pid):
        await message.answer("Product not found.")
        return
    await state.update_data(product_id=pid)
    await state.set_state(AdminFlow.add_tier_qty)
    await message.answer("Minimum quantity? (e.g. 10)")


@router.message(AdminFlow.add_tier_qty)
async def addtier_qty(message: Message, state: FSMContext) -> None:
    try:
        qty = int(message.text.strip())
        if qty < 2:
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer("Send a whole number ≥ 2.")
        return
    await state.update_data(min_qty=qty)
    await state.set_state(AdminFlow.add_tier_price)
    await message.answer(
        "Price per unit at this tier in USDT? (e.g. 1.80)\n\n"
        "<i>This is the discounted each-price for buying that many or more.</i>"
    )


@router.message(AdminFlow.add_tier_price)
async def addtier_price(message: Message, state: FSMContext) -> None:
    try:
        unit_price = round(float(message.text.strip().replace(",", ".")), 2)
        if unit_price <= 0:
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer("Send a positive price in USDT (e.g. 1.80).")
        return
    data = await state.get_data()
    pid = int(data["product_id"])
    min_qty = int(data["min_qty"])
    product = await db.get_product(pid)
    base = float(product["base_price"]) if product else 0.0
    await db.set_pricing_tier(pid, min_qty, unit_price)
    await state.clear()
    note = ""
    if base and unit_price >= base:
        note = (
            f"\n\n⚠️ Note: this is not lower than the base price "
            f"(${base:.2f}), so it isn't really a discount."
        )
    await message.answer(
        f"✅ Tier saved: buy {min_qty}+ → <b>${unit_price:.2f}</b> each.{note}"
    )


# ------------------- Coupons -------------------

@router.message(Command("addcoupon"))
async def cmd_addcoupon(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.set_state(AdminFlow.add_coupon_code)
    await message.answer("Coupon code? (e.g. SUMMER20)")


@router.message(AdminFlow.add_coupon_code)
async def addc_code(message: Message, state: FSMContext) -> None:
    code = (message.text or "").strip().upper()
    if not code:
        await message.answer("Send a non-empty code.")
        return
    await state.update_data(code=code)
    await state.set_state(AdminFlow.add_coupon_kind)
    await message.answer("Type? Reply 'percent' or 'fixed'.")


@router.message(AdminFlow.add_coupon_kind)
async def addc_kind(message: Message, state: FSMContext) -> None:
    kind = (message.text or "").strip().lower()
    if kind not in ("percent", "fixed"):
        await message.answer("Reply 'percent' or 'fixed'.")
        return
    await state.update_data(kind=kind)
    await state.set_state(AdminFlow.add_coupon_value)
    await message.answer(
        "Value? For percent send 0-90; for fixed send a USDT amount."
    )


@router.message(AdminFlow.add_coupon_value)
async def addc_value(message: Message, state: FSMContext) -> None:
    try:
        val = float(message.text.strip().replace(",", "."))
        if val <= 0:
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer("Send a positive number.")
        return
    data = await state.get_data()
    await state.clear()
    cid = await db.create_coupon(data["code"], data["kind"], val)
    await message.answer(
        f"✅ Coupon <code>{escape(data['code'])}</code> created (id {cid})."
    )


@router.message(Command("coupons"))
async def cmd_coupons(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    rows = await db.list_coupons()
    if not rows:
        await message.answer("No coupons.")
        return
    lines = ["<b>Coupons</b>"]
    for r in rows:
        v = f"{r['value']:.0f}%" if r["kind"] == "percent" else f"${r['value']:.2f}"
        max_uses = "∞" if not r["max_uses"] else r["max_uses"]
        on = "✅" if r["active"] else "❌"
        lines.append(
            f"{on} <code>{escape(r['code'])}</code> — {r['kind']} {v} — "
            f"used {r['used_count']}/{max_uses}"
        )
    await message.answer("\n".join(lines))


# ------------------- Stats -------------------

@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    # Exclude admin accounts' own test orders / self-top-ups from the financial
    # figures, exactly as the web dashboard does, so /stats and the dashboard
    # agree. Without this an admin's free unlimited-wallet test buys and self
    # deposits silently inflate revenue + deposit totals. (review H8)
    admin_ids = list(settings.ADMIN_IDS or [])
    excl = ""
    excl_params: tuple = ()
    if admin_ids:
        ph = ",".join("?" for _ in admin_ids)
        excl = f" AND user_id NOT IN (SELECT id FROM users WHERE tg_id IN ({ph}))"
        excl_params = tuple(admin_ids)

    from db import get_db
    async with get_db() as d:
        if admin_ids:
            ph = ",".join("?" for _ in admin_ids)
            cur = await d.execute(
                f"SELECT COUNT(*) AS n FROM users WHERE tg_id NOT IN ({ph})",
                tuple(admin_ids),
            )
        else:
            cur = await d.execute("SELECT COUNT(*) AS n FROM users")
        users_n = (await cur.fetchone())["n"]
        cur = await d.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(payable),0) AS rev "
            "FROM orders WHERE status = 'delivered'" + excl,
            excl_params,
        )
        r = await cur.fetchone()
        orders_n, revenue = r["n"], float(r["rev"])
        cur = await d.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(unique_amount),0) AS dep "
            "FROM payments WHERE status = 'confirmed'" + excl,
            excl_params,
        )
        r = await cur.fetchone()
        dep_n, dep_total = r["n"], float(r["dep"])
        cur = await d.execute("SELECT COUNT(*) AS n FROM stock_items WHERE sold = 0")
        stock_n = (await cur.fetchone())["n"]
    await message.answer(
        "📊 <b>Stats</b>\n\n"
        f"Users: <b>{users_n}</b>\n"
        f"Delivered orders: <b>{orders_n}</b>\n"
        f"Revenue: <b>${revenue:.2f}</b>\n"
        f"Confirmed payments: <b>{dep_n}</b> (${dep_total:.2f})\n"
        f"Unsold stock items: <b>{stock_n}</b>"
    )


# ------------------- Broadcast -------------------

@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.set_state(AdminFlow.broadcast_text)
    await message.answer("Send the message to broadcast.")


@router.message(AdminFlow.broadcast_text)
async def msg_broadcast(message: Message, state: FSMContext, bot: Bot) -> None:
    await state.clear()
    text = message.text or ""
    if not text.strip():
        await message.answer("Empty. Aborted.")
        return
    from db import get_db
    async with get_db() as d:
        cur = await d.execute("SELECT tg_id FROM users")
        rows = await cur.fetchall()
    ok = fail = 0
    for r in rows:
        try:
            await bot.send_message(int(r["tg_id"]), text)
            ok += 1
        except Exception as e:
            log.info("broadcast: failed to %s: %s", r["tg_id"], e)
            fail += 1
    await message.answer(f"Broadcast done. Delivered {ok}, failed {fail}.")


# ------------------- Restock broadcast -------------------

@router.message(Command("notifyrestock"))
async def cmd_notifyrestock(message: Message, bot: Bot) -> None:
    if not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Usage: /notifyrestock <product_id> <items_added>")
        return
    try:
        pid = int(parts[1])
        n = int(parts[2])
    except ValueError:
        await message.answer("Usage: /notifyrestock <product_id> <items_added>")
        return
    product = await db.get_product(pid)
    if not product:
        await message.answer("Product not found.")
        return
    await broadcast_restock(bot, product, n)
    await message.answer("Broadcast queued.")


# broadcast_restock now lives in services/broadcast.py so the web dashboard
# can call the same code path (see webapp/app.py products_stock route).


# ------------------- Set price (with price-drop broadcast) -------------------

@router.message(Command("setprice"))
async def cmd_setprice(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    products = await db.list_products(active_only=False, limit=200)
    if not products:
        await message.answer("No products yet. Create one with /addproduct.")
        return
    lines = ["Reply with the product ID to change price for:"]
    for p in products:
        lines.append(
            f"#{p['id']} — {escape(p['name'])} — "
            f"${float(p['base_price']):.2f}/{p['unit_label']}"
        )
    await state.set_state(AdminFlow.set_price_pick_product)
    await message.answer("\n".join(lines))


@router.message(AdminFlow.set_price_pick_product)
async def setprice_pick(message: Message, state: FSMContext) -> None:
    try:
        pid = int(message.text.strip().lstrip("#"))
    except (ValueError, AttributeError):
        await message.answer("Send the product ID number.")
        return
    product = await db.get_product(pid)
    if not product:
        await message.answer("Product not found.")
        return
    await state.update_data(product_id=pid, old_price=float(product["base_price"]))
    await state.set_state(AdminFlow.set_price_amount)
    await message.answer(
        f"<b>{escape(product['name'])}</b> is currently "
        f"${float(product['base_price']):.2f} USDT.\n\n"
        f"Send the new price in USDT (e.g. 17.50). "
        f"If it's lower than the current price, a Price Drop! card is "
        f"broadcast to every user."
    )


@router.message(AdminFlow.set_price_amount)
async def setprice_save(message: Message, state: FSMContext, bot: Bot) -> None:
    try:
        new_price = round(float(message.text.strip().replace(",", ".")), 2)
        if new_price <= 0:
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer("Send a positive number (e.g. 17.50).")
        return
    data = await state.get_data()
    pid = int(data["product_id"])
    await state.clear()

    # Re-read the CURRENT price right before updating instead of trusting the
    # value snapshotted when the admin first picked the product — a concurrent
    # web/admin edit could otherwise make the broadcast announce a false drop. (review L)
    current = await db.get_product(pid)
    if not current:
        await message.answer("Product disappeared mid-edit. Try /products.")
        return
    old_price = float(current["base_price"])

    await db.update_product(pid, base_price=new_price)
    product = await db.get_product(pid)
    if not product:
        await message.answer("Product disappeared mid-edit. Try /products.")
        return

    if new_price < old_price:
        await message.answer(
            f"✅ Price updated: ${old_price:.2f} → ${new_price:.2f}. "
            f"Broadcasting Price Drop! to all users now."
        )
        # Same fire-and-forget pattern as /addstock so the admin chat
        # doesn't sit idle while the queue drains at 25 msgs/sec. (review M2)
        _spawn(broadcast_price_drop(bot, product, old_price, new_price))
    elif new_price > old_price:
        await message.answer(
            f"✅ Price updated: ${old_price:.2f} → ${new_price:.2f}. "
            f"No broadcast (price went up)."
        )
    else:
        await message.answer(
            f"Price is unchanged at ${new_price:.2f}. No broadcast."
        )


# ------------------- Grab a third-party emoji pack -------------------

@router.message(Command("grabemoji"))
async def cmd_grabemoji(message: Message) -> None:
    """Pull a third-party custom-emoji pack into keyboards/mix_emoji_ids.json.

    Usage: forward any message that contains a custom emoji from the target
    pack (or just type/paste one of those emojis after /grabemoji), and the
    bot will resolve the set via getCustomEmojiStickers + getStickerSet and
    save every ID. After this the bot can use those emojis everywhere via
    keyboards.mix_icons.

    This is the painless setup path for owner-shared packs like
    @OrderEmoji's 'Mix Emoji ✨' — no need to know the pack's URL slug."""
    if not is_admin(message.from_user.id):
        return

    # Read custom-emoji entities from BOTH the /grabemoji message itself
    # (admin typed/pasted the emoji inline) AND the replied-to message
    # (admin replied to a forward containing the emoji). The help text
    # advertises both flows, so the handler must support both.
    entities: list[MessageEntity] = []
    entities += list(message.entities or [])
    entities += list(message.caption_entities or [])
    if message.reply_to_message is not None:
        entities += list(message.reply_to_message.entities or [])
        entities += list(message.reply_to_message.caption_entities or [])

    custom_ids: list[str] = []
    seen: set[str] = set()
    for ent in entities:
        if ent.type != "custom_emoji" or not ent.custom_emoji_id:
            continue
        cid = str(ent.custom_emoji_id)
        if cid in seen:
            continue
        seen.add(cid)
        custom_ids.append(cid)

    if not custom_ids:
        await message.answer(
            "Send /grabemoji together with one of the custom emojis from the "
            "pack you want to grab — either type the emoji in the same "
            "message, or reply with /grabemoji to a message containing it.\n\n"
            "<i>Tip: forward one announcement from the bot whose look you "
            "want to copy.</i>"
        )
        return

    from services.emoji_pack import grab_pack_by_custom_emoji_id

    seen_sets: dict[str, dict] = {}
    for cid in custom_ids:
        try:
            result = await grab_pack_by_custom_emoji_id(bot=message.bot, custom_emoji_id=cid)
        except Exception as e:
            log.info("grabemoji: lookup failed for %s: %s", cid, e)
            continue
        if not result:
            continue
        name = result.get("name") or "?"
        if name not in seen_sets:
            seen_sets[name] = result

    if not seen_sets:
        await message.answer(
            "Couldn't resolve any of those custom emojis to a sticker set. "
            "Try a different emoji from the pack."
        )
        return

    # Each forwarded message may reference multiple packs; save the first
    # (largest) one as the primary mix_emoji_ids.json. Tell the admin about
    # every pack we saw.
    primary = max(seen_sets.values(), key=lambda r: r.get("count", 0))
    summary_lines = [
        f"✅ <b>Saved pack:</b> {escape(primary.get('title') or '?')}",
        f"   short_name: <code>{escape(primary.get('name') or '?')}</code>",
        f"   emojis: <b>{primary.get('count', 0)}</b>",
    ]
    if len(seen_sets) > 1:
        summary_lines.append("")
        summary_lines.append("Other packs referenced in that message:")
        for name, r in seen_sets.items():
            if name == primary.get("name"):
                continue
            summary_lines.append(
                f"   • {escape(r.get('title') or '?')} "
                f"(<code>{escape(name)}</code>, {r.get('count', 0)} emojis)"
            )
    summary_lines.append("")
    summary_lines.append(
        "<i>Restart the bot or run a flow with custom emojis — they're "
        "live in welcome / profile / delivery messages now.</i>"
    )
    await message.answer("\n".join(summary_lines))
