"""
Binance REST API client.

Used endpoints:
  GET /sapi/v1/capital/deposit/hisrec   -> recent deposit history (signed)

Auth: API-Key in `X-MBX-APIKEY` header, query string signed with HMAC-SHA256
over the urlencoded params (incl. `timestamp` and `recvWindow`).

Docs: https://developers.binance.com/docs/wallet/capital
"""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any
from urllib.parse import urlencode

import httpx

BASE_URL = "https://api.binance.com"


class BinanceError(Exception):
    pass


class BinanceClient:
    def __init__(self, api_key: str, api_secret: str, base_url: str = BASE_URL,
                 timeout: float = 15.0):
        self.api_key = api_key
        self.api_secret = api_secret.encode("utf-8") if api_secret else b""
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    def _sign(self, params: dict[str, Any]) -> str:
        qs = urlencode(params, doseq=True)
        sig = hmac.new(self.api_secret, qs.encode("utf-8"), hashlib.sha256).hexdigest()
        return qs + "&signature=" + sig

    async def _signed_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if not self.api_key or not self.api_secret:
            raise BinanceError("Binance API key/secret not configured")
        params = dict(params or {})
        params.setdefault("recvWindow", 10_000)
        params["timestamp"] = int(time.time() * 1000)
        url = f"{self.base_url}{path}?{self._sign(params)}"
        headers = {"X-MBX-APIKEY": self.api_key}
        resp = await self._client.get(url, headers=headers)
        if resp.status_code >= 400:
            raise BinanceError(f"Binance {resp.status_code}: {resp.text}")
        return resp.json()

    async def server_time_ms(self) -> int:
        """Public endpoint — no signing. Used at startup to detect clock skew."""
        resp = await self._client.get(f"{self.base_url}/api/v3/time")
        if resp.status_code >= 400:
            raise BinanceError(f"Binance {resp.status_code}: {resp.text}")
        return int(resp.json()["serverTime"])

    async def deposit_history(self, coin: str | None = None, network: str | None = None,
                              status: int | None = None, limit: int = 100,
                              start_time_ms: int | None = None) -> list[dict[str, Any]]:
        """
        Returns recent deposits. Each item shape (selected fields):
          {
            "amount": "10.00000000",
            "coin": "USDT",
            "network": "TRX",
            "status": 1,            # 0=pending, 6=credited but cannot withdraw, 1=success
            "address": "T...",
            "addressTag": "",
            "txId": "abcd1234...",
            "insertTime": 1716540000000,
            "transferType": 0,
            "confirmTimes": "1/1"
          }
        """
        params: dict[str, Any] = {"limit": limit}
        if coin:
            params["coin"] = coin
        if network:
            params["network"] = network
        if status is not None:
            params["status"] = status
        if start_time_ms:
            params["startTime"] = start_time_ms
        return await self._signed_get("/sapi/v1/capital/deposit/hisrec", params)

    async def pay_transactions(self, *, limit: int = 100,
                               start_time_ms: int | None = None,
                               end_time_ms: int | None = None) -> list[dict[str, Any]]:
        """
        Binance Pay trade history (C2C / Send-to-UID transfers).
        Docs: GET /sapi/v1/pay/transactions
        """
        params: dict[str, Any] = {"limit": min(limit, 100)}
        if start_time_ms:
            params["startTime"] = start_time_ms
        if end_time_ms:
            params["endTime"] = end_time_ms
        payload = await self._signed_get("/sapi/v1/pay/transactions", params)
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, list):
                return data
        if isinstance(payload, list):
            return payload
        return []
