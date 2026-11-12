from __future__ import annotations

import asyncio
import hashlib
import secrets
from dataclasses import dataclass
from typing import Any

import db
from services.delivery import OutOfStock, pay_from_wallet
from services.order_exports import build_order_export
from services.pricing import effective_unit_price

API_KEY_PREFIX = "ak_"
API_KEY_VISIBLE_PREFIX_LEN = 14
IDEMPOTENCY_KEY_MAX_LEN = 120

_order_locks: dict[tuple[int, str], asyncio.Lock] = {}


class ResellerApiError(Exception):
    status_code = 400


class ProductUnavailable(ResellerApiError):
    status_code = 404


class StockUnavailable(ResellerApiError):
    status_code = 409


class InsufficientApiBalance(ResellerApiError):
    status_code = 402

    def __init__(self, *, balance: float, payable: float):
        super().__init__("Insufficient wallet balance")
        self.balance = round(balance, 2)
        self.payable = round(payable, 2)
        self.short = round(max(0.0, payable - balance), 2)


class InvalidIdempotencyKey(ResellerApiError):
    status_code = 400


@dataclass(frozen=True)
class ApiKeyMaterial:
    key: str
    prefix: str
    row: Any


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def mask_api_key(api_key: str) -> str:
    value = (api_key or "").strip()
    if len(value) <= 10:
        return "***"
    return f"{value[:6]}...{value[-4:]}"


def _new_api_key() -> str:
    return API_KEY_PREFIX + secrets.token_urlsafe(32)


def _prefix(api_key: str) -> str:
    return api_key[:API_KEY_VISIBLE_PREFIX_LEN]


async def ensure_api_key_for_user(user_id: int) -> ApiKeyMaterial | None:
    row = await db.get_active_api_key_for_user(user_id)
    if row:
        return None
    return await regenerate_api_key_for_user(user_id)


async def regenerate_api_key_for_user(user_id: int) -> ApiKeyMaterial:
    api_key = _new_api_key()
    row = await db.replace_active_api_key(
        user_id,
        key_hash=hash_api_key(api_key),
        key_prefix=_prefix(api_key),
    )
    return ApiKeyMaterial(key=api_key, prefix=_prefix(api_key), row=row)


async def authenticate_api_key(raw_api_key: str | None):
    token = (raw_api_key or "").strip()
    if not token:
        return None
    row = await db.get_active_api_key_by_hash(hash_api_key(token))
    if row:
        await db.touch_api_key(int(row["id"]))
    return row


def normalize_idempotency_key(value: str | None) -> str:
    key = (value or "").strip()
    if not key or len(key) > IDEMPOTENCY_KEY_MAX_LEN:
        raise InvalidIdempotencyKey("X-Idempotency-Key is required")
    return key


