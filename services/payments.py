from __future__ import annotations

import json
import logging
import secrets
import string
import time

import httpx
import db
from config import PAYMENT_METADATA, get_settings
from services.binance import BinanceClient, BinanceError
from services.delivery import OutOfStock, confirm_and_settle, deliver_order

__all__ = [
    "get_binance", "close_binance",
    "create_order_payment", "create_topup_payment",
    "poll_payments", "maybe_credit_referrer",
    "confirm_and_settle",   # re-exported from services.delivery
]

log = logging.getLogger(__name__)
settings = get_settings()

# Binance tx must land at or after pending payment creation (minus small clock skew).
PAYMENT_TIME_GRACE_SECONDS = 120


def payment_tx_time_allowed(payment_row, tx_time_sec: float) -> bool:
    """Reject Binance transfers that predate the pending payment row."""
    created = int(payment_row["created_at"])
    return tx_time_sec >= created - PAYMENT_TIME_GRACE_SECONDS


def _pay_tx_time_sec(tx: dict) -> float | None:
    raw = tx.get("transactionTime")
    if raw is None:
        return None
    try:
        return int(raw) / 1000.0
    except (TypeError, ValueError):
        return None


def _onchain_tx_time_sec(deposit: dict) -> float | None:
    raw = deposit.get("insertTime")
    if raw is None:
        return None
    try:
        return int(raw) / 1000.0
    except (TypeError, ValueError):
        return None


def _normalize_address(value: str | None) -> str:
    return str(value or "").strip().lower()


def _ankr_advanced_url() -> str:
    configured = str(settings.ANKR_ADVANCED_API_URL or "").strip()
    if configured:
        return configured
    key = str(settings.ANKR_API_KEY or "").strip()
    if key:
        return f"https://rpc.ankr.com/multichain/{key}"
    return ""


async def _fetch_tron_usdt_deposits(min_created: int, address: str | None = None) -> list[dict]:
    """Fetch incoming USDT TRC20 transfers to a bot payment address."""
    address = str(address or settings.USDT_TRC20_ADDRESS or "").strip()
    if not address:
        return []

    params = {
        "only_confirmed": "true",
        "only_to": "true",
        "limit": "200",
        "contract_address": str(settings.TRON_USDT_CONTRACT).strip(),
        "order_by": "block_timestamp,desc",
        "min_timestamp": str((min_created - PAYMENT_TIME_GRACE_SECONDS) * 1000),
    }
    headers = {}
    if settings.TRONGRID_API_KEY:
        headers["TRON-PRO-API-KEY"] = settings.TRONGRID_API_KEY

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"https://api.trongrid.io/v1/accounts/{address}/transactions/trc20",
                params=params,
                headers=headers,
            )
            resp.raise_for_status()
            payload = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("poll_payments: TronGrid fetch failed for TRX/USDT: %s", exc)
        return []

    expected_to = address
    expected_contract = str(settings.TRON_USDT_CONTRACT or "").strip()
    out: list[dict] = []
    for tx in payload.get("data") or []:
        token = tx.get("token_info") or {}
        if str(token.get("address") or "").strip() != expected_contract:
            continue
        if str(tx.get("to") or "").strip() != expected_to:
            continue
        txid = str(tx.get("transaction_id") or "").strip()
        if not txid:
            continue
        try:
            decimals = int(token.get("decimals") or 6)
            amount = int(str(tx.get("value") or "0")) / (10 ** decimals)
            insert_ms = int(tx.get("block_timestamp") or 0)
        except (TypeError, ValueError):
            continue
        if amount <= 0 or insert_ms <= 0:
            continue
        out.append({
            "amount": str(amount),
            "coin": "USDT",
            "network": "TRX",
            "address": expected_to,
            "txId": txid,
            "insertTime": insert_ms,
            "raw_source": tx,
        })
    return out


