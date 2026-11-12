"""
Runs the Telegram bot and the FastAPI admin dashboard in one event loop.

Use this for local dev and for production (under systemd or Docker).
For separate processes, run `python bot.py` and `uvicorn webapp.app:create_app --factory` independently.
"""
from __future__ import annotations

import asyncio
import logging

import uvicorn

from bot import run_telegram_bot
from config import get_settings
from webapp.app import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)
log = logging.getLogger("run")


async def run_webapp() -> None:
    settings = get_settings()
    config = uvicorn.Config(
        create_app(),
        host=settings.WEB_HOST,
        port=settings.WEB_PORT,
        log_level="info",
        # Off: access logs on a money panel record request paths/IPs to a
        # long-lived file with no rotation. (review info)
        access_log=False,
        # Honour X-Forwarded-For from the trusted nginx proxy so the login
        # rate-limiter buckets by the real client IP, not 127.0.0.1. (review M8)
        proxy_headers=True,
        forwarded_allow_ips=settings.WEB_FORWARDED_ALLOW_IPS,
    )
    server = uvicorn.Server(config)
    log.info("Admin dashboard starting on http://%s:%s",
             settings.WEB_HOST, settings.WEB_PORT)
    await server.serve()


async def main() -> None:
    bot_task = asyncio.create_task(run_telegram_bot(), name="telegram-bot")
    web_task = asyncio.create_task(run_webapp(), name="admin-web")
    # FIRST_COMPLETED, not FIRST_EXCEPTION: uvicorn intercepts SIGTERM and
    # returns *without* raising, so FIRST_EXCEPTION would block on the still-
    # polling bot task until systemd's 90s SIGKILL. With FIRST_COMPLETED the
    # web task's clean return immediately triggers a cancel of the bot task.
    done, pending = await asyncio.wait(
        {bot_task, web_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    for t in pending:
        t.cancel()
    # Wait for cancellations so each task's finally-block runs (closes the
    # bot session, binance client, scheduler). Swallow CancelledError.
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    for t in done:
        if t.cancelled():
            continue
        exc = t.exception()
        if exc is not None:
            raise exc


if __name__ == "__main__":
    asyncio.run(main())
