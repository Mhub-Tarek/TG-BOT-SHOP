#!/usr/bin/env python3
"""Reconcile dashboard profit vs revenue (read-only). Run on VPS:
  cd /opt/telegram-shop && sudo -u shopbot .venv/bin/python scripts/profit_audit.py
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import db  # noqa: E402
from config import get_settings  # noqa: E402


def _print_breakdown(label: str, b: dict) -> None:
    print(f"\n=== {label} ({b['period']}) ===")
    print(f"  A. Delivered revenue (period):     ${b['period_revenue']:.2f}")
    print(f"  B. Profit-eligible revenue:        ${b['eligible_revenue']:.2f}")
    print(f"  C. COGS:                           ${b['cogs']:.2f}")
    print(f"  D. Raw profit (B − C):             ${b['raw_profit']:.2f}")
    print(f"  E. Dashboard profit (net):         ${b['raw_profit']:.2f}")
    if b["order_losses"] > 0:
        print(f"  H. Losses on underwater orders:    ${b['order_losses']:.2f}")
    print(f"  F. Excluded revenue (A − B):       ${b['excluded_revenue']:.2f}")
    print(f"      Excluded orders:              {b['excluded_order_count']}")
    print(f"      Eligible orders:              {b['eligible_order_count']}")
    if b["hidden_loss"] > 0:
        print(f"  G. Loss hidden by clamp:           ${b['hidden_loss']:.2f}")


async def _backfill_delivered_at() -> int:
    async with db.get_db() as conn:
        cur = await conn.execute(
            "UPDATE orders SET delivered_at = created_at "
            "WHERE status = 'delivered' AND (delivered_at IS NULL OR delivered_at = 0)"
        )
        await conn.commit()
        return cur.rowcount


async def main() -> int:
    parser = argparse.ArgumentParser(description="Profit vs revenue reconciliation")
    parser.add_argument(
        "--repair-delivered-at",
        action="store_true",
        help="Backfill delivered_at from created_at where missing (delivered orders)",
    )
    args = parser.parse_args()

    settings = get_settings()
    admin_ids = list(settings.ADMIN_IDS or [])

    await db.init_db()

    if args.repair_delivered_at:
        n = await _backfill_delivered_at()
        print(f"Repaired delivered_at on {n} order(s).")

    async with db.get_db() as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) FROM orders WHERE status = 'delivered' "
            "AND (delivered_at IS NULL OR delivered_at = 0)"
        )
        null_delivered = int((await cur.fetchone())[0] or 0)
    if null_delivered:
        print(
            f"\nNote: {null_delivered} delivered order(s) still lack delivered_at "
            f"(excluded from period profit). Run with --repair-delivered-at to fix."
        )

    for period in ("day", "month"):
        b = await db.profit_breakdown_for_period(period, exclude_admin_ids=admin_ids or None)
        legacy = await db.profit_for_period(period, exclude_admin_ids=admin_ids or None)
        assert legacy == b["raw_profit"]
        title = "Today (Casa)" if period == "day" else "This month (Casa)"
        _print_breakdown(title, b)

    print("\n=== Recent delivered orders today (up to 10) ===")
    orders = await db.profit_orders_audit("day", exclude_admin_ids=admin_ids or None, limit=10)
    if not orders:
        print("  (none)")
    for o in orders:
        flag = "" if o["reason"] == "included" else f"  [{o['reason']}]"
        print(
            f"  #{o['order_id']}  payable=${o['payable']:.2f}  "
            f"cogs=${o['cogs']:.2f}  margin=${o['margin']:.2f}{flag}"
        )

    print(
        "\nTip: Dashboard 'All-time revenue' is lifetime delivered sales, not 'Profit today'."
    )
    print("     Compare 'Revenue today' with line A and profit with line E on the dashboard.")
    print("     Profit updates live on the dashboard; it drops when loss-making orders deliver.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
