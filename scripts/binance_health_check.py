#!/usr/bin/env python3
"""
Live Binance + payment pipeline health check.

Run on the VPS (uses production .env + shop.db):

    cd /opt/telegram-shop && .venv/bin/python scripts/binance_health_check.py

Exit code 0 = all checks passed.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
from config import get_settings
from services.binance import BinanceClient, BinanceError
from services.payments import (
    _poll_binance_pay,
    _poll_onchain_deposits,
    _usdt_amount_from_pay_tx,
    get_binance,
    poll_payments,
)


def ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def fail(msg: str) -> None:
    print(f"  ✗ {msg}")


async def main() -> int:
    settings = get_settings()
    errors: list[str] = []

    print("=== Binance API health check ===\n")

    # --- Config ---
    print("Config")
    if settings.BINANCE_API_KEY and settings.BINANCE_API_SECRET:
        ok(f"API key configured ({settings.BINANCE_API_KEY[:8]}…)")
    else:
        fail("BINANCE_API_KEY / BINANCE_API_SECRET missing")
        errors.append("missing api keys")

    if settings.BINANCE_PAY_UID:
        ok(f"Binance Pay UID: {settings.BINANCE_PAY_UID}")
    else:
        fail("BINANCE_PAY_UID not set — Binance Pay disabled")
        errors.append("missing binance pay uid")

    for label, addr in (("BEP20", settings.USDT_BEP20_ADDRESS), ("TRC20", settings.USDT_TRC20_ADDRESS)):
        if addr:
            ok(f"{label} address set ({addr[:10]}…)")
        else:
            print(f"  · {label} address not set (optional)")

    print(f"  · Poll interval: {settings.POLL_INTERVAL_SECONDS}s")
    print(f"  · Payment TTL: {settings.PAYMENT_TTL_MINUTES} min")

    if errors:
        print("\nCannot continue without API keys.")
        return 1

    client = BinanceClient(settings.BINANCE_API_KEY, settings.BINANCE_API_SECRET)
    try:
        # --- Clock skew ---
        print("\nClock sync")
        server_ms = await client.server_time_ms()
        drift_ms = abs(server_ms - int(time.time() * 1000))
        if drift_ms <= 5000:
            ok(f"Clock skew vs Binance: {drift_ms} ms")
        else:
            fail(f"Clock skew too high: {drift_ms} ms — signed calls may fail")
            errors.append("clock skew")

        # --- Deposit history (on-chain) ---
        print("\nOn-chain deposit API (TRC20 / BEP20 matching)")
        for network in ("TRX", "BSC"):
            try:
                hist = await client.deposit_history(coin="USDT", network=network, status=1, limit=5)
                ok(f"deposit/hisrec USDT/{network} → HTTP OK, {len(hist)} recent deposit(s)")
            except BinanceError as e:
                fail(f"deposit/hisrec USDT/{network} failed: {e}")
                errors.append(f"deposit {network}")

        # --- Pay transactions (Binance Pay UID) ---
        print("\nBinance Pay API (Send-to-UID matching)")
        try:
            pay = await client.pay_transactions(limit=10)
            ok(f"pay/transactions → HTTP OK, {len(pay)} recent trade(s)")
            incoming = [t for t in pay if float(t.get("amount", 0)) > 0
                        and t.get("orderType") in ("C2C", "C2C_HOLDING")]
            ok(f"Incoming C2C transfers in last 10: {len(incoming)}")
            if incoming:
                sample = incoming[0]
                usdt = _usdt_amount_from_pay_tx(sample)
                ok(f"Sample parse: {usdt} USDT from tx {sample.get('transactionId', '?')[:20]}…")
        except BinanceError as e:
            fail(f"pay/transactions failed: {e}")
            errors.append("pay transactions")

        # --- Unit: amount parser ---
        print("\nPayment matching logic")
        parsed = _usdt_amount_from_pay_tx({
            "amount": "10",
            "currency": "USDT",
            "fundsDetail": [{"currency": "USDT", "amount": "10"}],
        })
        if parsed == 10.0:
            ok("_usdt_amount_from_pay_tx parses flat 10 USDT")
        else:
            fail(f"amount parser returned {parsed}, expected 10.0")
            errors.append("amount parser")

        await db.init_db()
        pid, amt = await db.create_payment(
            user_id=1, order_id=None, method="binance_pay",
            network="BINANCE_INTERNAL", coin="USDT", base_amount=10.0,
            note="HEALTH", address=None, binance_uid=settings.BINANCE_PAY_UID,
            ttl_seconds=60,
        )
        if amt == 10.0:
            ok(f"create_payment stores clean amount 10.0 (payment #{pid})")
        else:
            fail(f"create_payment amount {amt}, expected 10.0")
            errors.append("create amount")

        match = await db.find_pending_payment_by_amount(10.0, "USDT", "BINANCE_INTERNAL")
        if match and int(match["id"]) == pid:
            ok("find_pending_payment_by_amount matches exact $10 pending")
        else:
            fail("find_pending_payment_by_amount did not match test payment")
            errors.append("amount match")

        # Clean up test row so it doesn't block real deposits.
        async with db.get_db() as conn:
            await conn.execute("DELETE FROM payments WHERE id = ?", (pid,))
            await conn.commit()
        ok("test payment row removed")

        # --- Pending payments audit ---
        print("\nProduction pending payments")
        pending = await db.pending_payments()
        if not pending:
            ok("No pending payments — clean slate for new deposits")
        else:
            print(f"  · {len(pending)} pending payment(s):")
            dup_10 = 0
            for p in pending:
                print(
                    f"    #{p['id']} user={p['user_id']} method={p['method']} "
                    f"amount={p['unique_amount']} order={p['order_id'] or 'top-up'}"
                )
                if p["method"] == "binance_pay" and round(float(p["unique_amount"]), 2) == 10.0:
                    dup_10 += 1
            if dup_10 > 1:
                fail(f"{dup_10} pending Binance Pay $10 payments — only one should exist at a time")
                errors.append("duplicate pending 10")
            else:
                ok("No duplicate pending $10 Binance Pay payments")

        # --- Live poll (no new matches expected unless a deposit just arrived) ---
        print("\nLive poll_payments()")
        try:
            events = await poll_payments()
            ok(f"poll_payments completed — {len(events)} event(s) this run")
            for ev in events:
                if "expired" in ev:
                    print(f"    expired payment #{ev['expired']['id']}")
                else:
                    print(f"    confirmed payment #{ev.get('payment_id')} amount={ev.get('amount')}")
        except Exception as e:
            fail(f"poll_payments raised: {e}")
            errors.append("poll_payments")

    finally:
        await client.close()

    print("\n=== Summary ===")
    if errors:
        print(f"FAILED — {len(errors)} issue(s): {', '.join(errors)}")
        return 1
    print("ALL CHECKS PASSED — Binance API and payment pipeline look healthy.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
