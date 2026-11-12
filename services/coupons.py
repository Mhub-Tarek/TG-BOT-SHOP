from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import db


@dataclass
class CouponResult:
    code: str
    kind: str       # percent / fixed
    value: float
    discount: float
    new_payable: float


async def validate_and_compute(code: str, subtotal: float) -> tuple[Optional[CouponResult], str]:
    """
    Returns (CouponResult, error_message). On success, error_message is "".
    """
    if not code or not code.strip():
        return None, "No coupon code given."
    row = await db.get_coupon(code.strip())
    if not row:
        return None, "Coupon not found."
    if not row["active"]:
        return None, "Coupon is disabled."
    if row["expires_at"] and int(row["expires_at"]) < int(time.time()):
        return None, "Coupon has expired."
    if row["max_uses"] and int(row["used_count"]) >= int(row["max_uses"]):
        return None, "Coupon usage limit reached."

    kind = row["kind"]
    value = float(row["value"])
    if kind == "percent":
        # Clamp the percentage to [0, 100] so a misconfigured value (negative,
        # or >100) can't produce a negative discount that *inflates* payable or
        # a >100% discount. (review M-low)
        pct = min(max(value, 0.0), 100.0)
        discount = round(subtotal * pct / 100.0, 2)
    elif kind == "fixed":
        discount = round(min(max(value, 0.0), subtotal), 2)
    else:
        return None, "Unknown coupon kind."

    # Final guard: discount is always within [0, subtotal], so new_payable can
    # never exceed subtotal (overcharge) or go below zero.
    discount = max(0.0, min(discount, subtotal))
    new_payable = round(subtotal - discount, 2)
    return CouponResult(code=row["code"], kind=kind, value=value,
                        discount=discount, new_payable=new_payable), ""
