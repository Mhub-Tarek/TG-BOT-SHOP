#!/usr/bin/env python3
"""Investigate a specific order + Binance Pay tx. Run on VPS."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
from config import get_settings
from services.binance import BinanceClient

# Redact financial PII (txids, raw payloads, payer detail) by default — this
# output goes to the shared app log. Pass --full for an unmasked dump. (review M11)
FULL = "--full" in sys.argv[1:]
_args = [a for a in sys.argv[1:] if not a.startswith("--")]
ORDER_ID = int(_args[0]) if _args else 16


def _mask(value: object, keep: int = 4) -> str:
    s = str(value or "")
    if FULL or len(s) <= keep * 2:
        return s
    return f"{s[:keep]}…{s[-keep:]}"


def main_db() -> None:
    conn = sqlite3.connect(Path(__file__).resolve().parent.parent / "shop.db")
    conn.row_factory = sqlite3.Row
    print(f"=== ORDER #{ORDER_ID} ===")
    row = conn.execute("SELECT * FROM orders WHERE id=?", (ORDER_ID,)).fetchone()
    if not row:
        print("Order not found")
        return
    print(dict(row))

    print("\n=== PAYMENTS for this order ===")
    pays = conn.execute(
        "SELECT id,user_id,order_id,method,network,base_amount,unique_amount,"
        "note,txid,status,created_at,confirmed_at,binance_uid,address,raw "
        "FROM payments WHERE order_id=? ORDER BY id",
        (ORDER_ID,),
    ).fetchall()
    if not pays:
        print("(no payment rows — may have been wallet checkout)")
    for p in pays:
        d = dict(p)
        raw = d.pop("raw", None)
        if d.get("txid"):
            d["txid"] = _mask(d["txid"])
        print(json.dumps({k: d[k] for k in d if k != "raw"}, indent=2, default=str))
        if raw and FULL:
            try:
                parsed = json.loads(raw)
                print("RAW (parsed):", json.dumps(parsed, indent=2)[:3000])
            except Exception:
                print("RAW (text):", raw[:2000])
        elif raw:
            print("RAW: (masked; pass --full to show payer/payload detail)")

    user_id = row["user_id"]
    u = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    print("\n=== BUYER ===")
    print(dict(u) if u else "missing")


async def main_binance(txid: str | None, amount: float | None) -> None:
    settings = get_settings()
    if not settings.BINANCE_API_KEY:
        print("\n(no Binance API key)")
        return
    client = BinanceClient(settings.BINANCE_API_KEY, settings.BINANCE_API_SECRET)
    try:
        print("\n=== BINANCE PAY UID (shop receives here) ===")
        print(settings.BINANCE_PAY_UID or "(not set)")

        print("\n=== Recent Binance Pay C2C incoming ===")
        pay = await client.pay_transactions(limit=50)
        incoming = [
            t for t in pay
            if float(t.get("amount", 0)) > 0
            and t.get("orderType") in ("C2C", "C2C_HOLDING")
        ]
        for t in incoming[:20]:
            tid = str(t.get("transactionId", ""))
            marker = " <-- MATCH" if txid and tid == txid else ""
            print(json.dumps({
                "transactionId": _mask(tid),
                "amount": t.get("amount"),
                "currency": t.get("currency"),
                "orderType": t.get("orderType"),
                "transactionTime": t.get("transactionTime"),
                "fundsDetail": t.get("fundsDetail") if FULL else "(masked; --full to show)",
            }) + marker)

        if txid:
            found = any(str(t.get("transactionId")) == txid for t in pay)
            print(f"\nStored txid {_mask(txid)} in recent pay history: {found}")

        if amount is not None:
            matches = [
                t for t in incoming
                if abs(float(t.get("amount", 0)) - amount) < 0.01
                or any(
                    abs(float(f.get("amount", 0)) - amount) < 0.01
                    for f in (t.get("fundsDetail") or [])
                    if str(f.get("currency", "")).upper() == "USDT"
                )
            ]
            print(f"\nIncoming Pay txs near ${amount}: {len(matches)}")

    finally:
        await client.close()


async def main() -> None:
    await db.init_db()
    main_db()

    conn = sqlite3.connect(Path(__file__).resolve().parent.parent / "shop.db")
    conn.row_factory = sqlite3.Row
    order = conn.execute("SELECT * FROM orders WHERE id=?", (ORDER_ID,)).fetchone()
    if not order:
        return

    pay = conn.execute(
        "SELECT txid, unique_amount, method FROM payments WHERE order_id=? AND status='confirmed' ORDER BY id DESC LIMIT 1",
        (ORDER_ID,),
    ).fetchone()

    txid = pay["txid"] if pay else None
    amount = float(pay["unique_amount"]) if pay else float(order["payable"])
    method = pay["method"] if pay else order["payment_method"]

    print(f"\n=== SUMMARY ===")
    print(f"payment_method on order: {order['payment_method']}")
    print(f"confirmed payment method: {method}")
    print(f"expected amount: {amount}")
    print(f"stored txid: {_mask(txid)}")

    if method == "binance_pay" or order["payment_method"] == "binance_pay":
        await main_binance(txid, amount)
    elif method == "wallet":
        print("\nThis order was paid from in-bot WALLET balance, not Binance directly.")
        print("No Binance deposit expected for this order — buyer used prepaid wallet credit.")


if __name__ == "__main__":
    asyncio.run(main())