async def _fetch_bsc_usdt_deposits(min_created: int, address: str | None = None) -> list[dict]:
    """Fetch incoming USDT BEP20 transfers to a bot payment address."""
    address = str(address or settings.USDT_BEP20_ADDRESS or "").strip()
    endpoint = _ankr_advanced_url()
    if not address or not endpoint:
        if address:
            log.warning("poll_payments: ANKR_API_KEY/ANKR_ADVANCED_API_URL missing for BSC scanner")
        return []

    expected_to = _normalize_address(address)
    expected_contract = _normalize_address(settings.BSC_USDT_CONTRACT)
    from_ts = max(0, min_created - PAYMENT_TIME_GRACE_SECONDS)
    to_ts = int(time.time()) + 120
    transfers: list[dict] = []
    page_token = ""

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            for _page in range(3):
                params = {
                    "blockchain": "bsc",
                    "address": [address],
                    "fromTimestamp": from_ts,
                    "toTimestamp": to_ts,
                    "descOrder": True,
                    "pageSize": 100,
                }
                if page_token:
                    params["pageToken"] = page_token
                resp = await client.post(
                    endpoint,
                    json={
                        "jsonrpc": "2.0",
                        "method": "ankr_getTokenTransfers",
                        "params": params,
                        "id": 1,
                    },
                )
                resp.raise_for_status()
                payload = resp.json()
                if payload.get("error"):
                    log.warning("poll_payments: Ankr BSC fetch failed: %s", payload["error"])
                    return transfers
                result = payload.get("result") or {}
                transfers.extend(result.get("transfers") or [])
                page_token = str(result.get("nextPageToken") or "")
                if not page_token:
                    break
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("poll_payments: Ankr BSC fetch failed: %s", exc)
        return transfers

    out: list[dict] = []
    for tx in transfers:
        if str(tx.get("direction") or "").lower() != "in":
            continue
        if _normalize_address(tx.get("toAddress")) != expected_to:
            continue
        if _normalize_address(tx.get("contractAddress")) != expected_contract:
            continue
        if str(tx.get("tokenSymbol") or "").upper() != "USDT":
            continue
        txid = str(tx.get("transactionHash") or "").strip()
        if not txid:
            continue
        try:
            amount = float(tx.get("value") or 0)
            insert_ms = int(float(tx.get("timestamp") or 0) * 1000)
        except (TypeError, ValueError):
            continue
        if amount <= 0 or insert_ms <= 0:
            continue
        out.append({
            "amount": str(amount),
            "coin": "USDT",
            "network": "BSC",
            "address": address,
            "txId": txid,
            "insertTime": insert_ms,
            "raw_source": tx,
        })
    return out


async def _is_admin_order(payment_row) -> bool:
    """True if a matched order-payment belongs to an admin account, so the
    crypto-confirm delivery clears the consumed stock's cost just like the
    unlimited wallet path. Top-ups (no order_id) are never admin orders. (review M6)"""
    if not payment_row["order_id"]:
        return False
    owner = await db.get_user(int(payment_row["user_id"]))
    return bool(owner) and settings.is_admin(owner["tg_id"])


def _pay_receiver_matches(tx: dict) -> bool:
    expected = str(settings.BINANCE_PAY_UID or "").strip()
    if not expected:
        return True
    receiver = tx.get("receiverInfo") or {}
    candidates = {
        str(tx.get("uid", "")).strip(),
        str(receiver.get("binanceId", "")).strip(),
    }
    return expected in candidates


_binance: BinanceClient | None = None


def get_binance() -> BinanceClient:
    global _binance
    if _binance is None:
        _binance = BinanceClient(settings.BINANCE_API_KEY, settings.BINANCE_API_SECRET)
    return _binance


async def close_binance() -> None:
    global _binance
    if _binance is not None:
        await _binance.close()
        _binance = None


def _alnum_note(n: int = 6) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


def address_for_method(method: str) -> str | None:
    if method == "usdt_trc20":
        return settings.USDT_TRC20_ADDRESS or None
    if method == "usdt_bep20":
        return settings.USDT_BEP20_ADDRESS or None
    return None


def uid_for_method(method: str) -> str | None:
    if method == "binance_pay":
        return settings.BINANCE_PAY_UID or None
    return None


def _usdt_amount_from_pay_tx(tx: dict) -> float | None:
    """Extract incoming USDT amount from a Binance Pay transaction."""
    for item in tx.get("fundsDetail") or []:
        if str(item.get("currency", "")).upper() == "USDT":
            return abs(float(item.get("amount", 0)))
    if str(tx.get("currency", "")).upper() == "USDT":
        return abs(float(tx.get("amount", 0)))
    return None


