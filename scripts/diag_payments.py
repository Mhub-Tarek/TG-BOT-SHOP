#!/usr/bin/env python3
"""One-off diagnostic: pending payments + recent Binance deposits."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
from config import get_settings
from services.binance import BinanceClient

# Diagnostic output lands in the shared app log, so redact financial PII (txids,
# payer details) by default. Pass --full for an unmasked dump when debugging. (review M11)
FULL = "--full" in sys.argv[1:]


def _mask(value: object, keep: int = 4) -> str:
    s = str(value or "")
    if FULL or len(s) <= keep * 2:
        return s
    return f"{s[:keep]}…{s[-keep:]}"


async def main() -> None:
    settings = get_settings()
    conn = sqlite3.connect(Path(__file__).resolve().parent.parent / "shop.db")
    conn.row_factory = sqlite3.Row
    print("=== PAYMENTS (last 10) ===")
    for row in conn.execute(
        "SELECT id, user_id, order_id, method, network, base_amount, unique_amount, "
        "note, status, created_at, expires_at FROM payments ORDER BY id DESC LIMIT 10"
    ):
        print(dict(row))
    print("\n=== USERS ===")
    for row in conn.execute("SELECT id, tg_id, username, balance FROM users"):
        print(dict(row))
    print("\n=== PENDING ===")
    pending = await db.pending_payments()
    for p in pending:
        print(dict(p))

    if not settings.BINANCE_API_KEY:
        print("\n(no Binance API key)")
        return

    client = BinanceClient(settings.BINANCE_API_KEY, settings.BINANCE_API_SECRET)
    try:
        print("\n=== BINANCE USDT deposits (no network filter, status=1) ===")
        hist = await client.deposit_history(coin="USDT", status=1, limit=20)
        print("\n=== BINANCE Pay transactions (recent) ===")
        pay = await client.pay_transactions(limit=20)
        for t in pay[:15]:
            print(json.dumps({
                "orderType": t.get("orderType"),
                "transactionId": _mask(t.get("transactionId")),
                "amount": t.get("amount"),
                "currency": t.get("currency"),
                "transactionTime": t.get("transactionTime"),
                # fundsDetail can carry payer identity — only with --full.
                "fundsDetail": t.get("fundsDetail") if FULL else "(masked; --full to show)",
            }))
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
