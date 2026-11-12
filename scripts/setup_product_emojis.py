#!/usr/bin/env python3
"""Create colored product icon emoji pack (full color, not white)."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import get_settings  # noqa: E402

OUT = ROOT / "keyboards" / "product_emoji_ids.json"
ASSETS = ROOT / "assets" / "product-icons"

# name -> (fill RGB, alt emoji)
ICONS: dict[str, tuple[tuple[int, int, int], str]] = {
    "green": ((52, 211, 153), "🟢"),
    "blue": ((79, 158, 255), "🔵"),
    "purple": ((167, 139, 250), "🟣"),
    "orange": ((251, 146, 60), "🟠"),
    "yellow": ((251, 191, 36), "🟡"),
    "red": ((248, 113, 113), "🔴"),
    "default": ((79, 158, 255), "🔵"),
}


def _png_circle(rgb: tuple[int, int, int], size: int = 100) -> bytes:
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pad = 4
    draw.ellipse([pad, pad, size - pad, size - pad], fill=(*rgb, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _png_out_of_stock(size: int = 100) -> bytes:
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pad = 4
    draw.ellipse([pad, pad, size - pad, size - pad], fill=(248, 113, 113, 255))
    w = 8
    m = size // 4
    draw.line([m, m, size - m, size - m], fill=(255, 255, 255, 255), width=w)
    draw.line([size - m, m, m, size - m], fill=(255, 255, 255, 255), width=w)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _bot_username(settings) -> str:
    return (settings.BOT_USERNAME or "Akunding_store_bot").strip().lstrip("@")


def _set_name(settings) -> str:
    return f"AkundingProductIcons_by_{_bot_username(settings)}"


def main() -> int:
    settings = get_settings()
    if not settings.BOT_TOKEN or not settings.ADMIN_IDS:
        print("✗ BOT_TOKEN and ADMIN_IDS required")
        return 1

    ASSETS.mkdir(parents=True, exist_ok=True)
    owner_id = int(settings.ADMIN_IDS[0])
    token = settings.BOT_TOKEN
    base = f"https://api.telegram.org/bot{token}"
    set_name = _set_name(settings)
    legacy = f"AkundingProductIcons_by_{_bot_username(settings)}"

    pngs: dict[str, bytes] = {}
    for name, (rgb, _) in ICONS.items():
        pngs[name] = _png_circle(rgb)
    pngs["out_of_stock"] = _png_out_of_stock()

    order = ["green", "blue", "purple", "orange", "yellow", "red", "default", "out_of_stock"]

    with httpx.Client(timeout=120.0) as client:
        for old in (legacy, set_name):
            gs = client.get(f"{base}/getStickerSet", params={"name": old})
            if gs.json().get("ok"):
                client.post(f"{base}/deleteStickerSet", data={"name": old})
                print(f"  ✓ deleted old set {old}")

        uploaded = []
        for name in order:
            path = ASSETS / f"{name}.png"
            path.write_bytes(pngs[name])
            with path.open("rb") as f:
                up = client.post(
                    f"{base}/uploadStickerFile",
                    data={"user_id": str(owner_id), "sticker_format": "static"},
                    files={"sticker": (f"{name}.png", f, "image/png")},
                )
            body = up.json()
            if not body.get("ok"):
                raise RuntimeError(f"upload {name}: {body}")
            alt = ICONS.get(name, ((0, 0, 0), "❌"))[1]
            if name == "out_of_stock":
                alt = "❌"
            uploaded.append({
                "sticker": body["result"]["file_id"],
                "format": "static",
                "emoji_list": [alt],
                "keywords": [name, "product", "akunding"],
            })

        create = client.post(
            f"{base}/createNewStickerSet",
            data={
                "user_id": str(owner_id),
                "name": set_name,
                "title": "Akunding Product Icons",
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
    for i, name in enumerate(order):
        if i < len(stickers) and stickers[i].get("custom_emoji_id"):
            ids[name] = str(stickers[i]["custom_emoji_id"])
            print(f"  ✓ {name} → {ids[name]}")

    OUT.write_text(json.dumps(ids, indent=2), encoding="utf-8")
    print(f"\n✓ Saved {OUT}")
    return 0 if len(ids) >= len(order) else 1


if __name__ == "__main__":
    sys.exit(main())