async def _recover_or_flag(
    coin: str,
    network: str,
    amount: float,
    txid: str,
    raw_json: str,
    *,
    allow_recovery: bool = True,
    manual_if_unrecovered: bool = False,
) -> dict | None:
    """Recover bot-related late funds; ignore transfers unrelated to bot invoices."""
    if not txid or await db.txid_already_used(txid):
        return None

    expired_count = await db.count_recent_expired_payments_by_amount(
        amount, coin, network,
    )
    expired = (
        await db.find_recently_expired_payment_by_amount(amount, coin, network)
        if allow_recovery else None
    )
    if expired:
        ok = await db.recover_expired_payment_to_wallet(
            int(expired["id"]), txid, raw_json, amount,
        )
        if ok:
            if not expired["order_id"]:
                try:
                    await maybe_credit_referrer(int(expired["user_id"]), amount)
                except Exception:
                    log.exception(
                        "late-deposit referrer credit failed for payment #%s",
                        expired["id"],
                    )
            log.warning(
                "poll_payments: recovered late %s/%s deposit txid=%s amount=%s "
                "to user_id=%s from expired payment #%s",
                coin, network, txid, amount, expired["user_id"], expired["id"],
            )
            return {
                "recovered": True,
                "payment_id": int(expired["id"]),
                "user_id": int(expired["user_id"]),
                "order_id": None,
                "amount": amount,
                "coin": coin,
                "network": network,
                "txid": txid,
            }

    if expired_count == 0 and not manual_if_unrecovered:
        dep_id = await db.record_ignored_deposit(
            coin, network, amount, txid, raw_json,
        )
        if dep_id is not None:
            log.info(
                "poll_payments: ignored unrelated %s/%s deposit txid=%s amount=%s "
                "(no matching bot invoice)",
                coin, network, txid, amount,
            )
        return None

    dep_id = await db.record_unmatched_deposit(coin, network, amount, txid, raw_json)
    if dep_id is None:
        return None
    log.warning(
        "poll_payments: UNMATCHED verified %s/%s deposit txid=%s amount=%s "
        "recorded as unmatched_deposits #%s; manual review needed",
        coin, network, txid, amount, dep_id,
    )
    return {
        "unmatched": True,
        "deposit_id": dep_id,
        "amount": amount,
        "coin": coin,
        "network": network,
        "txid": txid,
    }


