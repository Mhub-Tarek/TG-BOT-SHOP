from __future__ import annotations

import os
from functools import lru_cache
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Telegram
    BOT_TOKEN: str
    BOT_USERNAME: str = "@mhubdigital_bot"
    ADMIN_IDS: List[int] = Field(default_factory=list)

    # Branding
    SHOP_NAME: str = "꧁☠️☬MediaHub | Digital Marketplace☬☠️꧂"
    WELCOME_BLURB: str = (
        "Welcome to ꧁☠️☬MediaHub | Digital Marketplace☬☠️꧂ — premium digital products at the best prices. "
        "Fast, secure, and fully automated delivery."
    )
    SUPPORT_USERNAME: str = "@MediaHubDigitalMarketplace"
    CHANNEL_URL: str = ""
    DEFAULT_DELIVERY_PROMISE: str = "💚 Instant Code Delivery\n🛡 Full Year Warranty"

    # Binance API
    BINANCE_API_KEY: str = ""
    BINANCE_API_SECRET: str = ""

    # Payment methods
    BINANCE_PAY_UID: str = ""
    USDT_BEP20_ADDRESS: str = ""
    USDT_TRC20_ADDRESS: str = ""
    TRONGRID_API_KEY: str = ""
    ANKR_API_KEY: str = ""
    ANKR_ADVANCED_API_URL: str = ""
    TRON_USDT_CONTRACT: str = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
    BSC_USDT_CONTRACT: str = "0x55d398326f99059ff775485246999027b3197955"
    PAYMENT_TTL_MINUTES: int = 15
    POLL_INTERVAL_SECONDS: int = 30
    MIN_DEPOSIT_USDT: float = 1.0
    MAX_DEPOSIT_USDT: float = 5000.0

    # Web admin
    # Bind to loopback by default: the dashboard handles real money, so it must
    # sit behind the nginx reverse proxy (127.0.0.1:8000) with TLS, not be
    # exposed directly on a public interface. Override to 0.0.0.0 only behind a
    # firewall you control. (review H2)
    # Railway/PaaS: auto-detects PORT env var and binds 0.0.0.0 when present
    WEB_HOST: str = os.environ.get("WEB_HOST") or ("0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")
    WEB_PORT: int = int(os.environ.get("WEB_PORT") or os.environ.get("PORT") or 8000)
    WEB_ADMIN_PASSWORD: str = "change-me-now"
    WEB_SESSION_SECRET: str = "change-me-too"
    # Mark the session cookie Secure by default (HTTPS-only). Set false only for
    # plain-HTTP local dev. (review H2)
    WEB_SECURE_COOKIES: bool = True
    WEB_LOGIN_MAX_ATTEMPTS: int = 5            # Per IP per WEB_LOGIN_WINDOW_SECONDS
    WEB_LOGIN_WINDOW_SECONDS: int = 300

    # Trusted reverse-proxy IPs whose X-Forwarded-For uvicorn should honour, so
    # the login rate-limiter sees the real client IP instead of 127.0.0.1. (review M8)
    WEB_FORWARDED_ALLOW_IPS: str = "127.0.0.1"

    # Refund calculator (refund.akunding.shop → nginx → loopback)
    # Railway/PaaS: auto-detects PORT env var and binds 0.0.0.0 when present
    REFUND_WEB_HOST: str = os.environ.get("REFUND_WEB_HOST") or ("0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")
    REFUND_WEB_PORT: int = int(os.environ.get("REFUND_WEB_PORT") or os.environ.get("PORT") or 8010)
    REFUND_WINDOW_DAYS: int = 365

    # Referral
    REFERRAL_PERCENT: float = 5.0

    @field_validator("ADMIN_IDS", mode="before")
    @classmethod
    def _parse_admin_ids(cls, v):
        if isinstance(v, int):
            return [v]
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("["):
                import json
                return json.loads(v)
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        return v

    @field_validator("DEFAULT_DELIVERY_PROMISE", mode="before")
    @classmethod
    def _unescape_newlines(cls, v):
        if isinstance(v, str):
            return v.replace("\\n", "\n")
        return v

    def is_admin(self, tg_id: int | None) -> bool:
        """Single source of truth for "is this Telegram id an admin?". Every
        money-sensitive admin check (unlimited wallet, ∞ display, financial
        exclusion) must route through here so the rule can never drift between
        call sites. (review M5)"""
        if tg_id is None:
            return False
        return int(tg_id) in (self.ADMIN_IDS or [])

    def using_default_web_secrets(self) -> list[str]:
        """Names of any web credentials still left at their shipped defaults.
        An empty list means it's safe to serve the admin panel. (review H1)"""
        bad: list[str] = []
        if self.WEB_ADMIN_PASSWORD == "change-me-now":
            bad.append("WEB_ADMIN_PASSWORD")
        if self.WEB_SESSION_SECRET == "change-me-too":
            bad.append("WEB_SESSION_SECRET")
        return bad

    def enabled_methods(self) -> list[str]:
        """Which payment buttons should be shown to users."""
        methods = ["wallet"]
        if self.BINANCE_PAY_UID:
            methods.append("binance_pay")
        if self.USDT_BEP20_ADDRESS:
            methods.append("usdt_bep20")
        if self.USDT_TRC20_ADDRESS:
            methods.append("usdt_trc20")
        return methods


@lru_cache
def get_settings() -> Settings:
    return Settings()


# Payment method metadata (labels come from keyboards.icons)
PAYMENT_METADATA = {
    "wallet": {
        "label": "👛 Pay via Wallet",
        "coin": "USDT",
        "network": "WALLET",
    },
    "binance_pay": {
        "label": "🟡 Binance Pay",
        "coin": "USDT",
        "network": "BINANCE_INTERNAL",
        "instructions_hint": "Send via Binance app → Send → choose Binance ID",
    },
    "usdt_bep20": {
        "label": "🟡 USDT BEP20",
        "coin": "USDT",
        "network": "BSC",
        "instructions_hint": "Send USDT on the BNB Smart Chain (BEP20) network",
    },
    "usdt_trc20": {
        "label": "🟢 USDT TRC20",
        "coin": "USDT",
        "network": "TRX",
        "instructions_hint": "Send USDT on the TRON (TRC20) network",
    },
}
