from __future__ import annotations

import asyncio
import logging
import time
from html import escape

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import db
import handlers
import i18n
from config import get_settings
from keyboards import hype_emoji as hype
from services.binance import BinanceError
from services.payments import close_binance, get_binance, poll_payments
from services.telegram_delivery import code_delivery_messages

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)
log = logging.getLogger("bot")


# Module-level handle to the live Bot, so the webapp (running in the same
# event loop, see run.py) can dispatch broadcasts without re-creating a Bot.
_running_bot: Bot | None = None


def get_running_bot() -> Bot | None:
    return _running_bot


class HypeBot(Bot):
    """Bot wrapper that upgrades known plain emoji in outgoing HTML text."""

    @staticmethod
    def _hype_method(method):
        for key in ("text", "caption"):
            try:
                value = getattr(method, key)
            except AttributeError:
                continue
            if value is None:
                continue
            try:
                setattr(method, key, hype.hype_text(value))
            except Exception:
                continue
        return method

    @staticmethod
    def _hype_arg(args: tuple, kwargs: dict, *, key: str, index: int) -> tuple[tuple, dict]:
        args_list = list(args)
        if key in kwargs:
            kwargs[key] = hype.hype_text(kwargs[key])
        elif len(args_list) > index:
            args_list[index] = hype.hype_text(args_list[index])
        return tuple(args_list), kwargs

    @staticmethod
    def _hype_caption(args: tuple, kwargs: dict) -> tuple[tuple, dict]:
        return HypeBot._hype_arg(args, kwargs, key="caption", index=2)

    async def __call__(self, method, request_timeout=None):
        return await super().__call__(
            self._hype_method(method),
            request_timeout=request_timeout,
        )

    async def send_message(self, *args, **kwargs):
        args, kwargs = self._hype_arg(args, kwargs, key="text", index=1)
        return await super().send_message(*args, **kwargs)

    async def edit_message_text(self, *args, **kwargs):
        args, kwargs = self._hype_arg(args, kwargs, key="text", index=0)
        return await super().edit_message_text(*args, **kwargs)

    async def send_document(self, *args, **kwargs):
        args, kwargs = self._hype_caption(args, kwargs)
        return await super().send_document(*args, **kwargs)

    async def send_photo(self, *args, **kwargs):
        args, kwargs = self._hype_caption(args, kwargs)
        return await super().send_photo(*args, **kwargs)

    async def send_video(self, *args, **kwargs):
        args, kwargs = self._hype_caption(args, kwargs)
        return await super().send_video(*args, **kwargs)

    async def send_animation(self, *args, **kwargs):
        args, kwargs = self._hype_caption(args, kwargs)
        return await super().send_animation(*args, **kwargs)


async def _notify_payment_event(bot: Bot, event: dict) -> None:
    if event.get("unmatched"):
        text = (
            "⚠️ <b>Unmatched deposit received</b>\n\n"
            f"Deposit ID: <b>#{event['deposit_id']}</b>\n"
            f"Amount: <b>{float(event['amount']):.4f} {escape(str(event['coin']))}</b>\n"
            f"Network: <code>{escape(str(event['network']))}</code>\n"
            f"TXID: <code>{escape(str(event['txid']))}</code>\n\n"
            "Funds were verified but could not be safely matched. Review manually."
        )
        for admin_id in get_settings().ADMIN_IDS or []:
            try:
                await bot.send_message(int(admin_id), text)
            except Exception as ex:
                log.info("notify unmatched deposit failed for admin %s: %s", admin_id, ex)
        return

    if "expired" in event:
        e = event["expired"]
        user = await db.get_user(int(e["user_id"]))
        if not user:
            return
        # Localize the poller's outgoing message to the recipient's language
        # (handlers get this from the middleware; background sends do it here).
        with i18n.use_language(user["language"]):
            try:
                await bot.send_message(
                    int(user["tg_id"]),
                    i18n.t("notify.expired", id=e['id']),
                )
            except Exception as ex:
                log.info("notify expired failed for %s: %s", user["tg_id"], ex)
        return

    user = await db.get_user(int(event["user_id"]))
    if not user:
        return
    tg_id = int(user["tg_id"])
    amount = float(event["amount"])
    coin = event["coin"]

    with i18n.use_language(user["language"]):
        if event.get("order_id"):
            product = None
            order = await db.get_order(int(event["order_id"]))
            if order:
                product = await db.get_product(int(order["product_id"]))

            if event.get("refunded"):
                try:
                    await bot.send_message(tg_id, i18n.t(
                        "notify.refunded", id=event['payment_id'],
                        amount=f"{amount:.4f}", coin=coin))
                except Exception as e:
                    log.info("notify refund failed: %s", e)
                return

            contents = event.get("contents") or []
            # Product name stays as the owner entered it (data), only escaped.
            name = escape(product["name"]) if product else f"order #{order['id']}"
            instructions = (product["delivery_instructions"] or "") if product else ""
            head = i18n.t(
                "notify.delivered", name=name, id=order['id'],
                amount=f"{amount:.4f}", coin=coin, txid=escape(event['txid']))
            try:
                for msg in code_delivery_messages(head, contents, instructions=instructions):
                    await bot.send_message(tg_id, msg)
                # Follow-up with the post-delivery actions (incl. "Report an issue"),
                # since code messages carry no keyboard.
                from keyboards.payment import delivery_keyboard
                await bot.send_message(
                    tg_id,
                    i18n.t("notify.help"),
                    reply_markup=delivery_keyboard(int(order["id"])),
                )
            except Exception as e:
                log.info("notify delivery failed: %s", e)
            return

        # Wallet top-up
        try:
            await bot.send_message(
                tg_id,
                i18n.t("notify.deposit", amount=f"{amount:.4f}", coin=coin,
                       txid=escape(event['txid'])),
            )
        except Exception as e:
            log.info("notify topup failed: %s", e)


