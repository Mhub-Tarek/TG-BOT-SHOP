#!/usr/bin/env python3
"""Set products.brand from product name keywords (one-time / re-run safe)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import db  # noqa: E402
from keyboards.brands import guess_brand_from_name  # noqa: E402


async def main() -> int:
    await db.init_db()
    products = await db.list_products(active_only=False)
    updated = 0
    for p in products:
        brand = guess_brand_from_name(p["name"])
        current = str(p["brand"] if "brand" in p.keys() else "").strip()
        if brand and brand != current:
            await db.update_product(p["id"], brand=brand)
            print(f"  #{p['id']} {p['name']!r} → {brand}")
            updated += 1
        elif brand:
            print(f"  #{p['id']} {p['name']!r} already {brand}")
    print(f"\n✓ Updated {updated} product(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