async def _poll_onchain_deposits(candidates: list) -> list[dict]:
    """Match pending on-chain (TRC20/BEP20) payments via Trust Wallet scanners."""
    out: list[dict] = []
    pairs: dict[tuple[str, str], list] = {}
    for p in candidates:
        if p["network"] == "BINANCE_INTERNAL":
            continue
        key = (p["coin"], p["network"])
        pairs.setdefault(key, []).append(p)

    for (coin, network), group in pairs.items():
        min_created = min(int(p["created_at"]) for p in group)
        addresses: list[str] = []
        seen_addresses: set[str] = set()
        for p in group:
            address = str(p["address"] or "").strip()
            if not address:
                address = (
                    str(settings.USDT_TRC20_ADDRESS or "").strip()
                    if network == "TRX"
                    else str(settings.USDT_BEP20_ADDRESS or "").strip()
                )
            normalized = _normalize_address(address)
            if address and normalized not in seen_addresses:
                addresses.append(address)
                seen_addresses.add(normalized)

        if coin != "USDT":
            log.warning("poll_payments: unsupported on-chain coin %s/%s", coin, network)
            continue
        if not addresses:
            continue
        history: list[dict] = []
        if network == "TRX":
            for address in addresses:
                history.extend(await _fetch_tron_usdt_deposits(min_created, address))
        elif network == "BSC":
            for address in addresses:
                history.extend(await _fetch_bsc_usdt_deposits(min_created, address))
        else:
            log.warning("poll_payments: unsupported on-chain network %s/%s", coin, network)
            continue

        for h in history:
            txid = str(h.get("txId", ""))
            if not txid:
                continue
            if await db.txid_already_used(txid):
                continue
            amount = float(h.get("amount", 0))
            if str(h.get("network", "")).upper() != network.upper():
                continue

            tx_time = _onchain_tx_time_sec(h)
            if tx_time is None:
                log.warning("poll_payments: on-chain deposit %s missing insertTime", txid)
                continue

            match = await db.find_pending_payment_by_amount(amount, coin, network)
            raw_json = json.dumps(h.get("raw_source") or h, ensure_ascii=False)
            if not match:
                event = await _recover_or_flag(
                    coin, network, amount, txid, raw_json,
                )
                if event:
                    out.append(event)
                continue
            expected_address = str(match["address"] or "").strip().lower()
            tx_address = str(h.get("address") or "").strip().lower()
            # When the pending payment has a configured destination address,
            # REQUIRE the deposit to land on it. Previously the check was
            # skipped whenever the tx address was empty (Binance omits it for
            # some transfer shapes), letting a deposit confirm without any
            # address verification. Treat absent-or-mismatched as a hard fail. (review M9)
            if expected_address and expected_address != tx_address:
                log.warning(
                    "poll_payments: rejected %s tx %s for unexpected/missing address "
                    "(expected=%s got=%r)",
                    network, txid, expected_address, tx_address,
                )
                event = await _recover_or_flag(
                    coin, network, amount, txid, raw_json,
                    manual_if_unrecovered=True,
                )
                if event:
                    out.append(event)
                continue
            if not payment_tx_time_allowed(match, tx_time):
                log.warning(
                    "poll_payments: rejected stale on-chain tx %s (tx=%s, payment #%s created=%s)",
                    txid, tx_time, match["id"], match["created_at"],
                )
                event = await _recover_or_flag(
                    coin, network, amount, txid, raw_json,
                )
                if event:
                    out.append(event)
                continue
            # A current pending invoice wins when the verified transaction time
            # fits that invoice. Older expired invoices may reuse the same amount
            # later; they should not block a buyer who just paid their active
            # invoice. Late-deposit recovery only runs when no active match
            # exists, or when the tx predates the active invoice.
            event = await confirm_and_settle(
                match, txid, raw_json, amount,
                is_admin_order=await _is_admin_order(match),
            )
            if event:
                out.append(event)
    return out


async def _poll_binance_pay(binance: BinanceClient, candidates: list) -> list[dict]:
    """Match pending Binance Pay (Send-to-UID) payments via Pay trade history."""
    out: list[dict] = []
    pay_candidates = [p for p in candidates if p["method"] == "binance_pay"]
    if not pay_candidates:
        return out

    min_created = min(int(p["created_at"]) for p in pay_candidates)
    start_ms = (min_created - PAYMENT_TIME_GRACE_SECONDS) * 1000
    try:
        history = await binance.pay_transactions(limit=100, start_time_ms=start_ms)
    except BinanceError as e:
        log.warning("poll_payments: binance pay fetch failed: %s", e)
        return out

    for tx in history:
        txid = str(tx.get("transactionId", ""))
        if not txid:
            continue
        if await db.txid_already_used(txid):
            continue
        if tx.get("orderType") not in ("C2C", "C2C_HOLDING"):
            continue
        if not _pay_receiver_matches(tx):
            log.warning("poll_payments: rejected Binance Pay tx %s for wrong receiver", txid)
            continue
        amount = float(tx.get("amount", 0))
        if amount <= 0:
            continue  # receiver perspective: positive = incoming

        usdt = _usdt_amount_from_pay_tx(tx)
        if usdt is None or usdt <= 0:
            continue

        tx_time = _pay_tx_time_sec(tx)
        if tx_time is None:
            log.warning("poll_payments: Binance Pay tx %s missing transactionTime", txid)
            continue

        match = await db.find_pending_payment_by_amount(
            usdt, "USDT", "BINANCE_INTERNAL",
        )
        if not match or match["method"] != "binance_pay":
            event = await _recover_or_flag(
                "USDT", "BINANCE_INTERNAL", usdt, txid, json.dumps(tx),
            )
            if event:
                out.append(event)
            continue
        if not payment_tx_time_allowed(match, tx_time):
            log.warning(
                "poll_payments: rejected stale Binance Pay tx %s (tx=%s, payment #%s created=%s)",
                txid, tx_time, match["id"], match["created_at"],
            )
            event = await _recover_or_flag(
                "USDT", "BINANCE_INTERNAL", usdt, txid, json.dumps(tx),
            )
            if event:
                out.append(event)
            continue
        # A current pending invoice wins when the verified transaction time fits
        # it. Same-amount expired invoices are handled only when there is no
        # active match, or when the tx predates the active invoice.
        event = await confirm_and_settle(
            match, txid, json.dumps(tx), usdt,
            is_admin_order=await _is_admin_order(match),
        )
        if event:
            out.append(event)
    return out