async def check_clock_skew() -> None:
    """Detect and warn if the local clock is too far from Binance's server time.
    Signed Binance API calls fail with 'outside recvWindow' if drift > ~10s, and
    the failure is silent in poll_payments. Spot-check at startup so the
    operator sees the problem immediately."""
    settings = get_settings()
    if not (settings.BINANCE_API_KEY and settings.BINANCE_API_SECRET):
        return
    try:
        server_ms = await get_binance().server_time_ms()
    except BinanceError as e:
        log.warning("clock-skew check: could not reach Binance: %s", e)
        return
    local_ms = int(time.time() * 1000)
    drift_ms = abs(server_ms - local_ms)
    if drift_ms > 5000:
        log.warning(
            "⚠️  Local clock is %d ms off Binance server time. "
            "Signed API calls may fail. Run `timedatectl status` and enable NTP.",
            drift_ms,
        )
    else:
        log.info("Clock skew vs Binance: %d ms (OK)", drift_ms)


async def cleanup_tick() -> None:
    """Daily-ish housekeeping — drops orders the user abandoned at the qty/summary step."""
    try:
        n = await db.cleanup_stale_drafts(older_than_seconds=24 * 3600)
        if n:
            log.info("cleanup_stale_drafts: removed %d stale draft orders", n)
    except Exception:
        log.exception("cleanup_tick failed")


async def auto_payment_tick(bot: Bot) -> None:
    try:
        events = await poll_payments()
    except Exception:
        log.exception("auto_payment_tick failed")
        return
    for ev in events:
        await _notify_payment_event(bot, ev)


async def setup_bot_commands(bot: Bot) -> None:
    from aiogram.types import BotCommand
    await bot.set_my_commands([
        BotCommand(command="start", description="Start the bot"),
        BotCommand(command="shop", description="Browse products"),
        BotCommand(command="deposit", description="Add funds to wallet"),
        BotCommand(command="profile", description="View your profile"),
    ])


class LanguageMiddleware(BaseMiddleware):
    """Resolve the sender's chosen UI language once per update and run the
    handler under it (via the i18n contextvar), so every t() call in handlers /
    keyboards / text-builders is automatically localized. One cheap lookup per
    update; defaults to English when unknown."""

    async def __call__(self, handler, event, data):
        lang = i18n.DEFAULT
        user = getattr(event, "from_user", None)
        if user is not None:
            try:
                row = await db.get_user_by_tg(user.id)
                if row is not None and row["language"]:
                    lang = row["language"]
            except Exception:
                pass
        with i18n.use_language(lang):
            return await handler(event, data)


async def build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    # Localize every buyer-facing handler by the sender's language.
    dp.message.middleware(LanguageMiddleware())
    dp.callback_query.middleware(LanguageMiddleware())

    dp.include_router(handlers.start.router)
    dp.include_router(handlers.shop.router)
    dp.include_router(handlers.payment.router)
    dp.include_router(handlers.deposit.router)
    dp.include_router(handlers.profile.router)
    dp.include_router(handlers.support.router)
    dp.include_router(handlers.ticket.router)
    dp.include_router(handlers.language.router)
    dp.include_router(handlers.refer.router)
    dp.include_router(handlers.api.router)
    dp.include_router(handlers.admin.router)

    # Swallow the no-op (paging indicator) callback so the loading spinner clears
    @dp.callback_query(F.data == "noop")
    async def _noop(cb: CallbackQuery) -> None:
        await cb.answer()

    return dp


async def run_telegram_bot() -> None:
    global _running_bot  # single declaration; covers both set + clear below
    settings = get_settings()
    if not settings.BOT_TOKEN:
        raise SystemExit("BOT_TOKEN missing — fill .env first.")
    await db.init_db()

    # Recover any deposits caught in the confirm→credit/deliver crash window
    # (confirmed top-ups never credited, orders stuck in 'paid'). Idempotent.
    try:
        from services.delivery import reconcile_unsettled_payments
        recon = await reconcile_unsettled_payments()
        if recon.get("topups_credited") or recon.get("orders_redelivered") or recon.get("errors"):
            log.info("startup reconcile: %s", recon)
    except Exception:
        log.exception("startup reconcile failed (non-fatal)")

    bot = HypeBot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    _running_bot = bot
    dp = await build_dispatcher()

    await check_clock_skew()

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        auto_payment_tick, "interval",
        seconds=max(10, settings.POLL_INTERVAL_SECONDS),
        args=[bot], id="auto_payment_tick",
        coalesce=True, max_instances=1,
    )
    scheduler.add_job(
        cleanup_tick, "interval", hours=1,
        id="cleanup_tick", coalesce=True, max_instances=1,
    )
    scheduler.start()

    await setup_bot_commands(bot)
    log.info("Bot starting as @%s", (await bot.get_me()).username)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        _running_bot = None
        scheduler.shutdown(wait=False)
        await close_binance()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(run_telegram_bot())
