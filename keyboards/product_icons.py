"""Telegram custom emoji IDs for shop product buttons."""
from __future__ import annotations

import json
from pathlib import Path

from keyboards.brands import product_brand

_IDS_PATH = Path(__file__).parent / "product_emoji_ids.json"
_BRAND_IDS_PATH = Path(__file__).parent / "brand_logo_ids.json"

# Fallback map: product emoji field → JSON key (colored dots)
GLYPH_KEYS: dict[str, str] = {
    "🟢": "green",
    "🔵": "blue",
    "🟣": "purple",
    "🟠": "orange",
    "🟡": "yellow",
    "🔴": "red",
    "⚪": "white",
    "🟤": "brown",
    "⚫": "black",
}


def _load(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def icon_id_for_product(product, stock: int) -> str | None:
    """
    Resolve custom emoji id:
    1. Manual icon_custom_emoji_id
    2. Brand logo (ElevenLabs, Gemini, Claude, …)
    3. Out-of-stock icon
    4. Color dot from emoji field
    5. Default dot
    """
    custom = (product["icon_custom_emoji_id"] if "icon_custom_emoji_id" in product.keys() else None) or ""
    custom = str(custom).strip()
    if custom:
        return custom

    brand_ids = _load(_BRAND_IDS_PATH)
    brand = product_brand(product)
    if brand:
        brand_id = brand_ids.get(brand)
        if brand_id:
            return brand_id
    # DB brand set but missing from BRAND_LABELS (partial deploy) — still use logo map.
    if "brand" in product.keys():
        slug = str(product["brand"] or "").strip().lower()
        if slug:
            pinned = brand_ids.get(slug)
            if pinned:
                return pinned

    dots = _load(_IDS_PATH)
    if stock <= 0 and dots.get("out_of_stock"):
        return dots["out_of_stock"]

    glyph = (product["emoji"] or "").strip()
    key = GLYPH_KEYS.get(glyph)
    if key and dots.get(key):
        return dots[key]
    return dots.get("default")
