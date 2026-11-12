#!/usr/bin/env python3
"""
Download full-color brand logos and create a Telegram custom-emoji pack.

Philosophy: icon-only — we want the recognisable glyph (Discord head, Gemini
star, YouTube play button) without wordmarks or iOS-style rounded-square
frames. Iconify's "logos" collection ships both forms for most brands; the
`-icon` suffix is the glyph-only variant. We try that first per source, then
fall back to the default entry, then to monochrome simple-icons, then favicon.

Priority per brand:
  1. Iconify "logos:{name}-icon" (glyph-only colored SVG) — preferred
  2. Iconify "logos:{name}" (often wordmark, kept as graceful fallback)
  3. cdn.simpleicons.org (monochrome glyph-only SVG)
  4. Site favicon (last resort)

Run on VPS:
  cd /opt/telegram-shop && .venv/bin/python scripts/setup_brand_logos.py
"""
from __future__ import annotations

import io
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

OUT_PATH = ROOT / "keyboards" / "brand_logo_ids.json"
ASSETS_DIR = ROOT / "assets" / "brand-logos"
# Hand-picked logos that have no clean icon-CDN source (e.g. Adobe Creative
# Cloud's rainbow mark, Wispr Flow). A `{brand}.png` here wins over the whole
# download chain, so the result is deterministic and never silently degrades
# to a wrong fallback when a third-party host blocks us or changes its asset.
CURATED_DIR = ROOT / "assets" / "brand-logos-curated"


def _bot_username(settings) -> str:
    return (settings.BOT_USERNAME or "Akunding_store_bot").strip().lstrip("@")


def _set_name(settings) -> str:
    return f"AkundingBrandLogos_by_{_bot_username(settings)}"


def _render_svg_to_png(svg_bytes: bytes, size: int = 100) -> bytes:
    """Render SVG to PNG preserving original fills / gradients."""
    try:
        import cairosvg
    except ImportError as e:
        raise RuntimeError("Install cairosvg: pip install cairosvg") from e

    return cairosvg.svg2png(bytestring=svg_bytes, output_width=size, output_height=size)