async def create_order_payment(*, user_id: int, order_id: int, method: str,
                               amount: float) -> int:
    """Create a pending payment for an order. Returns payment_id."""
    if method == "wallet":
        raise ValueError("Wallet payments don't use the payments table")
    existing = await db.get_pending_payment_for_order_method(order_id, method)
    if existing:
        return int(existing["id"])

    meta = PAYMENT_METADATA[method]
    coin, network = meta["coin"], meta["network"]
    pid, _amount = await db.create_payment(
        user_id=user_id, order_id=order_id,
        method=method, network=network, coin=coin,
        base_amount=amount, note=_alnum_note(6),
        address=address_for_method(method), binance_uid=uid_for_method(method),
        ttl_seconds=settings.PAYMENT_TTL_MINUTES * 60,
    )
    await db.cancel_other_pending_payments_for_order(order_id, pid)
    return pid


async def create_topup_payment(*, user_id: int, method: str, amount: float) -> int:
    if method == "wallet":
        raise ValueError("Cannot top up wallet from wallet")
    meta = PAYMENT_METADATA[method]
    coin, network = meta["coin"], meta["network"]
    pid, _amount = await db.create_payment(
        user_id=user_id, order_id=None,
        method=method, network=network, coin=coin,
        base_amount=amount, note=_alnum_note(6),
        address=address_for_method(method), binance_uid=uid_for_method(method),
        ttl_seconds=settings.PAYMENT_TTL_MINUTES * 60,
    )
    return pid


async def poll_payments() -> list[dict]:
    """
    Match pending payments to Binance deposit history. Returns a list of
    event dicts (one per confirmation or expiry) so bot.py can DM users.
    """
    expired = await db.expire_old_payments()
    pending = await db.pending_payments()
    recent_expired = await db.recent_expired_payments()
    candidates = [*pending, *recent_expired]

    out: list[dict] = []

    if candidates:
        has_binance_pay = any(p["method"] == "binance_pay" for p in candidates)
        if has_binance_pay:
            binance = get_binance()
            out.extend(await _poll_binance_pay(binance, candidates))
        out.extend(await _poll_onchain_deposits(candidates))

    for e in expired:
        current = await db.get_payment(int(e["id"]))
        if current and current["status"] == "expired":
            out.append({"expired": e})
    return out


async def maybe_credit_referrer(user_id: int, deposited_amount: float) -> None:
    """Legacy top-up hook kept for callers.

    Referral rewards now credit only after the referred user's first
    qualifying delivered order. Wallet deposits/top-ups intentionally do not
    pay referral bonuses.
    """
    return None
    pct = settings.REFERRAL_PERCENT
    if pct <= 0:
        return
    from db import get_db
    async with get_db() as d:
        cur = await d.execute("SELECT referrer_id FROM users WHERE id = ?", (user_id,))
        user = await cur.fetchone()
        if not user or not user["referrer_id"]:
            return
        cur = await d.execute(
            "SELECT COUNT(*) AS n FROM payments "
            "WHERE user_id = ? AND order_id IS NULL AND status = 'confirmed'",
            (user_id,),
        )
        n_topups = int((await cur.fetchone())["n"])
    # Pay the referral bonus ONLY on the referred user's first confirmed
    # deposit — not on every top-up. Otherwise a user with two accounts (one
    # referring the other) farms REFERRAL_PERCENT on every dollar cycled
    # through, withdrawable as store credit. (review referral)
    if n_topups != 1:
        return
    bonus = round(deposited_amount * pct / 100.0, 4)
    if bonus > 0:
        await db.add_balance(int(user["referrer_id"]), bonus)