async def create_wallet_api_order(
    *,
    api_key_id: int,
    user_id: int,
    product_id: int,
    quantity: int,
    idempotency_key: str,
) -> tuple[dict[str, Any], bool]:
    idem = normalize_idempotency_key(idempotency_key)
    lock_key = (api_key_id, idem)
    lock = _order_locks.setdefault(lock_key, asyncio.Lock())
    async with lock:
        existing = await db.api_order_by_idempotency(api_key_id, idem)
        if existing:
            payload = await order_payload(
                int(existing["order_id"]),
                user_id,
                include_contents=True,
            )
            payload["charged"] = round(float(payload["payable"]), 2)
            return payload, True

        if not isinstance(quantity, int) or quantity < 1 or quantity > 1000:
            raise InvalidIdempotencyKey("quantity must be between 1 and 1000")

        product = await db.get_product(product_id)
        if not product or not int(product["active"]):
            raise ProductUnavailable("Product not found")
        stock = await db.stock_count(product_id)
        if stock < quantity:
            raise StockUnavailable(f"Only {stock} item(s) available")

        tiers = await db.get_pricing_tiers(product_id)
        unit_price, _discount_pct = effective_unit_price(
            float(product["base_price"]),
            quantity,
            tiers,
        )
        subtotal = round(unit_price * quantity, 2)
        order_id = await db.create_order(
            user_id=user_id,
            product_id=product_id,
            quantity=quantity,
            unit_price=unit_price,
            subtotal=subtotal,
            coupon_code=None,
            discount=0.0,
            payable=subtotal,
            status="draft",
            referral_discount=0.0,
        )

        balance = await db.get_balance(user_id)
        if balance < subtotal:
            await db.update_order(order_id, status="cancelled")
            raise InsufficientApiBalance(balance=balance, payable=subtotal)

        try:
            contents, charged = await pay_from_wallet(user_id, order_id, unlimited=False)
        except OutOfStock as exc:
            raise StockUnavailable("Stock ran out before delivery") from exc
        except ValueError as exc:
            balance = await db.get_balance(user_id)
            raise InsufficientApiBalance(balance=balance, payable=subtotal) from exc

        await db.record_api_order(
            api_key_id=api_key_id,
            user_id=user_id,
            order_id=order_id,
            idempotency_key=idem,
        )
        payload = await order_payload(order_id, user_id, include_contents=False)
        payload["charged"] = round(float(charged), 2)
        payload["items"] = contents
        return payload, False


async def product_payload(product, *, stock: int | None = None) -> dict[str, Any]:
    product_id = int(product["id"])
    if stock is None:
        stock = await db.stock_count(product_id)
    tiers = await db.get_pricing_tiers(product_id)
    return {
        "id": product_id,
        "name": str(product["name"]),
        "description": str(product["description"] or ""),
        "features": str(product["features"] or ""),
        "unit_label": str(product["unit_label"] or "code"),
        "base_price": round(float(product["base_price"]), 2),
        "stock": int(stock),
        "bulk_tiers": [
            {
                "min_qty": int(t["min_qty"]),
                "unit_price": round(
                    float(t["unit_price"])
                    if t["unit_price"] is not None
                    else float(product["base_price"]) * (1 - float(t["discount_pct"] or 0.0) / 100.0),
                    2,
                ),
            }
            for t in tiers
        ],
    }


async def order_payload(
    order_id: int,
    user_id: int,
    *,
    include_contents: bool = False,
) -> dict[str, Any]:
    order = await db.get_order(order_id)
    if not order or int(order["user_id"]) != int(user_id):
        raise ProductUnavailable("Order not found")
    product = await db.get_product(int(order["product_id"]))
    if not product:
        raise ProductUnavailable("Product not found")
    payload = {
        "id": int(order["id"]),
        "status": str(order["status"]),
        "product": {
            "id": int(product["id"]),
            "name": str(product["name"]),
            "unit_label": str(product["unit_label"] or "code"),
        },
        "quantity": int(order["quantity"]),
        "unit_price": round(float(order["unit_price"]), 2),
        "subtotal": round(float(order["subtotal"]), 2),
        "discount": round(float(order["discount"] or 0.0), 2),
        "payable": round(float(order["payable"]), 2),
        "payment_method": order["payment_method"],
        "created_at": int(order["created_at"]),
        "delivered_at": int(order["delivered_at"]) if order["delivered_at"] else None,
    }
    if include_contents:
        delivered = await db.delivered_contents_by_orders([order_id])
        payload["items"] = delivered.get(order_id, [])
    return payload


async def order_export(order_id: int, user_id: int, fmt: str):
    order = await db.get_order(order_id)
    if not order or int(order["user_id"]) != int(user_id):
        raise ProductUnavailable("Order not found")
    if str(order["status"]) != "delivered":
        raise StockUnavailable("Order is not delivered yet")
    product = await db.get_product(int(order["product_id"]))
    delivered = await db.delivered_contents_by_orders([order_id])
    return build_order_export(
        order=order,
        product=product,
        contents=delivered.get(order_id, []),
        fmt=fmt,
    )
