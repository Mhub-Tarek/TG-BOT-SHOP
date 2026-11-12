#!/usr/bin/env python3
"""
Rebuild the Telegram custom-emoji set used for product brand logos, from the
brand PNGs already on disk (assets/brand-logos/) — WITHOUT re-downloading them.

Why this exists
---------------
The shop buttons show a brand logo via a custom emoji whose id is stored in
keyboards/brand_logo_ids.json. Telegram custom-emoji stickers MUST be exactly
100×100 px; if the set is ever (re)created from a wrong-sized PNG the whole set
is rejected/corrupted (`STICKER_PNG_DIMENSIONS` on create, `DOCUMENT_INVALID`
when used) and EVERY product loses its logo. This script always resizes to
100×100 before upload, so it is the safe one-command recovery and never
reintroduces the dimensions footgun.

Unlike setup_brand_logos.py this does NOT hit the network for logos — it reuses
the exact PNGs already present, so the logos come back precisely as they were.

Run on the VPS:
    cd /opt/telegram-shop && .venv/bin/python scripts/rebuild_brand_emojis.py
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import get_settings           # noqa: E402
from keyboards.brands import BRAND_LABELS  # noqa: E402

ASSETS_DIR = ROOT / "assets" / "brand-logos"
OUT_PATH = ROOT / "keyboards" / "brand_logo_ids.json"
EMOJI = "🏷"


def _png_100(path: Path) -> bytes:
    """Return the PNG resized to exactly 100×100 (the only size Telegram accepts
    for custom-emoji stickers)."""
    from PIL import Image

    img = Image.open(path).convert("RGBA")
    if img.size != (100, 100):
        img = img.resize((100, 100), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _set_name(settings) -> str:
    user = (settings.BOT_USERNAME or "Akunding_store_bot").strip().lstrip("@")
    return f"AkundingBrandLogos_by_{user}"


def main() -> int:
    settings = get_settings()
    if not settings.BOT_TOKEN or not settings.ADMIN_IDS:
        print("✗ BOT_TOKEN and ADMIN_IDS required")
        return 1

    # Destructive: deletes + recreates the set, reissuing EVERY custom_emoji_id
    # (logos in already-delivered messages stop rendering). Require explicit opt-in.
    if "--yes" not in sys.argv[1:]:
        print("This will DELETE and RECREATE the brand emoji set, reissuing every "
              "custom_emoji_id — logos in already-delivered messages will break.\n"
              "Only needed to recover a corrupted set. Re-run with --yes to proceed.")
        return 1

    owner = int(settings.ADMIN_IDS[0])
    base = f"https://api.telegram.org/bot{settings.BOT_TOKEN}"
    set_name = _set_name(settings)

    # Brands in catalog order that actually have a PNG on disk.
    order = [b for b in BRAND_LABELS if (ASSETS_DIR / f"{b}.png").is_file()]
    if not order:
        print(f"✗ No brand PNGs found in {ASSETS_DIR}")
        return 1
    print(f"=== Rebuilding {set_name} from {len(order)} on-disk logos (100×100) ===")

    with httpx.Client(timeout=180.0) as client:
        # Drop the old/corrupted set so createNewStickerSet can reuse the name.
        gs = client.get(f"{base}/getStickerSet", params={"name": set_name}).json()
        if gs.get("ok"):
            client.post(f"{base}/deleteStickerSet", data={"name": set_name})
            print(f"  ✓ deleted old set {set_name}")

        uploaded = []
        for brand in order:
            png = _png_100(ASSETS_DIR / f"{brand}.png")
            up = client.post(
                f"{base}/uploadStickerFile",
                data={"user_id": str(owner), "sticker_format": "static"},
                files={"sticker": (f"{brand}.png", png, "image/png")},
            ).json()
            if not up.get("ok"):
                print(f"  ✗ upload {brand}: {up.get('description')}")
                return 1
            uploaded.append({
                "sticker": up["result"]["file_id"],
                "format": "static",
                "emoji_list": [EMOJI],
                "keywords": [brand, "brand", "akunding"],
            })
        print(f"  ✓ uploaded {len(uploaded)} stickers (100×100)")

        cr = client.post(
            f"{base}/createNewStickerSet",
            data={
                "user_id": str(owner),
                "name": set_name,
                "title": "Akunding Brand Logos",
                "stickers": json.dumps(uploaded),
                "sticker_type": "custom_emoji",
                "needs_repainting": "false",
            },
        ).json()
        if not cr.get("ok"):
            print(f"  ✗ createNewStickerSet: {cr.get('description')}")
            return 1
        print("  ✓ created new set")

        stickers = client.get(f"{base}/getStickerSet",
                              params={"name": set_name}).json()["result"]["stickers"]

    ids = {b: str(stickers[i]["custom_emoji_id"])
           for i, b in enumerate(order) if i < len(stickers)}
    OUT_PATH.write_text(json.dumps(ids, indent=2), encoding="utf-8")
    print(f"\n✓ Saved {OUT_PATH} ({len(ids)} brands). Restart the bot to reload the map.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
