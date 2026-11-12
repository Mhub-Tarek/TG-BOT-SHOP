"""Safe visual emoji helpers for buyer-facing Telegram HTML."""
from __future__ import annotations

from html import escape
import re
from typing import Any

from keyboards import mix_icons
from keyboards import product_icons as pi
from keyboards.icons import tg_emoji


LIGHTNING = "\u26a1\ufe0f"
BUY_BUTTON_CUSTOM_EMOJI_ID = "6129870117619634982"

_EMOJI_ALIASES: dict[str, tuple[str, ...]] = {
    "\U0001f310": ("\U0001f310",),
    "\u2705": ("\u2714\ufe0f", "\U0001f7e2"),
    "\U0001f31f": ("\u2b50\ufe0f", "\u2728"),
    "\U0001f48e": ("\U0001f48e",),
    "\U0001f4dc": ("\U0001f4ce", "\U0001f4cc"),
    "\U0001f4cb": ("\U0001f4ce", "\U0001f4cc"),
    "\U0001f6cd": ("\U0001f6cd",),
    "\U0001f4b0": ("\U0001f4b5", "\U0001f4b8"),
    "\U0001f464": ("\U0001f642", "\U0001f440"),
    "\U0001f4b3": ("\U0001f4b5", "\U0001f4b8"),
    "\U0001f3e0": ("\U0001f3e0",),
    "\U0001f6d2": ("\U0001f6cd",),
    "\u270f\ufe0f": ("\u270f\ufe0f",),
    "\u274c": ("\u274c",),
    "\U0001f504": ("\U0001f504",),
    "\U0001f6d1": ("\U0001f6ab", "\u274c"),
    "\U0001f3ab": ("\U0001f4cc", "\U0001f4ac"),
    "\U0001f4ac": ("\U0001f4ac",),
    "\U0001f45b": ("\U0001f4b5", "\U0001f4b8"),
    "\U0001f451": ("\U0001f451",),
    "\u2728": ("\u2728",),
    "\U0001f381": ("\U0001f6cd", "\U0001f389"),
    "\u26a1": ("\u26a1\ufe0f",),
    "\u26a1\ufe0f": ("\u26a1\ufe0f",),
    "\U0001f447": ("\u2b07\ufe0f",),
    "\U0001f4b2": ("\U0001f4b5",),
    "\U0001f4e6": ("\U0001f4e6",),
    "\U0001f4ca": ("\U0001f4ca",),
    "\U0001f4cc": ("\U0001f4cc",),
    "\U0001f194": ("\U0001f4cc",),
    "\U0001f195": ("\U0001f195",),
    "\U0001f9ee": ("\U0001f4ca",),
    "\U0001f39f": ("\U0001f4cc", "\U0001f4ac"),
    "\U0001f44b": ("\U0001f642",),
    "\U0001f3c5": ("\U0001f947",),
    "\U0001f680": ("\u26a1\ufe0f", "\u2728"),
    "\U0001f9fe": ("\U0001f4ce",),
    "\U0001f522": ("\U0001f4ca",),
    "\U0001f4b5": ("\U0001f4b5",),
    "\U0001f4e4": ("\u27a1\ufe0f",),
    "\U0001f3e6": ("\U0001f4b5",),
    "\u26a0\ufe0f": ("\u26a0\ufe0f",),
    "\u2757\ufe0f": ("\u2757\ufe0f",),
    "\U0001f6df": ("\U0001f4ac",),
    "\U0001f198": ("\U0001f4ac", "\u2757\ufe0f"),
    "\U0001f4dd": ("\u270f\ufe0f",),
    "\U0001f6ab": ("\U0001f6ab",),
    "\U0001f389": ("\U0001f389",),
    "\U0001f4a5": ("\U0001f4a5",),
    "\U0001f517": ("\U0001f517",),
    "\U0001f4b8": ("\U0001f4b8",),
    "\U0001f4c9": ("\U0001f4c9",),
    "\U0001f31e": ("\U0001f31e",),
    "\U0001f441": ("\U0001f440",),
    "\U0001f4c4": ("\U0001f4ce",),
    "\U0001f4e5": ("\u2b07\ufe0f",),
    "\U0001f5c2": ("\U0001f4ce",),
    "\U0001f9ff": ("\U0001f48e",),
    "\U0001f947": ("\U0001f947",),
    "\U0001f7e1": ("\U0001f7e2", "\U0001f534"),
    "\U0001f7e2": ("\U0001f7e2",),
    "\U0001f1ec\U0001f1e7": ("\U0001f310",),
    "\U0001f1f8\U0001f1e6": ("\U0001f310",),
    "\U0001f1ee\U0001f1f3": ("\U0001f310",),
    "\U0001f1fb\U0001f1f3": ("\U0001f310",),
    "\U0001f1e8\U0001f1f3": ("\U0001f310",),
    "\u25c0\ufe0f": ("\U0001f504",),
    "\u25c0": ("\U0001f504",),
    "\u27a1\ufe0f": ("\u27a1\ufe0f",),
    "\u2b05\ufe0f": ("\U0001f504",),
    "\U0001f53b": ("\U0001f4c9",),
}

