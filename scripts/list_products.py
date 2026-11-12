#!/usr/bin/env python3
import sqlite3
import sys
from pathlib import Path

db = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent.parent / "shop.db"
c = sqlite3.connect(db)
c.row_factory = sqlite3.Row
for r in c.execute(
    "SELECT id, name, emoji, base_price, warranty_days FROM products "
    "WHERE active=1 ORDER BY base_price"
):
    print(f"{r['id']}|{r['emoji'] or '•'}|{r['name']}|{r['base_price']}|{r['warranty_days']}")
