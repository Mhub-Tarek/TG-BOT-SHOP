"""
Browser admin dashboard.

Login uses a single shared password (WEB_ADMIN_PASSWORD env var). Sessions are
signed cookies (no DB rows), keyed by WEB_SESSION_SECRET.

Routes:
  GET  /                       -> redirect to /dashboard or /login
  GET/POST /login
  GET  /logout
  GET  /dashboard              -> stats + recent orders
  GET  /products
  POST /products/new
  POST /products/{id}/edit
  POST /products/{id}/toggle
  POST /products/{id}/delete
  POST /products/{id}/stock    -> bulk stock paste
  POST /products/{id}/stock/{item_id}/delete
  POST /products/{id}/stock/delete-selected
  POST /products/{id}/tier
  POST /products/{id}/tier/delete
  GET  /orders
  GET  /payments
  GET  /users
  POST /users/{id}/balance
  GET  /coupons
  POST /coupons/new
  POST /coupons/{id}/toggle
  GET  /broadcast
  POST /broadcast
  POST /broadcast/bulk-pricing
  POST /broadcast/flash-sale
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import math
import time
from collections import defaultdict, deque
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import httpx

from fastapi import (Cookie, Depends, FastAPI, Form, HTTPException, Request,
                     Response)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer

import db
import i18n
from config import get_settings
from keyboards.brands import BRAND_LABELS, admin_preview_url, brand_choices_sorted
from webapp.reseller_api import create_reseller_api_app

settings = get_settings()
BASE = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE / "templates"))
log = logging.getLogger("admin")


# Strong references to in-flight fire-and-forget broadcast/notify tasks. The
# event loop only holds a WEAK reference to tasks, so a long-running broadcast
# (which sleeps between every recipient) could be garbage-collected and
# silently cancelled mid-send. Keep a ref until it finishes, and log any
# exception so a dead task is visible. (review M2)
_BG_TASKS: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)

    def _done(t: asyncio.Task) -> None:
        _BG_TASKS.discard(t)
        if not t.cancelled() and t.exception() is not None:
            log.error("background task failed", exc_info=t.exception())

    task.add_done_callback(_done)


def _datetime_filter(ts) -> str:
    # Render every admin-dashboard timestamp in Africa/Casablanca local time
    # (single source of truth in db.casa_format).
    return db.casa_format(ts)


templates.env.filters["datetime"] = _datetime_filter


def render(request: Request, name: str, context: dict[str, Any], **kwargs: Any):
    """Starlette 0.41+ expects (request, name, context=...) instead of (name, {request, ...})."""
    return templates.TemplateResponse(request, name, context=context, **kwargs)


def _serializer() -> URLSafeSerializer:
    return URLSafeSerializer(settings.WEB_SESSION_SECRET, salt="admin-session")


SESSION_TTL_SECONDS = 60 * 60 * 24 * 7   # 7 days


def _set_session(resp: Response) -> None:
    token = _serializer().dumps({"admin": True, "iat": int(time.time())})
    resp.set_cookie(
        "admin_session", token,
        httponly=True, samesite="lax",
        secure=settings.WEB_SECURE_COOKIES,
        max_age=SESSION_TTL_SECONDS,
    )


def _logged_in(admin_session: str | None) -> bool:
    if not admin_session:
        return False
    try:
        payload = _serializer().loads(admin_session)
    except BadSignature:
        return False
    if not payload.get("admin"):
        return False
    iat = int(payload.get("iat", 0))
    if iat and int(time.time()) - iat > SESSION_TTL_SECONDS:
        return False
    return True


# ---- Simple per-IP login rate limiter (in-memory) -------------------------
_login_attempts: dict[str, deque[float]] = defaultdict(deque)


def _login_blocked(ip: str) -> tuple[bool, int]:
    """Returns (is_blocked, seconds_until_unblock)."""
    now = time.time()
    window = settings.WEB_LOGIN_WINDOW_SECONDS
    bucket = _login_attempts[ip]
    while bucket and bucket[0] < now - window:
        bucket.popleft()
    # Evict empty buckets so a stream of unique source IPs can't slowly grow
    # the dict forever.
    if not bucket:
        _login_attempts.pop(ip, None)
        return False, 0
    if len(bucket) >= settings.WEB_LOGIN_MAX_ATTEMPTS:
        wait = int(window - (now - bucket[0]))
        return True, max(wait, 1)
    return False, 0


def _record_login_attempt(ip: str) -> None:
    _login_attempts[ip].append(time.time())


def _clear_login_attempts(ip: str) -> None:
    _login_attempts.pop(ip, None)


def _enforce_credentials() -> None:
    """Refuse to start the admin panel while the example credentials shipped in
    .env.example are still in place. The session cookie is a stateless
    itsdangerous token signed with WEB_SESSION_SECRET, so a known-default secret
    lets anyone forge an admin cookie with zero credentials; a default password
    is trivially guessable. Fail closed instead of merely warning. (review H1)"""
    bad = settings.using_default_web_secrets()
    if bad:
        raise RuntimeError(
            "Refusing to start the admin dashboard: "
            + ", ".join(bad)
            + " still set to the shipped default(s). Set strong values in .env "
            "(e.g. `python -c \"import secrets;print(secrets.token_urlsafe(32))\"`) "
            "and restart."
        )


def create_app() -> FastAPI:
    _enforce_credentials()
    app = FastAPI(
        title=f"{settings.SHOP_NAME} — Admin",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    # ---- static (CSS) ----
    static_dir = BASE / "static"
    static_dir.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    brand_logos_dir = BASE.parent / "assets" / "brand-logos"
    if brand_logos_dir.is_dir():
        app.mount("/brand-logos", StaticFiles(directory=str(brand_logos_dir)), name="brand-logos")
    app.mount("/api", create_reseller_api_app(), name="reseller-api")

    # ---- auth middleware via dependency injection ----
    def auth_redirect(admin_session: str | None = Cookie(default=None)):
        if not _logged_in(admin_session):
            raise HTTPException(status_code=302, headers={"Location": "/login"})

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        if exc.status_code == 302 and "Location" in (exc.headers or {}):
            return RedirectResponse(url=exc.headers["Location"], status_code=302)
        return HTMLResponse(
            f"<h1>{exc.status_code}</h1><p>{escape(str(exc.detail))}</p>",
            status_code=exc.status_code,
        )

    @app.get("/", response_class=HTMLResponse)
    async def root(admin_session: str | None = Cookie(default=None)):
        if _logged_in(admin_session):
            return RedirectResponse("/dashboard", status_code=302)
        return RedirectResponse("/login", status_code=302)

    # ---------- Login ----------

    @app.get("/login", response_class=HTMLResponse)
    async def login_form(request: Request, error: str | None = None):
        return render(
            request, "login.html",
            {"error": error, "shop": settings.SHOP_NAME},
        )

    @app.post("/login", response_class=HTMLResponse)
    async def login_submit(request: Request, password: str = Form(...)):
        ip = request.client.host if request.client else "unknown"
        blocked, wait = _login_blocked(ip)
        if blocked:
            return render(
                request, "login.html",
                {"error": f"Too many attempts. Try again in {wait} seconds.",
                 "shop": settings.SHOP_NAME},
                status_code=429,
            )
        # Constant-time compare so the check can't leak the password length /
        # leading bytes via response timing. (review L)
        # Compare UTF-8 BYTES, not str: hmac.compare_digest raises TypeError on
        # any non-ASCII str (so a passphrase with an accent/emoji would 500 every
        # login attempt — a permanent admin lockout). Bytes accept all Unicode
        # and keep the constant-time guarantee. (review: login non-ASCII crash)
        if not hmac.compare_digest(password.encode("utf-8"),
                                   settings.WEB_ADMIN_PASSWORD.encode("utf-8")):
            _record_login_attempt(ip)
            return render(
                request, "login.html",
                {"error": "Wrong password.", "shop": settings.SHOP_NAME},
                status_code=401,
            )
        _clear_login_attempts(ip)
        resp = RedirectResponse("/dashboard", status_code=302)
        _set_session(resp)
        return resp

    @app.get("/logout")
    async def logout():
        resp = RedirectResponse("/login", status_code=302)
        resp.delete_cookie("admin_session",
                           secure=settings.WEB_SECURE_COOKIES, samesite="lax")
        return resp

    # ---------- Dashboard ----------

    @app.get("/dashboard", response_class=HTMLResponse, dependencies=[Depends(auth_redirect)])
    async def dashboard(request: Request):
        from db import get_db
        # Revenue and customer-activity stats exclude transactions from admin
        # accounts (self top-ups, test purchases) so the numbers reflect real
        # customer money. Admin tg_ids come from ADMIN_IDS in .env.
        admin_ids = list(settings.ADMIN_IDS or [])
        admin_filter_sql = ""
        admin_params: tuple = ()
        if admin_ids:
            placeholders = ",".join("?" for _ in admin_ids)
            admin_filter_sql = (
                f" AND user_id NOT IN ("
                f"SELECT id FROM users WHERE tg_id IN ({placeholders}))"
            )
            admin_params = tuple(admin_ids)

        async with get_db() as d:
            async def scalar(q: str, params: tuple = ()) -> Any:
                cur = await d.execute(q, params)
                row = await cur.fetchone()
                if not row:
                    return 0
                return row[0]

            if admin_ids:
                placeholders_u = ",".join("?" for _ in admin_ids)
                users_n = await scalar(
                    f"SELECT COUNT(*) FROM users WHERE tg_id NOT IN ({placeholders_u})",
                    tuple(admin_ids),
                )
            else:
                users_n = await scalar("SELECT COUNT(*) FROM users")
            orders_n = await scalar(
                "SELECT COUNT(*) FROM orders WHERE status = 'delivered'"
                + admin_filter_sql,
                admin_params,
            )
            revenue = await scalar(
                "SELECT COALESCE(SUM(payable),0) FROM orders WHERE status = 'delivered'"
                + admin_filter_sql,
                admin_params,
            )
            payments_n = await scalar(
                "SELECT COUNT(*) FROM payments WHERE status = 'confirmed'"
                + admin_filter_sql,
                admin_params,
            )
            stock_n = await scalar("SELECT COUNT(*) FROM stock_items WHERE sold = 0")
            pending_orders = await scalar(
                "SELECT COUNT(*) FROM orders WHERE status = 'awaiting_payment'"
                + admin_filter_sql,
                admin_params,
            )

        # Profit (revenue − cost of codes sold) for delivered, fully-costed,
        # non-admin orders in the current Morocco-time day / month. See
        # db.profit_for_period for the exclusion semantics.
        daily_profit = await db.profit_for_period("day", exclude_admin_ids=admin_ids or None)
        monthly_profit = await db.profit_for_period("month", exclude_admin_ids=admin_ids or None)
        open_tickets = await db.count_open_tickets(exclude_admin_ids=admin_ids or None)

        recent_orders = await db.all_orders(limit=20, exclude_admin_ids=admin_ids or None)
        recent_ids = [int(o["id"]) for o in recent_orders]
        delivered = await db.delivered_contents_by_orders(recent_ids)
        return render(
            request, "dashboard.html",
            {
                "shop": settings.SHOP_NAME,
                "page": "dashboard",
                "stats": {
                    "users": users_n,
                    "orders": orders_n,
                    "revenue": float(revenue or 0),
                    "daily_profit": float(daily_profit or 0),
                    "monthly_profit": float(monthly_profit or 0),
                    "payments": payments_n,
                    "stock": stock_n,
                    "pending_orders": pending_orders,
                    "open_tickets": open_tickets,
                },
                "recent": recent_orders,
                "delivered": delivered,
            },
        )

    # ---------- Statistics ----------

    # period -> number of buckets to show
    STAT_PERIODS = {"day": 30, "week": 12, "month": 12}
    # period -> lookback window (days) for funnel / top-products / method-mix
    STAT_WINDOW = {"day": 30, "week": 84, "month": 365}

    @app.get("/statistics", response_class=HTMLResponse, dependencies=[Depends(auth_redirect)])
    async def statistics_page(request: Request, period: str = "day"):
        if period not in STAT_PERIODS:
            period = "day"
        count = STAT_PERIODS[period]
        window = STAT_WINDOW[period]
        # Exclude the owner's own (admin) activity from every metric, exactly
        # like the dashboard.
        admin = list(settings.ADMIN_IDS or []) or None

        kpi = await db.kpi_summary(period, exclude_admin_ids=admin)
        new_users = await db.new_users_series(period, count, exclude_admin_ids=admin)
        revenue = await db.revenue_series(period, count, exclude_admin_ids=admin)
        deposits = await db.deposits_series(period, count, exclude_admin_ids=admin)
        starts = await db.events_series(period, count, "start", exclude_admin_ids=admin)
        shop_views = await db.events_series(period, count, "shop_view", exclude_admin_ids=admin)
        product_views = await db.events_series(period, count, "product_view", exclude_admin_ids=admin)
        # Orders are bucketed by created_at (when the order originated) — a
        # cohort-by-creation view; revenue/deposits above use delivered_at /
        # confirmed_at (when money was realised). Intentionally different axes.
        o_delivered = await db.orders_series(period, count, "delivered", exclude_admin_ids=admin)
        o_paid = await db.orders_series(period, count, "paid", exclude_admin_ids=admin)
        o_awaiting = await db.orders_series(period, count, "awaiting_payment", exclude_admin_ids=admin)
        funnel = await db.funnel_counts(days=window, exclude_admin_ids=admin)
        top = await db.top_products(days=window, limit=8, exclude_admin_ids=admin)
        mix = await db.payment_method_mix(days=window, exclude_admin_ids=admin)
        retention = await db.repeat_buyer_stats(exclude_admin_ids=admin)

        profit_day = await db.profit_breakdown_for_period("day", exclude_admin_ids=admin)
        profit_orders = await db.list_profit_order_rows(
            "day", exclude_admin_ids=admin, limit=500,
        )

        # One JSON blob the page's Chart.js init reads.
        charts = {
            "labels": new_users["labels"],
            "new_users": new_users["values"],
            "revenue": revenue["values"],
            "deposits": deposits["values"],
            "starts": starts["values"],
            "shop_views": shop_views["values"],
            "product_views": product_views["values"],
            "orders": {
                "delivered": o_delivered["values"],
                "paid": o_paid["values"],
                "awaiting": o_awaiting["values"],
            },
            "funnel": funnel,
            "mix": [{"method": r["method"], "n": int(r["n"]),
                     "revenue": float(r["revenue"] or 0)} for r in mix],
        }
        return render(
            request, "statistics.html",
            {
                "shop": settings.SHOP_NAME,
                "page": "statistics",
                "period": period,
                "periods": list(STAT_PERIODS.keys()),
                "window_days": window,
                "kpi": kpi,
                "charts": charts,
                "top": top,
                "retention": retention,
                "profit_day": profit_day,
                "profit_orders": profit_orders,
            },
        )

    # ---------- Products ----------

    @app.get("/products", response_class=HTMLResponse, dependencies=[Depends(auth_redirect)])
    async def products_page(request: Request):
        from services.pricing import tier_rows_for_display
        rows = await db.list_products(active_only=False, limit=500)
        brand_list = brand_choices_sorted()
        brand_previews = {slug: admin_preview_url(slug) for slug, _ in brand_list}
        # Batch what used to be 4 queries PER product into 4 total queries. (perf)
        ids = [int(r["id"]) for r in rows]
        counts = await db.stock_counts(ids)
        sold_map = await db.sold_counts(ids)
        stock_map = await db.available_stock_for_products(ids, per_limit=200)
        tiers_map = await db.pricing_tiers_for_products(ids)
        enriched = []
        for r in rows:
            pid = int(r["id"])
            # Resolve each tier to a concrete per-unit price (handles legacy
            # percentage rows) so the template can show plain dollar amounts.
            tier_view = tier_rows_for_display(float(r["base_price"]), tiers_map.get(pid, []))
            bslug = str(r["brand"] or "").strip()
            enriched.append({
                "p": r,
                "stock": counts.get(pid, 0),
                "sold": sold_map.get(pid, 0),
                "stock_rows": stock_map.get(pid, []),
                "tiers": tier_view,
                "brand_label": BRAND_LABELS.get(bslug, ""),
            })
        return render(
            request, "products.html",
            {
                "shop": settings.SHOP_NAME,
                "page": "products",
                "items": enriched,
                "brands": brand_list,
                "brand_previews": brand_previews,
            },
        )

    @app.post("/products/new", dependencies=[Depends(auth_redirect)])
    async def products_new(
        name: str = Form(...),
        emoji: str = Form(""),
        base_price: float = Form(...),
        unit_label: str = Form("code"),
        features: str = Form(""),
        description: str = Form(""),
        icon_custom_emoji_id: str = Form(""),
        brand: str = Form(""),
        delivery_instructions: str = Form(""),
        warranty_days: int = Form(0),
    ):
        new_id = await db.create_product(
            name=name.strip(),
            emoji=emoji.strip(),
            description=description.strip(),
            base_price=float(base_price),
            features=features.strip(),
            unit_label=unit_label.strip() or "code",
            icon_custom_emoji_id=icon_custom_emoji_id.strip(),
            brand=brand.strip().lower(),
            delivery_instructions=delivery_instructions.strip(),
            warranty_days=max(0, int(warranty_days or 0)),
        )
        # Fire-and-forget the "NEW PRODUCT ADDED!" broadcast so every user
        # hears about it (same pattern as the restock announcement). Only runs
        # when the bot process is live in this event loop.
        from bot import get_running_bot
        from services.broadcast import broadcast_new_product
        b = get_running_bot()
        if b is not None:
            product = await db.get_product(new_id)
            if product is not None:
                _spawn(broadcast_new_product(b, product))
        return RedirectResponse("/products", status_code=302)

    @app.post("/products/{pid}/edit", dependencies=[Depends(auth_redirect)])
    async def products_edit(
        pid: int,
        name: str = Form(...),
        emoji: str = Form(""),
        base_price: float = Form(...),
        unit_label: str = Form("code"),
        features: str = Form(""),
        description: str = Form(""),
        icon_custom_emoji_id: str = Form(""),
        brand: str = Form(""),
        delivery_instructions: str = Form(""),
        warranty_days: int = Form(0),
    ):
        # Snapshot the old price BEFORE update so a reduction can fan out a
        # "Price Drop!" broadcast to every user. Skipped on raises/no-change.
        existing = await db.get_product(pid)
        old_price = float(existing["base_price"]) if existing else 0.0
        new_price = float(base_price)

        await db.update_product(
            pid,
            name=name.strip(),
            emoji=emoji.strip(),
            description=description.strip(),
            base_price=new_price,
            features=features.strip(),
            unit_label=unit_label.strip() or "code",
            icon_custom_emoji_id=icon_custom_emoji_id.strip(),
            brand=brand.strip().lower(),
            delivery_instructions=delivery_instructions.strip(),
            warranty_days=max(0, int(warranty_days or 0)),
        )

        # Fire-and-forget the price-drop broadcast so the admin's redirect
        # isn't blocked while messages trickle out at 25/sec. The helper is
        # a no-op if the new price isn't strictly lower than the old price.
        if existing and new_price < old_price:
            from bot import get_running_bot
            from services.broadcast import broadcast_price_drop
            b = get_running_bot()
            if b is not None:
                # Re-fetch so the broadcast renders the freshly-saved name,
                # logo, and emoji (admins often rename/relogo + drop price
                # in the same form submission).
                updated = await db.get_product(pid)
                if updated is not None:
                    _spawn(broadcast_price_drop(b, updated, old_price, new_price))

        return RedirectResponse("/products", status_code=302)

    @app.post("/products/{pid}/move/{direction}", dependencies=[Depends(auth_redirect)])
    async def products_move(pid: int, direction: str):
        # Reorder one step up/down; the shop list + admin list both honour
        # products.sort_order. Anchor back to this product after the redirect.
        await db.reorder_product(pid, direction)
        return RedirectResponse(f"/products#product-{pid}", status_code=302)

    @app.post("/products/{pid}/toggle", dependencies=[Depends(auth_redirect)])
    async def products_toggle(pid: int):
        p = await db.get_product(pid)
        if p:
            await db.update_product(pid, active=0 if p["active"] else 1)
        return RedirectResponse("/products", status_code=302)

    @app.post("/products/{pid}/delete", dependencies=[Depends(auth_redirect)])
    async def products_delete(pid: int):
        await db.delete_product(pid)
        return RedirectResponse("/products", status_code=302)

    @app.post("/products/{pid}/stock", dependencies=[Depends(auth_redirect)])
    async def products_stock(pid: int, items: str = Form(...), cost: str = Form("")):
        lines = items.splitlines()
        # Per-item cost for this batch. Blank or non-numeric → untracked (None),
        # which keeps these codes out of the profit calculation. Negatives are
        # treated as untracked too.
        unit_cost: float | None = None
        raw = (cost or "").strip()
        if raw:
            try:
                parsed = float(raw)
                unit_cost = parsed if parsed >= 0 else None
            except ValueError:
                unit_cost = None
        n = await db.add_stock_items(pid, lines, cost=unit_cost)
        # Fire-and-forget the restock broadcast so the admin's redirect isn't
        # blocked while messages trickle out at 25/sec. Only runs when the
        # bot process is live in the same loop (None-check guards the
        # uvicorn-only standalone path).
        if n > 0:
            from bot import get_running_bot
            from services.broadcast import broadcast_restock
            b = get_running_bot()
            if b is not None:
                product = await db.get_product(pid)
                if product is not None:
                    _spawn(broadcast_restock(b, product, n))
        return RedirectResponse(f"/products#product-{pid}", status_code=302)

    @app.post("/products/{pid}/stock/{item_id}/delete", dependencies=[Depends(auth_redirect)])
    async def products_stock_delete_one(pid: int, item_id: int):
        await db.delete_stock_item(item_id, pid)
        return RedirectResponse(f"/products#product-{pid}", status_code=302)

    @app.post("/products/{pid}/stock/delete-selected", dependencies=[Depends(auth_redirect)])
    async def products_stock_delete_selected(pid: int, request: Request):
        form = await request.form()
        raw = form.getlist("item_ids")
        item_ids = [int(x) for x in raw if str(x).isdigit()]
        if item_ids:
            await db.delete_available_stock(pid, item_ids)
        return RedirectResponse(f"/products#product-{pid}", status_code=302)

    @app.post("/products/{pid}/tier", dependencies=[Depends(auth_redirect)])
    async def products_tier(pid: int,
                            min_qty: int = Form(...),
                            unit_price: float = Form(...)):
        # Bulk tiers are now an explicit per-unit USDT price (not a %). Ignore
        # non-positive prices so a blank/0 submission can't create a junk tier.
        if int(min_qty) >= 2 and float(unit_price) > 0:
            await db.set_pricing_tier(pid, int(min_qty), round(float(unit_price), 2))
        return RedirectResponse(f"/products#product-{pid}", status_code=302)

    @app.post("/products/{pid}/tier/delete", dependencies=[Depends(auth_redirect)])
    async def products_tier_delete(pid: int, min_qty: int = Form(...)):
        await db.delete_pricing_tier(pid, int(min_qty))
        return RedirectResponse(f"/products#product-{pid}", status_code=302)

    # ---------- Orders ----------

    # Order statuses the admin can filter by (matches the orders.status values).
    ORDER_STATUSES = [
        "draft", "awaiting_payment", "paid", "delivered",
        "failed", "expired", "cancelled",
    ]

    @app.get("/orders", response_class=HTMLResponse, dependencies=[Depends(auth_redirect)])
    async def orders_page(request: Request, q: str | None = None,
                          status: str | None = None):
        admin_ids = list(settings.ADMIN_IDS or [])
        status = status if status in ORDER_STATUSES else None
        rows = await db.all_orders(
            limit=500, search=q, status=status,
            exclude_admin_ids=admin_ids or None,
        )
        order_ids = [int(o["id"]) for o in rows]
        delivered = await db.delivered_contents_by_orders(order_ids)
        return render(
            request, "orders.html",
            {
                "shop": settings.SHOP_NAME,
                "page": "orders",
                "rows": rows,
                "delivered": delivered,
                "query": (q or "").strip(),
                "status": status or "",
                "statuses": ORDER_STATUSES,
            },
        )

    # ---------- Tickets (in-app support) ----------

    TICKET_STATUSES = ["open", "answered", "closed"]

    @app.get("/tickets", response_class=HTMLResponse, dependencies=[Depends(auth_redirect)])
    async def tickets_page(request: Request, q: str | None = None,
                           status: str | None = None):
        admin_ids = list(settings.ADMIN_IDS or [])
        status = status if status in TICKET_STATUSES else None
        rows = await db.all_tickets(
            limit=500, search=q, status=status,
            exclude_admin_ids=admin_ids or None,
        )
        return render(
            request, "tickets.html",
            {
                "shop": settings.SHOP_NAME,
                "page": "tickets",
                "rows": rows,
                "query": (q or "").strip(),
                "status": status or "",
                "statuses": TICKET_STATUSES,
            },
        )

    @app.post("/tickets/{tid}/status", dependencies=[Depends(auth_redirect)])
    async def tickets_status(tid: int, new_status: str = Form(...)):
        await db.set_ticket_status(tid, new_status.strip().lower())
        return RedirectResponse("/tickets", status_code=302)

    @app.post("/tickets/{tid}/reply", dependencies=[Depends(auth_redirect)])
    async def tickets_reply(tid: int, reply: str = Form(...)):
        # Cap server-side (the form's maxlength is only a client hint) so a huge
        # reply can't be stored or overflow Telegram's 4096-char DM limit.
        body = (reply or "").strip()[:1000]
        if not body:
            return RedirectResponse("/tickets", status_code=302)
        ticket = await db.reply_ticket(tid, body)
        # DM the buyer the reply (best-effort, tracked background task).
        if ticket and settings.BOT_TOKEN:
            user = await db.get_user(int(ticket["user_id"]))
            if user:
                tg_id = int(user["tg_id"])
                # Dashboard routes run outside the bot's LanguageMiddleware, so
                # the i18n contextvar would default to English here — set it to
                # the buyer's chosen language explicitly. Only the chrome is
                # localized; the admin's reply text is sent as written.
                with i18n.use_language(user["language"]):
                    msg = i18n.t("ticket.reply_dm", id=tid,
                                 order=int(ticket["order_id"]), body=escape(body))

                async def _dm() -> None:
                    try:
                        async with httpx.AsyncClient(timeout=10.0) as client:
                            await client.post(
                                f"https://api.telegram.org/bot{settings.BOT_TOKEN}/sendMessage",
                                json={"chat_id": tg_id, "text": msg, "parse_mode": "HTML"},
                            )
                    except httpx.HTTPError:
                        pass

                _spawn(_dm())
        return RedirectResponse("/tickets", status_code=302)

    # ---------- Payments ----------

    @app.get("/payments", response_class=HTMLResponse, dependencies=[Depends(auth_redirect)])
    async def payments_page(request: Request):
        rows = await db.all_payments(limit=500)
        unmatched = await db.open_unmatched_deposits()
        return render(
            request, "payments.html",
            {
                "shop": settings.SHOP_NAME,
                "page": "payments",
                "rows": rows,
                "unmatched": unmatched,
            },
        )

    @app.post("/payments/unmatched/{dep_id}/credit", dependencies=[Depends(auth_redirect)])
    async def payments_credit_unmatched(dep_id: int, user_lookup: str = Form(...)):
        lookup = (user_lookup or "").strip()
        user = None
        if lookup.lstrip("@").isdigit():
            user = await db.get_user_by_tg(int(lookup.lstrip("@")))
        else:
            user = await db.get_user_by_username(lookup)
        if not user:
            raise HTTPException(404, "User not found")

        credited, amount = await db.resolve_unmatched_deposit(dep_id, int(user["id"]))
        if credited and amount > 0 and settings.BOT_TOKEN:
            balance = await db.get_balance(int(user["id"]))
            with i18n.use_language(user["language"]):
                msg = i18n.t(
                    "notify.balance",
                    sign="+",
                    delta=f"{amount:.2f}",
                    balance=f"{balance:.2f}",
                )
                msg += i18n.t(
                    "notify.balance_note",
                    note=escape(f"Manual credit for unmatched deposit #{dep_id}"),
                )
            tg_id = int(user["tg_id"])

            async def _notify() -> None:
                try:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        await client.post(
                            f"https://api.telegram.org/bot{settings.BOT_TOKEN}/sendMessage",
                            json={"chat_id": tg_id, "text": msg, "parse_mode": "HTML"},
                        )
                except httpx.HTTPError:
                    pass

            _spawn(_notify())
        return RedirectResponse("/payments", status_code=302)

    # ---------- Users ----------

    @app.get("/users", response_class=HTMLResponse, dependencies=[Depends(auth_redirect)])
    async def users_page(request: Request, q: str | None = None):
        rows = await db.list_users(limit=500, search=q)
        return render(
            request, "users.html",
            {
                "shop": settings.SHOP_NAME,
                "page": "users",
                "rows": rows,
                "query": (q or "").strip(),
            },
        )

    def _users_redirect(q: str = "") -> RedirectResponse:
        suffix = f"?q={quote_plus(q)}" if q else ""
        return RedirectResponse(f"/users{suffix}", status_code=302)

    async def _notify_user_access(user, key: str) -> None:
        if not settings.BOT_TOKEN:
            return
        with i18n.use_language(user["language"]):
            msg = i18n.t(key)
        tg_id = int(user["tg_id"])

        async def _notify() -> None:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    await client.post(
                        f"https://api.telegram.org/bot{settings.BOT_TOKEN}/sendMessage",
                        json={"chat_id": tg_id, "text": msg, "parse_mode": "HTML"},
                    )
            except httpx.HTTPError:
                pass

        _spawn(_notify())

    @app.post("/users/{uid}/block", dependencies=[Depends(auth_redirect)])
    async def users_block(uid: int, q: str = Form("")):
        user = await db.get_user(uid)
        if not user:
            raise HTTPException(404, "User not found")
        await db.set_user_blocked(uid, True, admin_tg_ids=list(settings.ADMIN_IDS or []))
        await _notify_user_access(user, "notify.blocked")
        return _users_redirect(q.strip())

    @app.post("/users/{uid}/unblock", dependencies=[Depends(auth_redirect)])
    async def users_unblock(uid: int, q: str = Form("")):
        user = await db.get_user(uid)
        if not user:
            raise HTTPException(404, "User not found")
        await db.set_user_blocked(uid, False, admin_tg_ids=list(settings.ADMIN_IDS or []))
        await _notify_user_access(user, "notify.unblocked")
        return _users_redirect(q.strip())

    @app.post("/users/{uid}/balance", dependencies=[Depends(auth_redirect)])
    async def users_balance(
        uid: int,
        action: str = Form(...),
        amount: float = Form(...),
        note: str = Form(""),
    ):
        user = await db.get_user(uid)
        if not user:
            raise HTTPException(404, "User not found")
        amt = float(amount)
        # `amount: float = Form(...)` happily parses "inf"/"nan", and `amt < 0`
        # is False for both — a non-finite amount would otherwise reach the
        # balance helpers and could persist an infinite balance. Reject here;
        # the db helpers also guard as the authoritative check. (review: NaN/Inf)
        if not math.isfinite(amt) or amt < 0:
            raise HTTPException(400, "Amount must be a finite, non-negative number")
        action = action.strip().lower()
        note = note.strip()
        try:
            if action == "set":
                before, after = await db.set_user_balance(uid, amt, note=note)
            elif action == "add":
                before, after = await db.adjust_user_balance(uid, amt, note=note)
            elif action == "subtract":
                before, after = await db.adjust_user_balance(uid, -amt, note=note)
            else:
                raise HTTPException(400, "Invalid action")
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

        # Report the REALISED change (after - before), so a subtraction clamped
        # at zero shows the true delta, matching the ledger row. (review M-low)
        delta = round(after - before, 2)

        if delta != 0 and settings.BOT_TOKEN:
            sign = "+" if delta > 0 else ""
            # Localize the chrome to the buyer's language (dashboard routes run
            # outside the bot's LanguageMiddleware); the admin's free-text note
            # is shown as written.
            with i18n.use_language(user["language"]):
                msg = i18n.t("notify.balance", sign=sign,
                             delta=f"{delta:.2f}", balance=f"{after:.2f}")
                if note:
                    msg += i18n.t("notify.balance_note", note=escape(note))
            tg_id = int(user["tg_id"])

            # Fire the DM as a tracked background task so the admin's redirect
            # returns immediately instead of blocking up to 10s on Telegram. (review M-low)
            async def _notify() -> None:
                try:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        await client.post(
                            f"https://api.telegram.org/bot{settings.BOT_TOKEN}/sendMessage",
                            json={"chat_id": tg_id, "text": msg, "parse_mode": "HTML"},
                        )
                except httpx.HTTPError:
                    pass

            _spawn(_notify())

        return RedirectResponse("/users", status_code=302)

    # ---------- Coupons ----------

    @app.get("/coupons", response_class=HTMLResponse, dependencies=[Depends(auth_redirect)])
    async def coupons_page(request: Request):
        rows = await db.list_coupons()
        return render(
            request, "coupons.html",
            {"shop": settings.SHOP_NAME, "page": "coupons", "rows": rows},
        )

    @app.post("/coupons/new", dependencies=[Depends(auth_redirect)])
    async def coupons_new(
        code: str = Form(...),
        kind: str = Form(...),
        value: float = Form(...),
        max_uses: int = Form(0),
    ):
        await db.create_coupon(code.strip(), kind.strip(), float(value), int(max_uses))
        return RedirectResponse("/coupons", status_code=302)

    @app.post("/coupons/{cid}/toggle", dependencies=[Depends(auth_redirect)])
    async def coupons_toggle(cid: int):
        from db import get_db
        async with get_db() as d:
            cur = await d.execute("SELECT active FROM coupons WHERE id = ?", (cid,))
            row = await cur.fetchone()
            if row:
                new = 0 if int(row["active"]) else 1
                await d.execute("UPDATE coupons SET active = ? WHERE id = ?", (new, cid))
                await d.commit()
        return RedirectResponse("/coupons", status_code=302)

    # ---------- Broadcast ----------

    async def _count_users() -> int:
        from db import get_db
        async with get_db() as d:
            cur = await d.execute("SELECT COUNT(*) AS n FROM users")
            row = await cur.fetchone()
        return int(row["n"]) if row else 0

    @app.get("/broadcast", response_class=HTMLResponse, dependencies=[Depends(auth_redirect)])
    async def broadcast_page(
        request: Request,
        queued: int | None = None,
        queued_products: int | None = None,
        error: str | None = None,
    ):
        from webapp.broadcast_tools import active_product_choices
        return render(
            request, "broadcast.html",
            {
                "shop": settings.SHOP_NAME,
                "page": "broadcast",
                "user_count": await _count_users(),
                "products": await active_product_choices(),
                "queued": queued,
                "queued_products": queued_products,
                "error": error,
            },
        )

    @app.post("/broadcast", dependencies=[Depends(auth_redirect)])
    async def broadcast_send(message: str = Form(...)):
        body = (message or "").strip()
        if not body:
            return RedirectResponse("/broadcast?error=empty", status_code=302)

        # Late import: the bot module is started by run.py in the same event
        # loop and registers its singleton there. Importing at module load
        # would couple webapp startup to bot startup order.
        from bot import get_running_bot
        b = get_running_bot()
        if b is None:
            return RedirectResponse("/broadcast?error=bot_offline", status_code=302)

        n_users = await _count_users()
        from services.broadcast import broadcast_admin_notification
        _spawn(broadcast_admin_notification(b, body))
        return RedirectResponse(f"/broadcast?queued={n_users}", status_code=302)

    @app.post("/broadcast/bulk-pricing", dependencies=[Depends(auth_redirect)])
    async def broadcast_bulk_pricing_send(request: Request):
        form = await request.form()
        product_ids: list[int] = []
        for raw in form.getlist("product_ids"):
            try:
                product_ids.append(int(str(raw)))
            except (TypeError, ValueError):
                continue
        product_ids = list(dict.fromkeys(product_ids))
        if not product_ids:
            return RedirectResponse("/broadcast?error=no_product", status_code=302)

        from bot import get_running_bot
        b = get_running_bot()
        if b is None:
            return RedirectResponse("/broadcast?error=bot_offline", status_code=302)

        from webapp.broadcast_tools import active_product_choices, broadcast_bulk_pricing
        choices = await active_product_choices()
        tiered_ids = {int(p["id"]) for p in choices if int(p["tier_count"]) > 0}
        selected = [pid for pid in product_ids if pid in tiered_ids]
        if not selected:
            return RedirectResponse("/broadcast?error=no_tiers", status_code=302)

        _spawn(broadcast_bulk_pricing(b, selected))
        n_users = await _count_users()
        return RedirectResponse(
            f"/broadcast?queued={n_users}&queued_products={len(selected)}",
            status_code=302,
        )

    @app.post("/broadcast/flash-sale", dependencies=[Depends(auth_redirect)])
    async def broadcast_flash_sale_send(
        product_id: int = Form(...),
        was_price: float = Form(...),
        now_price: float = Form(...),
        duration: str = Form("12h"),
    ):
        if was_price <= 0 or now_price <= 0 or now_price >= was_price:
            return RedirectResponse("/broadcast?error=bad_price", status_code=302)

        from bot import get_running_bot
        b = get_running_bot()
        if b is None:
            return RedirectResponse("/broadcast?error=bot_offline", status_code=302)

        product = await db.get_product(int(product_id))
        if not product or not int(product["active"]):
            return RedirectResponse("/broadcast?error=no_product", status_code=302)

        from webapp.broadcast_tools import broadcast_flash_sale
        _spawn(
            broadcast_flash_sale(
                b,
                int(product_id),
                round(float(was_price), 2),
                round(float(now_price), 2),
                (duration or "12h").strip() or "12h",
            )
        )
        n_users = await _count_users()
        return RedirectResponse(
            f"/broadcast?queued={n_users}&queued_products=1",
            status_code=302,
        )

    return app