_EMOJI_TOKENS = sorted(_EMOJI_ALIASES, key=len, reverse=True)
_SKIP_HYPE_RE = re.compile(
    r"(<(?:code|pre)\b[^>]*>.*?</(?:code|pre)>|"
    r"<tg-emoji\b[^>]*>.*?</tg-emoji>)",
    re.IGNORECASE | re.DOTALL,
)


def _value(source: Any, *keys: str, default: str = "") -> str:
    for key in keys:
        try:
            if hasattr(source, "keys") and key in source.keys():
                value = source[key]
            elif isinstance(source, dict) and key in source:
                value = source[key]
            else:
                continue
        except (KeyError, TypeError):
            continue
        if value is not None:
            return str(value)
    return default


def tge(char: str, *, index: int = 0, fallback: str | None = None) -> str:
    """Return a custom animated emoji tag when available, else plain Unicode."""
    return mix_icons.tge(char, index=index, fallback=fallback or char)


def custom_id_for_char(char: str) -> str | None:
    if char == "\U0001f4e6":
        return None
    for candidate in _EMOJI_ALIASES.get(char, (char,)):
        emoji_id = mix_icons.get(candidate)
        if emoji_id:
            return emoji_id
    return None


def _exact_custom_id(char: str, index: int) -> str | None:
    try:
        ids = (mix_icons._load().get("by_emoji") or {}).get(char) or []
    except Exception:
        return None
    if 0 <= index < len(ids):
        return str(ids[index])
    return None


def store_icon_id() -> str | None:
    """Custom emoji id for Shop/Store navigation."""
    return mix_icons.get("\U0001f6cd")


def preferred_buy_icon_id() -> str | None:
    """Exact BUY custom emoji for Buy Now buttons."""
    return BUY_BUTTON_CUSTOM_EMOJI_ID


def hype_text(text: str | None) -> str | None:
    """Replace known plain emoji with HTML custom-emoji tags.

    Code blocks and existing tg-emoji tags are left untouched, so delivered
    product data and already-hyped icons do not get mangled.
    """
    if text is None:
        return None
    raw = str(text)
    parts = _SKIP_HYPE_RE.split(raw)
    for i, part in enumerate(parts):
        if not part or _SKIP_HYPE_RE.fullmatch(part):
            continue
        for token in _EMOJI_TOKENS:
            if token not in part:
                continue
            emoji_id = custom_id_for_char(token)
            if emoji_id:
                part = part.replace(token, tg_emoji(emoji_id, token))
        parts[i] = part
    return "".join(parts)


def clean_button_text(text: str) -> str:
    """Telegram button text cannot render HTML; remove static emoji glyphs."""
    value = str(text or "")
    for token in _EMOJI_TOKENS:
        value = value.replace(token, "")
    return " ".join(value.split()).strip()


def sparkle() -> str:
    return tge("✨")


def fire() -> str:
    return tge("🔥")


def down_arrow() -> str:
    return tge("👇")


def crown() -> str:
    return tge("👑")


def check() -> str:
    return tge("✅")


def support() -> str:
    return tge("🆘")


def money() -> str:
    return tge("💰")


def download() -> str:
    return tge("📥")


def rocket() -> str:
    return tge("🚀")


def diamond() -> str:
    return tge("💎")


def buy_button_icon_id() -> str | None:
    """Custom emoji id for the red BUY/shopping icon on Buy Now buttons."""
    return preferred_buy_icon_id()


def loading_flash() -> str:
    """Large custom lightning emoji shown briefly between Buy Now and quantity."""
    return tge(LIGHTNING, fallback=LIGHTNING)


def product_icon_for(product: Any, stock: int = 1, *, fallback: str = "📦") -> str:
    """HTML-safe product icon for message text.

    Prefer the same real product/brand custom emoji used in inline buttons. If
    the pack is missing, fall back to the product emoji after HTML escaping.
    """
    view = {
        "name": _value(product, "name", "product_name"),
        "emoji": _value(product, "emoji", "product_emoji"),
        "brand": _value(product, "brand", "product_brand"),
        "icon_custom_emoji_id": _value(
            product, "icon_custom_emoji_id", "product_icon_emoji_id",
        ),
    }
    try:
        emoji_id = pi.icon_id_for_product(view, stock)
    except Exception:
        emoji_id = None
    if emoji_id:
        return tg_emoji(emoji_id, fallback)
    generic_id = custom_id_for_char(fallback)
    if fallback != "\U0001f4e6" and not generic_id:
        generic_id = custom_id_for_char("\U0001f6cd")
    if generic_id:
        return tg_emoji(generic_id, fallback)
    return escape(fallback) if fallback else ""
