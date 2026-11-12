#!/usr/bin/env python3
"""Find confirmed payments where Binance tx predates pending payment creation."""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Output goes to the shared log — mask txids/payer names by default. (review M11)
FULL = "--full" in sys.argv[1:]


def _mask(value: object, keep: int = 4) -> str:
    s = str(value or "")
    if FULL or len(s) <= keep * 2:
        return s
    return f"{s[:keep]}…{s[-keep:]}"


DB = Path(__file__).resolve().parent.parent / "shop.db"
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
rows = conn.execute(
    """
    SELECT p.*, u.username, u.tg_id, u.created_at AS user_created
    FROM payments p
    LEFT JOIN users u ON u.id = p.user_id
    WHERE p.status = 'confirmed'
    ORDER BY p.id
    """
).fetchall()

def fmt(ts: int | float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

suspicious = []
print("=== CONFIRMED PAYMENTS AUDIT ===\n")
for r in rows:
    raw = r["raw"]
    payer = tx_time = None
    old_tx = False
    if raw:
        try:
            j = json.loads(raw)
            tx_ms = int(j.get("transactionTime") or 0)
            if tx_ms:
                tx_time = tx_ms / 1000
                old_tx = tx_time < int(r["created_at"])
            payer = (j.get("payerInfo") or {}).get("name")
        except json.JSONDecodeError:
            pass

    line = (
        f"#{r['id']} order={r['order_id']} @{r['username']} "
        f"${r['unique_amount']} {r['method']} txid={_mask(r['txid'])}"
    )
    if old_tx:
        line += " *** OLD TX ***"
        suspicious.append(r)
    print(line)
    print(f"   user joined: {fmt(r['user_created'])}")
    print(f"   payment created: {fmt(r['created_at'])}")
    if tx_time:
        print(f"   binance tx time: {fmt(tx_time)} payer={_mask(payer)!r}")
        if old_tx:
            hours = (int(r["created_at"]) - tx_time) / 3600
            print(f"   gap: {hours:.1f}h BEFORE order payment was created")
    print()

print(f"=== SUMMARY: {len(suspicious)} suspicious old-tx match(es) out of {len(rows)} confirmed ===")
