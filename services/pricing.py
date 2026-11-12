from __future__ import annotations

from html import escape
from typing import Iterable

import aiosqlite


def _tier_unit_price(t: aiosqlite.Row, base_price: float) -> float:
    """Resolve a tier's per-unit price in USDT.

    New tiers store `unit_price` directly. Legacy percentage tiers (created
    before the dollar switch — `unit_price IS NULL`) are converted from
    `discount_pct` so old data keeps resolving correctly."""
    keys = t.keys()
    up = t["unit_price"] if "unit_price" in keys else None
    if up is not None:
        return round(float(up), 2)
    pct = float(t["discount_pct"]) if ("discount_pct" in keys and t["discount_pct"] is not None) else 0.0
    return round(base_price * (1 - pct / 100.0), 2)


def effective_unit_price(base_price: float, quantity: int,
                         tiers: Iterable[aiosqlite.Row]) -> tuple[float, float]:
    """Return (unit_price, discount_pct_applied) for the given quantity.

    Charges the price of the band the quantity falls into — i.e. the tier with
    the HIGHEST `min_qty` that is still `<= quantity`. This is exactly the band
    shown by `format_tier_summary`, so the price charged always equals the
    price displayed to the customer, even if an admin enters non-monotonic
    tiers. Falls back to `base_price` below the first tier. The second tuple
    element is derived purely for back-compat with callers expecting the old
    2-tuple shape (they discard it)."""
    chosen_min = -1
    unit = round(float(base_price), 2)
    for t in tiers:
        mq = int(t["min_qty"])
        if quantity >= mq and mq > chosen_min:
            chosen_min = mq
            unit = _tier_unit_price(t, base_price)
    pct = round((1 - unit / base_price) * 100.0, 2) if base_price > 0 else 0.0
    return unit, pct


def format_tier_summary(base_price: float, tiers: Iterable[aiosqlite.Row],
                        unit_label: str = "code") -> str:
    """Range-style bulk-pricing display, e.g.

        • From 1-9: $1.90 / account
        • From 10+: $1.60 / account

    The first line covers the base price up to the first tier threshold; each
    tier then spans to the next tier's threshold, and the last tier is
    open-ended ("+"). Returns "" when there are no tiers, so callers can guard
    with a simple truthiness check and only render the block when set."""
    tiers = sorted(tiers, key=lambda t: int(t["min_qty"]))
    if not tiers:
        return ""
    # Escaped because this string is sent with parse_mode=HTML; unit_label is
    # admin-set but could contain '<'/'&' which would break message parsing.
    unit = escape((unit_label or "code").strip() or "code")
    rows: list[tuple[str, float]] = []

    first_min = int(tiers[0]["min_qty"])
    if first_min > 1:
        upper = first_min - 1
        rng = f"1-{upper}" if upper > 1 else "1"
        rows.append((rng, round(float(base_price), 2)))

    for i, t in enumerate(tiers):
        lo = int(t["min_qty"])
        price = _tier_unit_price(t, base_price)
        if i + 1 < len(tiers):
            hi = int(tiers[i + 1]["min_qty"]) - 1
            rng = f"{lo}-{hi}" if hi > lo else f"{lo}"
        else:
            rng = f"{lo}+"
        rows.append((rng, price))

    return "\n".join(f"• From {rng}: ${price:.2f} / {unit}" for rng, price in rows)


def tier_rows_for_display(base_price: float,
                          tiers: Iterable[aiosqlite.Row]) -> list[dict]:
    """Flatten tiers to {min_qty, unit_price} dicts for the admin dashboard
    template, which can't call the resolver helper directly."""
    return [
        {"min_qty": int(t["min_qty"]), "unit_price": _tier_unit_price(t, base_price)}
        for t in sorted(tiers, key=lambda t: int(t["min_qty"]))
    ]
