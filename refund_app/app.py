from __future__ import annotations

import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import get_settings
from services.refunds import refund_lookup_for_identifier


BASE = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE / "templates"))

LOOKUP_WINDOW_SECONDS = 300
LOOKUP_MAX_ATTEMPTS = 20
_lookup_attempts: dict[str, deque[float]] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip() or "unknown"
    return request.client.host if request.client else "unknown"


def _lookup_blocked(ip: str) -> tuple[bool, int]:
    now = time.time()
    bucket = _lookup_attempts[ip]
    while bucket and bucket[0] < now - LOOKUP_WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= LOOKUP_MAX_ATTEMPTS:
        wait = int(LOOKUP_WINDOW_SECONDS - (now - bucket[0]))
        return True, max(wait, 1)
    bucket.append(now)
    return False, 0


def _support_url() -> str:
    settings = get_settings()
    support = (settings.SUPPORT_USERNAME or "").strip().lstrip("@")
    if support:
        return f"https://t.me/{support}"
    bot = (settings.BOT_USERNAME or "").strip().lstrip("@")
    return f"https://t.me/{bot}" if bot else ""


def _common_context(request: Request, **extra: Any) -> dict[str, Any]:
    settings = get_settings()
    ctx: dict[str, Any] = {
        "request": request,
        "shop": settings.SHOP_NAME,
        "window_days": settings.REFUND_WINDOW_DAYS,
        "support_url": _support_url(),
        "base_path": "/refund",
    }
    ctx.update(extra)
    return ctx


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=f"{settings.SHOP_NAME} Refund Calculator")
    app.mount("/refund/static", StaticFiles(directory=str(BASE / "static")), name="refund-static")

    @app.get("/", response_class=HTMLResponse)
    @app.get("/refund", response_class=HTMLResponse)
    @app.get("/refund/", response_class=HTMLResponse)
    async def form(request: Request):
        return templates.TemplateResponse(
            request,
            "refund.html",
            context=_common_context(request, lookup=None, telegram_id=""),
        )

    @app.post("/", response_class=HTMLResponse)
    @app.post("/refund", response_class=HTMLResponse)
    @app.post("/refund/", response_class=HTMLResponse)
    async def lookup(request: Request, telegram_id: str = Form(...)):
        blocked, wait = _lookup_blocked(_client_ip(request))
        raw_id = (telegram_id or "").strip()
        if blocked:
            return templates.TemplateResponse(
                request,
                "refund.html",
                context=_common_context(
                    request,
                    lookup=None,
                    telegram_id=raw_id,
                    error=f"Too many lookups. Try again in {wait} seconds.",
                ),
                status_code=429,
            )
        if not raw_id or raw_id == "@":
            return templates.TemplateResponse(
                request,
                "refund.html",
                context=_common_context(
                    request,
                    lookup=None,
                    telegram_id=raw_id,
                    error="Enter your Telegram ID or @username.",
                ),
                status_code=400,
            )
        result = await refund_lookup_for_identifier(
            raw_id,
        )
        return templates.TemplateResponse(
            request,
            "refund.html",
            context=_common_context(request, lookup=result, telegram_id=raw_id),
        )

    return app
