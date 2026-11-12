"""One-off: list inactive products + their order/stock counts so the
owner can decide which to hard-purge. Read-only."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aiosqlite

DB = Path(__file__).resolve().parent.parent / "shop.db"


async def main() -> None:
    async with aiosqlite.connect(DB) as d:
        d.row_factory = aiosqlite.Row
        cur = await d.execute(
            "SELECT p.id, p.name, p.active, "
            "(SELECT COUNT(*) FROM orders WHERE product_id = p.id) AS orders, "
            "(SELECT COUNT(*) FROM stock_items WHERE product_id = p.id AND sold = 0) AS stock_left "
            "FROM products p ORDER BY p.active DESC, p.id"
        )
        rows = await cur.fetchall()
    print(f"{'id':>4}  {'active':<7}  {'orders':>6}  {'stock':>5}  name")
    print("-" * 60)
    for r in rows:
        flag = "yes" if r["active"] else "NO"
        print(f"{r['id']:>4}  {flag:<7}  {r['orders']:>6}  {r['stock_left']:>5}  {r['name']}")


if __name__ == "__main__":
    asyncio.run(main())
