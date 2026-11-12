from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

import db
from services.order_exports import normalize_export_format
from services.reseller_api import (
    InsufficientApiBalance,
    InvalidIdempotencyKey,
    ProductUnavailable,
    ResellerApiError,
    StockUnavailable,
    authenticate_api_key,
    create_wallet_api_order,
    order_export,
    order_payload,
    product_payload,
)

bearer = HTTPBearer(auto_error=False)


class OrderCreate(BaseModel):
    product_id: int = Field(..., ge=1)
    quantity: int = Field(..., ge=1, le=1000)


async def api_auth(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
):
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Missing API key")
    row = await authenticate_api_key(credentials.credentials)
    if not row:
        raise HTTPException(status_code=401, detail="Invalid API key")
    if int(row["user_blocked"] or 0):
        raise HTTPException(status_code=403, detail="This account is blocked")
    return row


def _raise_api_error(exc: ResellerApiError) -> None:
    if isinstance(exc, InsufficientApiBalance):
        raise HTTPException(
            status_code=exc.status_code,
            detail={
                "message": str(exc),
                "balance": exc.balance,
                "payable": exc.payable,
                "short": exc.short,
            },
        )
    raise HTTPException(status_code=exc.status_code, detail=str(exc))


def create_reseller_api_app() -> FastAPI:
    app = FastAPI(
        title="Akunding Store Reseller API",
        version="1.0.0",
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
        swagger_ui_parameters={"persistAuthorization": True},
    )

    @app.get("/v1/me")
    async def me(auth=Depends(api_auth)) -> dict[str, Any]:
        user = await db.get_user(int(auth["user_id"]))
        return {
            "user_id": int(auth["user_id"]),
            "telegram_id": int(auth["user_tg_id"]),
            "username": auth["user_username"],
            "first_name": auth["user_first_name"],
            "balance": round(float(user["balance"] if user else auth["user_balance"] or 0.0), 2),
            "api_key_prefix": auth["key_prefix"],
        }

    @app.get("/v1/products")
    async def products(auth=Depends(api_auth)) -> list[dict[str, Any]]:
        rows = await db.list_products(active_only=True, limit=1000)
        stock = await db.stock_counts([int(p["id"]) for p in rows])
        return [await product_payload(p, stock=stock.get(int(p["id"]), 0)) for p in rows]

    @app.get("/v1/products/{product_id}")
    async def product(product_id: int, auth=Depends(api_auth)) -> dict[str, Any]:
        row = await db.get_product(product_id)
        if not row or not int(row["active"]):
            raise HTTPException(status_code=404, detail="Product not found")
        return await product_payload(row)

    @app.post("/v1/orders")
    async def create_order(
        body: OrderCreate,
        x_idempotency_key: Annotated[str | None, Header(alias="X-Idempotency-Key")] = None,
        auth=Depends(api_auth),
    ) -> dict[str, Any]:
        try:
            payload, idempotent = await create_wallet_api_order(
                api_key_id=int(auth["id"]),
                user_id=int(auth["user_id"]),
                product_id=body.product_id,
                quantity=body.quantity,
                idempotency_key=x_idempotency_key or "",
            )
        except ResellerApiError as exc:
            _raise_api_error(exc)
        payload["idempotent"] = idempotent
        return payload

    @app.get("/v1/orders")
    async def orders(limit: int = 50, auth=Depends(api_auth)) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 50), 100))
        rows = await db.recent_orders(int(auth["user_id"]), limit=limit)
        return [
            await order_payload(int(row["id"]), int(auth["user_id"]), include_contents=False)
            for row in rows
        ]

    @app.get("/v1/orders/{order_id}")
    async def order(order_id: int, auth=Depends(api_auth)) -> dict[str, Any]:
        try:
            return await order_payload(order_id, int(auth["user_id"]), include_contents=True)
        except ProductUnavailable as exc:
            _raise_api_error(exc)

    @app.get("/v1/orders/{order_id}/export")
    async def export_order(
        order_id: int,
        format: str = "txt",
        auth=Depends(api_auth),
    ) -> Response:
        if normalize_export_format(format) is None:
            raise HTTPException(status_code=400, detail="format must be txt or xlsx")
        try:
            export = await order_export(order_id, int(auth["user_id"]), format)
        except (ProductUnavailable, StockUnavailable) as exc:
            _raise_api_error(exc)
        return Response(
            content=export.data,
            media_type=export.mime_type,
            headers={"Content-Disposition": f'attachment; filename="{export.filename}"'},
        )

    return app