def _on_black(png_bytes: bytes, size: int = 100) -> bytes:
    """Composite an alpha-keyed logo onto an opaque black background.

    Used for brands whose primary logo is white-on-transparent (Grok). The
    earlier `_invert_on_black` helper assumed black-on-transparent input and
    inverted it; lobehub's CDN flipped polarity to white-on-transparent at
    some point, so inversion now produces black-on-black. Compositing without
    inversion is correct for the current source and stable against future
    polarity flips of dark/light variants."""
    from PIL import Image

    img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    if img.size != (size, size):
        img = img.resize((size, size), Image.Resampling.LANCZOS)
    bg = Image.new("RGBA", (size, size), (0, 0, 0, 255))
    bg.paste(img, (0, 0), img)
    buf = io.BytesIO()
    bg.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _fit_png(png_bytes: bytes, size: int = 100) -> bytes:
    from PIL import Image

    img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    if img.size == (size, size):
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    img = img.resize((size, size), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _favicon_png(domain: str, size: int = 100) -> bytes:
    from PIL import Image

    urls = [
        f"https://{domain}/apple-touch-icon.png",
        f"https://{domain}/favicon.ico",
        f"https://www.{domain}/apple-touch-icon.png",
        f"https://www.google.com/s2/favicons?domain={domain}&sz=128",
    ]
    last_err: Exception | None = None
    for url in urls:
        try:
            r = httpx.get(url, timeout=30.0, follow_redirects=True)
            if r.status_code == 200 and len(r.content) > 50:
                img = Image.open(io.BytesIO(r.content)).convert("RGBA")
                img = img.resize((size, size), Image.Resampling.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="PNG", optimize=True)
                return buf.getvalue()
        except Exception as exc:
            last_err = exc
    raise RuntimeError(f"no favicon for {domain}") from last_err


async def _try_iconify(client: httpx.AsyncClient, icon: str) -> bytes | None:
    """Try `{icon}-icon` first (glyph-only), then `{icon}` (often includes
    wordmark). Iconify returns plain 404 for non-existent slugs, which the
    response check catches; brands without a `-icon` variant degrade to the
    default entry automatically."""
    for candidate in (f"{icon}-icon", icon):
        url = f"https://api.iconify.design/logos/{candidate}.svg"
        r = await client.get(url)
        if r.status_code == 200 and b"<svg" in r.content[:300]:
            return _render_svg_to_png(r.content)
    return None


async def _try_simpleicons(client: httpx.AsyncClient, slug: str) -> bytes | None:
    url = f"https://cdn.simpleicons.org/{slug}"
    r = await client.get(url)
    if r.status_code != 200 or b"<svg" not in r.content[:300]:
        return None
    return _render_svg_to_png(r.content)


async def download_logos() -> dict[str, Path]:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
        for brand in BRAND_LABELS:
            dest = ASSETS_DIR / f"{brand}.png"
            ok = False

            curated = CURATED_DIR / f"{brand}.png"
            if curated.is_file():
                try:
                    dest.write_bytes(_fit_png(curated.read_bytes()))
                    print(f"  ✓ {brand} (curated)")
                    ok = True
                except Exception as exc:
                    print(f"  · {brand} curated failed: {exc}")

            direct_url = BRAND_ICON_URL.get(brand)
            if not ok and direct_url:
                try:
                    r = await client.get(direct_url)
                    if r.status_code == 200 and len(r.content) > 50:
                        png = r.content
                        if brand == "grok":
                            png = _on_black(png)
                        dest.write_bytes(_fit_png(png))
                        print(f"  ✓ {brand} (url)")
                        ok = True
                except Exception as exc:
                    print(f"  · {brand} url failed: {exc}")

            iconify = ICONIFY_LOGO.get(brand)
            if not ok and iconify:
                try:
                    png = await _try_iconify(client, iconify)
                    if png:
                        dest.write_bytes(_fit_png(png))
                        print(f"  ✓ {brand} (iconify:{iconify})")
                        ok = True
                except Exception as exc:
                    print(f"  · {brand} iconify failed: {exc}")

            if not ok:
                slug = SIMPLE_ICON_SLUG.get(brand)
                if slug:
                    try:
                        png = await _try_simpleicons(client, slug)
                        if png:
                            dest.write_bytes(_fit_png(png))
                            print(f"  ✓ {brand} (simpleicons:{slug})")
                            ok = True
                    except Exception as exc:
                        print(f"  · {brand} simpleicons failed: {exc}")

            if not ok:
                domain = FAVICON_DOMAIN.get(brand, f"{brand}.com")
                try:
                    dest.write_bytes(_favicon_png(domain))
                    print(f"  ✓ {brand} (favicon:{domain})")
                    ok = True
                except Exception as exc:
                    print(f"  ✗ {brand} failed: {exc}")

            if ok:
                paths[brand] = dest

    return paths


def create_emoji_pack(settings, paths: dict[str, Path], user_id: int) -> dict[str, str]:
    token = settings.BOT_TOKEN
    base = f"https://api.telegram.org/bot{token}"
    set_name = _set_name(settings)
    order = [k for k in BRAND_LABELS if k in paths]

    with httpx.Client(timeout=180.0) as client:
        gs = client.get(f"{base}/getStickerSet", params={"name": set_name})
        if gs.json().get("ok"):
            client.post(f"{base}/deleteStickerSet", data={"name": set_name})
            print(f"  ✓ deleted old set {set_name}")

        uploaded = []
        for brand in order:
            path = paths[brand]
            with path.open("rb") as f:
                up = client.post(
                    f"{base}/uploadStickerFile",
                    data={"user_id": str(user_id), "sticker_format": "static"},
                    files={"sticker": (f"{brand}.png", f, "image/png")},
                )
            body = up.json()
            if not body.get("ok"):
                raise RuntimeError(f"upload {brand}: {body}")
            uploaded.append({
                "sticker": body["result"]["file_id"],
                "format": "static",
                "emoji_list": ["🏷"],
                "keywords": [brand, "product", "brand", "akunding"],
            })

        create = client.post(
            f"{base}/createNewStickerSet",
            data={
                "user_id": str(user_id),
                "name": set_name,
                "title": "Akunding Brand Logos",
                "stickers": json.dumps(uploaded),
                "sticker_type": "custom_emoji",
                "needs_repainting": "false",
            },
        )
        if not create.json().get("ok"):
            raise RuntimeError(create.json())

        gs = client.get(f"{base}/getStickerSet", params={"name": set_name})
        stickers = gs.json()["result"]["stickers"]

    ids: dict[str, str] = {}
    for i, brand in enumerate(order):
        if i < len(stickers) and stickers[i].get("custom_emoji_id"):
            ids[brand] = str(stickers[i]["custom_emoji_id"])
            print(f"  ✓ {brand} → {ids[brand]}")
    return ids


def main() -> int:
    settings = get_settings()
    if not settings.BOT_TOKEN or not settings.ADMIN_IDS:
        print("✗ BOT_TOKEN and ADMIN_IDS required")
        return 1

    owner_id = int(settings.ADMIN_IDS[0])
    print("=== Download color brand logos ===")
    paths = __import__("asyncio").run(download_logos())
    if not paths:
        print("✗ No logos downloaded")
        return 1

    print(f"\n=== Create emoji pack ({len(paths)} brands) ===")
    ids = create_emoji_pack(settings, paths, owner_id)
    if not ids:
        print("✗ No emoji IDs returned")
        return 1

    OUT_PATH.write_text(json.dumps(ids, indent=2), encoding="utf-8")
    print(f"\n✓ Saved {OUT_PATH} ({len(ids)} brands)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
