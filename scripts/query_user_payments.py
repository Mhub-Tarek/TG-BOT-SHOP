#!/usr/bin/env python3
"""Quick lookup: user by username + recent payments. Usage: python query_user_payments.py Bestrong093"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "shop.db"
if len(sys.argv) > 2 and sys.argv[1] == "--db":
    DB = Path(sys.argv[2])
    name = sys.argv[3]
else:
    name = sys.argv[1] if len(sys.argv) > 1 else "Bestrong093"

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
users = conn.execute(
    "SELECT id, tg_id, username, balance FROM users WHERE username LIKE ?",
    (f"%{name}%",),
).fetchall()
if not users:
    print("No user found for", name)
    sys.exit(1)
for u in users:
    print("USER", dict(u))
    uid = u["id"]
    pays = conn.execute(
        "SELECT id, order_id, method, base_amount, unique_amount, status, "
        "created_at, confirmed_at, txid FROM payments WHERE user_id=? "
        "ORDER BY id DESC LIMIT 15",
        (uid,),
    ).fetchall()
    print("PAYMENTS:")
    for p in pays:
        print(dict(p))
    orders = conn.execute(
        "SELECT id, product_id, payable, status, payment_method, created_at "
        "FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10",
        (uid,),
    ).fetchall()
    print("ORDERS:")
    for o in orders:
        print(dict(o))
    # Other pending at same time near 3.60?
    if pays:
        for p in pays:
            if abs(float(p["base_amount"]) - 3.6) < 0.02 or abs(float(p["unique_amount"]) - 3.61) < 0.02:
                ts = p["created_at"]
                others = conn.execute(
                    "SELECT id, user_id, order_id, base_amount, unique_amount, status, created_at "
                    "FROM payments WHERE status='pending' AND coin='USDT' "
                    "AND created_at BETWEEN ? AND ? "
                    "AND ABS(base_amount - 3.6) < 0.01 ORDER BY id",
                    (ts - 3600, ts + 3600),
                ).fetchall()
                print("PENDING_NEAR_3.60_WINDOW:")
                for o in others:
                    print(dict(o))

if __name__ == "__main__" and "--amount-window" in sys.argv:
    conn2 = sqlite3.connect(DB)
    conn2.row_factory = sqlite3.Row
    print("=== payments 3.59-3.62 (recent) ===")
    for r in conn2.execute(
        "SELECT p.id, p.user_id, u.username, p.order_id, p.base_amount, p.unique_amount, "
        "p.status, p.method, p.created_at, p.txid FROM payments p "
        "LEFT JOIN users u ON u.id = p.user_id "
        "WHERE p.unique_amount BETWEEN 3.59 AND 3.62 OR p.base_amount BETWEEN 3.59 AND 3.62 "
        "ORDER BY p.id DESC LIMIT 25"
    ):
        print(dict(r))
    print("=== unmatched 3.59-3.62 ===")
    for r in conn2.execute(
        "SELECT * FROM unmatched_deposits WHERE amount BETWEEN 3.59 AND 3.62 "
        "ORDER BY id DESC LIMIT 10"
    ):
        print(dict(r))
