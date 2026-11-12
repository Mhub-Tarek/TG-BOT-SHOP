#!/usr/bin/env python3
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
cid = json.loads((ROOT / "keyboards" / "brand_logo_ids.json").read_text())["antigravity"]
con = sqlite3.connect(ROOT / "shop.db")
con.execute(
    "UPDATE products SET icon_custom_emoji_id = ? "
    "WHERE lower(name) LIKE '%antigravity%' OR brand = 'antigravity'",
    (cid,),
)
con.commit()
print("pinned", cid)
