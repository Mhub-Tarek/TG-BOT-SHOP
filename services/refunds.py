from __future__ import annotations

import math
import time
from typing import Any

import db


SECONDS_PER_DAY = 86400


def _refund_row(row, *, now: int, fallback_window_days: int | None = None) -> dict[str, Any] | None:
    warranty_days = int(row["warranty_days"] or 0)
    if warranty_days <= 0 and fallback_window_days is not None:
        warranty_days = int(fallback_window_days)
    if warranty_days <= 0:
        return None
    delivered_at = int(row["delivered_at"])
    elapsed_seconds = max(0, int(now) - delivered_at)
    age_days = math.floor(elapsed_seconds / SECONDS_PER_DAY)
    remaining_days = max(0, warranty_days - age_days)
    payable = round(float(row["payable"] or 0), 2)
    estimated = round(payable * remaining_days / warranty_days, 2) if warranty_days > 0 else 0.0
    return {
        "order_id": int(row["id"]),
        "product_id": int(row["product_id"]),
        "product_name": row["product_name"],
        "product_emoji": row["product_emoji"] or "",
        "warranty_days": warranty_days,
        "quantity": int(row["quantity"]),
        "unit_price": round(float(row["unit_price"] or 0), 2),
        "subtotal": round(float(row["subtotal"] or 0), 2),
        "discount": round(float(row["discount"] or 0), 2),
        "payable": payable,
        "delivered_at": delivered_at,
        "age_days": age_days,
        "remaining_days": remaining_days,
        "estimated_refund": estimated,
    }


def _group_by_product(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[int, dict[str, Any]] = {}
    for order in orders:
        product_id = int(order["product_id"])
        item = grouped.setdefault(
            product_id,
            {
                "product_id": product_id,
                "product_name": order["product_name"],
                "product_emoji": order["product_emoji"],
                "order_ids": [],
                "orders": [],
                "order_count": 0,
                "quantity": 0,
                "payable": 0.0,
                "estimated_refund": 0.0,
                "min_remaining_days": order["remaining_days"],
                "max_age_days": order["age_days"],
                "warranty_days": order["warranty_days"],
            },
        )
        item["order_ids"].append(order["order_id"])
        item["orders"].append(order)
        item["order_count"] += 1
        item["quantity"] += order["quantity"]
        item["payable"] = round(item["payable"] + order["payable"], 2)
        item["estimated_refund"] = round(item["estimated_refund"] + order["estimated_refund"], 2)
        item["min_remaining_days"] = min(item["min_remaining_days"], order["remaining_days"])
        item["max_age_days"] = max(item["max_age_days"], order["age_days"])
        item["warranty_days"] = max(item["warranty_days"], order["warranty_days"])
    return sorted(
        grouped.values(),
        key=lambda p: (p["estimated_refund"], p["payable"]),
        reverse=True,
    )


async def _refund_lookup_for_user(
    user,
    *,
    lookup_value: str,
    now: int | None = None,
    window_days: int | None = None,
) -> dict[str, Any]:
    """Return refund estimates for a resolved user row.

    This is read-only by design: it never creates refund rows, credits wallet
    balance, or exposes stock/account contents.
    """
    now = int(time.time() if now is None else now)
    tg_id = int(user["tg_id"]) if user else None
    rows = await db.delivered_orders_for_tg(tg_id) if tg_id is not None else []
    calculated = [
        row for row in (
            _refund_row(r, now=now, fallback_window_days=window_days)
            for r in rows
        )
        if row is not None
    ]
    eligible = [r for r in calculated if r["remaining_days"] > 0 and r["estimated_refund"] > 0]
    expired = [r for r in calculated if r not in eligible]
    return {
        "found_user": user is not None,
        "lookup_value": lookup_value,
        "telegram_id": tg_id,
        "username": (user["username"] if user else None),
        "window_days": None if window_days is None else int(window_days),
        "eligible_orders": eligible,
        "eligible_products": _group_by_product(eligible),
        "expired_orders": expired,
        "expired_products": _group_by_product(expired),
    }


async def refund_lookup_for_tg(
    tg_id: int,
    *,
    now: int | None = None,
    window_days: int | None = None,
) -> dict[str, Any]:
    user = await db.get_user_by_tg(int(tg_id))
    return await _refund_lookup_for_user(
        user,
        lookup_value=str(int(tg_id)),
        now=now,
        window_days=window_days,
    )


async def refund_lookup_for_identifier(
    identifier: str,
    *,
    now: int | None = None,
    window_days: int | None = None,
) -> dict[str, Any]:
    lookup_value = (identifier or "").strip()
    if lookup_value.lstrip("@").isdigit():
        return await refund_lookup_for_tg(
            int(lookup_value.lstrip("@")),
            now=now,
            window_days=window_days,
        )
    user = await db.get_user_by_username(lookup_value)
    return await _refund_lookup_for_user(
        user,
        lookup_value=lookup_value,
        now=now,
        window_days=window_days,
    )
