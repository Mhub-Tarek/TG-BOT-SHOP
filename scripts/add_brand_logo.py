#!/usr/bin/env python3
"""
Incrementally add ONE brand logo to the existing Telegram custom-emoji pack
without regenerating the whole set.

Why not just rerun setup_brand_logos.py? That deletes and recreates the pack,
which reissues a fresh custom_emoji_id for every brand. Any logo already shown
in a delivered/broadcast message would stop rendering. This script instead
uses addStickerToSet, so existing IDs are untouched — only the new brand's ID
is appended and merged into brand_logo_ids.json.

Run on VPS:
  cd /opt/telegram-shop && .venv/bin/python scripts/add_brand_logo.py coursera
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import get_settings  # noqa: E402
from keyboards.brands import (  # noqa: E402
    BRAND_ICON_URL,
    BRAND_LABELS,
    FAVICON_DOMAIN,
    ICONIFY_LOGO,
    SIMPLE_ICON_SLUG,
)
# Reuse the download/render helpers from the full-pack builder so the logo
# sourcing cascade stays identical (iconify → simpleicons → favicon).
from scripts.setup_brand_logos import (  # noqa: E402
    ASSETS_DIR,
    CURATED_DIR,
    OUT_PATH,
    _bot_username,
    _favicon_png,
    _fit_png,
    _on_black,
    _set_name,
    _try_iconify,
    _try_simpleicons,
)


async def _download_one(brand: str) -> Path:
    """Mirror the per-brand cascade in setup_brand_logos.download_logos for a
    single brand. Returns the saved 100×100 PNG path."""
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    dest = ASSETS_DIR / f"{brand}.png"

    # A hand-placed assets/brand-logos-curated/{brand}.png wins over the whole
    # download cascade — same rule as setup_brand_logos.download_logos — so an
    # exact, operator-supplied logo is used verbatim. (consistency)
    curated = CURATED_DIR / f"{brand}.png"
    if curated.is_file():
        dest.write_bytes(_fit_png(curated.read_bytes()))
        print(f"  ✓ {brand} (curated)")
        return dest

    async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
        direct_url = BRAND_ICON_URL.get(brand)
        if direct_url:
            r = await client.get(direct_url)
            if r.status_code == 200 and len(r.content) > 50:
                png = r.content
                if brand == "grok":
                    png = _on_black(png)
                dest.write_bytes(_fit_png(png))
                print(f"  ✓ {brand} (url)")
                return dest

        iconify = ICONIFY_LOGO.get(brand)
        if iconify:
            png = await _try_iconify(client, iconify)
            if png:
                dest.write_bytes(_fit_png(png))
                print(f"  ✓ {brand} (iconify:{iconify})")
                return dest

        slug = SIMPLE_ICON_SLUG.get(brand)
        if slug:
            png = await _try_simpleicons(client, slug)
            if png:
                dest.write_bytes(_fit_png(png))
                print(f"  ✓ {brand} (simpleicons:{slug})")
                return dest

    domain = FAVICON_DOMAIN.get(brand, f"{brand}.com")
    dest.write_bytes(_favicon_png(domain))
    print(f"  ✓ {brand} (favicon:{domain})")
    return dest


def _add_to_set(settings, brand: str, png_path: Path, user_id: int,
                replace: bool = False) -> str:
    token = settings.BOT_TOKEN
    base = f"https://api.telegram.org/bot{token}"
    set_name = _set_name(settings)

    with httpx.Client(timeout=120.0) as client:
        gs = client.get(f"{base}/getStickerSet", params={"name": set_name})
        if not gs.json().get("ok"):
            raise RuntimeError(
                f"Sticker set {set_name} not found — run setup_brand_logos.py "
                f"once to create it before adding incrementally."
            )
        before = gs.json()["result"]["stickers"]

        existing = [
            st for st in before
            if brand in (st.get("keywords") or []) and st.get("file_id")
        ]
        if existing and not replace:
            # Idempotent add: brand already present, reuse its id.
            cid = existing[0].get("custom_emoji_id")
            if cid:
                print(f"  · {brand} already in set, reusing id")
                return str(cid)
        if existing and replace:
            # Swap the artwork: delete the old sticker(s) for this brand so the
            # new image takes its place. The new sticker gets a fresh
            # custom_emoji_id (the old one is retired), which we merge into the
            # JSON below — old messages with the previous logo lose it, which
            # is the intended outcome of a logo change.
            for st in existing:
                d = client.post(f"{base}/deleteStickerFromSet",
                                data={"sticker": st["file_id"]})
                if d.json().get("ok"):
                    print(f"  ✓ removed old {brand} sticker")
            gs = client.get(f"{base}/getStickerSet", params={"name": set_name})
            before = gs.json()["result"]["stickers"]

        with png_path.open("rb") as f:
            up = client.post(
                f"{base}/uploadStickerFile",
                data={"user_id": str(user_id), "sticker_format": "static"},
                files={"sticker": (f"{brand}.png", f, "image/png")},
            )
        body = up.json()
        if not body.get("ok"):
            raise RuntimeError(f"uploadStickerFile {brand}: {body}")
        file_id = body["result"]["file_id"]

        add = client.post(
            f"{base}/addStickerToSet",
            data={
                "user_id": str(user_id),
                "name": set_name,
                "sticker": json.dumps({
                    "sticker": file_id,
                    "format": "static",
                    "emoji_list": ["🏷"],
                    "keywords": [brand, "product", "brand", "akunding"],
                }),
            },
        )
        body = add.json()
        if not body.get("ok"):
            raise RuntimeError(f"addStickerToSet {brand}: {body}")

        gs = client.get(f"{base}/getStickerSet", params={"name": set_name})
        after = gs.json()["result"]["stickers"]

    if len(after) <= len(before):
        raise RuntimeError("set size did not grow after addStickerToSet")
    # The freshly added sticker is appended at the end of the set.
    new_id = str(after[-1].get("custom_emoji_id") or "")
    if not new_id:
        raise RuntimeError("new sticker has no custom_emoji_id yet")
    return new_id


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("brand", help="Brand slug (must exist in keyboards/brands.py)")
    parser.add_argument("--replace", action="store_true",
                        help="Replace the brand's existing logo with a fresh "
                             "download (deletes the old sticker first)")
    args = parser.parse_args()
    brand = args.brand.strip().lower()

    if brand not in BRAND_LABELS:
        print(f"✗ '{brand}' is not in BRAND_LABELS. Add it to keyboards/brands.py first.")
        return 1

    settings = get_settings()
    if not settings.BOT_TOKEN or not settings.ADMIN_IDS:
        print("✗ BOT_TOKEN and ADMIN_IDS required")
        return 1
    owner_id = int(settings.ADMIN_IDS[0])

    print(f"=== Download {BRAND_LABELS[brand]} logo ===")
    png_path = asyncio.run(_download_one(brand))

    print(f"\n=== Add to pack {_set_name(settings)} ===")
    new_id = _add_to_set(settings, brand, png_path, owner_id, replace=args.replace)
    print(f"  ✓ {brand} → {new_id}")

    ids: dict[str, str] = {}
    if OUT_PATH.exists():
        ids = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    ids[brand] = new_id
    OUT_PATH.write_text(json.dumps(ids, indent=2), encoding="utf-8")
    print(f"\n✓ Merged into {OUT_PATH} ({len(ids)} brands total)")
    print(f"✓ Preview asset at {png_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
