#!/usr/bin/env python3
"""
Create a Telegram custom-emoji pack with real payment brand icons and save IDs.

Requires:
  - BOT_TOKEN and ADMIN_IDS in .env
  - Bot owner Telegram Premium (Bot API 9.4+) OR Fragment username upgrade

Run on VPS:
  cd /opt/telegram-shop && .venv/bin/python scripts/setup_payment_emojis.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import get_settings  # noqa: E402

OUT_PATH = ROOT / "keyboards" / "payment_emoji_ids.json"
ASSETS_DIR = ROOT / "assets" / "payment-icons"

# Open-source brand / network icons (PNG, 128px)
ICON_URLS: dict[str, str] = {
    "wallet": "https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/72x72/1f45b.png",
    "binance": "https://cdn.jsdelivr.net/npm/cryptocurrency-icons@0.18.1/128/color/bnb.png",
    "usdt_bep20": "https://cdn.jsdelivr.net/npm/cryptocurrency-icons@0.18.1/128/color/bnb.png",
    "usdt_trc20": "https://cdn.jsdelivr.net/npm/cryptocurrency-icons@0.18.1/128/color/usdt.png",
}

EMOJI_LIST: dict[str, list[str]] = {
    "wallet": ["👛"],
    "binance": ["🟡"],
    "usdt_bep20": ["🟡"],
    "usdt_trc20": ["🟢"],
}


def _bot_username(settings) -> str:
    return (settings.BOT_USERNAME or "Akunding_store_bot").strip().lstrip("@")


def _set_name(settings) -> str:
    bot = _bot_username(settings)
    return f"AkundingPayColor_by_{bot}"


def _delete_set(client: httpx.Client, base: str, set_name: str) -> None:
    r = client.post(f"{base}/deleteStickerSet", data={"name": set_name})
    if r.json().get("ok"):
        print(f"  ✓ deleted old set {set_name}")


def _prepare_icon(src: Path, dest: Path, size: int = 100) -> Path:
    """Resize to square PNG for Telegram custom emoji (100×100)."""
    try:
        from PIL import Image
    except ImportError as e:
        raise RuntimeError("Install Pillow: pip install Pillow") from e
    img = Image.open(src).convert("RGBA")
    img = img.resize((size, size), Image.Resampling.LANCZOS)
    img.save(dest, format="PNG", optimize=True)
    return dest


async def download_icons() -> dict[str, Path]:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    raw_dir = ASSETS_DIR / "raw"
    raw_dir.mkdir(exist_ok=True)
    paths: dict[str, Path] = {}
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for key, url in ICON_URLS.items():
            raw = raw_dir / f"{key}.png"
            dest = ASSETS_DIR / f"{key}.png"
            if not raw.exists():
                r = await client.get(url)
                r.raise_for_status()
                raw.write_bytes(r.content)
            _prepare_icon(raw, dest)
            paths[key] = dest
            print(f"  ✓ {key} → {dest.name} ({dest.stat().st_size} bytes, 100×100)")
    return paths


def create_emoji_pack(settings, paths: dict[str, Path], user_id: int) -> dict[str, str]:
    """Upload stickers and create custom_emoji set; return {key: custom_emoji_id}."""
    token = settings.BOT_TOKEN
    base = f"https://api.telegram.org/bot{token}"
    set_name = _set_name(settings)
    legacy_name = f"AkundingPayIcons_by_{_bot_username(settings)}"

    with httpx.Client(timeout=120.0) as client:
        for old in (legacy_name, set_name):
            gs = client.get(f"{base}/getStickerSet", params={"name": old})
            if gs.json().get("ok"):
                _delete_set(client, base, old)

        gs = client.get(f"{base}/getStickerSet", params={"name": set_name})
        if gs.json().get("ok"):
            stickers = gs.json()["result"]["stickers"]
            if not any(s.get("needs_repainting") for s in stickers):
                print(f"  · color set already exists: {set_name}")
                return _ids_from_sticker_set(stickers)
            _delete_set(client, base, set_name)

        uploaded: list[dict] = []
        for key in ("wallet", "binance", "usdt_bep20", "usdt_trc20"):
            path = paths[key]
            with path.open("rb") as f:
                up = client.post(
                    f"{base}/uploadStickerFile",
                    data={"user_id": str(user_id), "sticker_format": "static"},
                    files={"sticker": (path.name, f, "image/png")},
                )
            up.raise_for_status()
            body = up.json()
            if not body.get("ok"):
                raise RuntimeError(f"uploadStickerFile {key}: {body}")
            file_id = body["result"]["file_id"]
            uploaded.append({
                "sticker": file_id,
                "format": "static",
                "emoji_list": EMOJI_LIST[key],
                "keywords": [key, "payment", "akunding"],
            })
            print(f"  ✓ uploaded {key}")

        stickers_json = json.dumps([
            {"sticker": u["sticker"], "format": u["format"],
             "emoji_list": u["emoji_list"], "keywords": u["keywords"]}
            for u in uploaded
        ])

        create = client.post(
            f"{base}/createNewStickerSet",
            data={
                "user_id": str(user_id),
                "name": set_name,
                "title": "Akunding Pay Icons",
                "stickers": stickers_json,
                "sticker_type": "custom_emoji",
                "needs_repainting": "false",
            },
        )
        body = create.json()
        if not body.get("ok"):
            raise RuntimeError(f"createNewStickerSet failed: {body}")

        print(f"  ✓ created set {set_name}")

        gs = client.get(f"{base}/getStickerSet", params={"name": set_name})
        gs.raise_for_status()
        return _ids_from_sticker_set(gs.json()["result"]["stickers"])


def _ids_from_sticker_set(stickers: list) -> dict[str, str]:
    order = ["wallet", "binance", "usdt_bep20", "usdt_trc20"]
    ids: dict[str, str] = {}
    for i, key in enumerate(order):
        if i < len(stickers) and stickers[i].get("custom_emoji_id"):
            ids[key] = str(stickers[i]["custom_emoji_id"])
            print(f"  ✓ {key} emoji_id={ids[key]}")
    return ids


def main() -> int:
    settings = get_settings()
    if not settings.BOT_TOKEN:
        print("✗ BOT_TOKEN missing")
        return 1
    if not settings.ADMIN_IDS:
        print("✗ ADMIN_IDS missing — need owner Telegram user id")
        return 1

    owner_id = int(settings.ADMIN_IDS[0])
    print("=== Download icon assets ===")
    paths = __import__("asyncio").run(download_icons())

    print("\n=== Create Telegram custom emoji pack ===")
    print(f"  Owner user_id: {owner_id}")
    print(f"  Set name: {_set_name(settings)}")
    try:
        ids = create_emoji_pack(settings, paths, owner_id)
    except Exception as e:
        print(f"\n✗ Failed: {e}")
        print("\nIf this failed, ensure:")
        print("  1. Bot owner has Telegram Premium, OR")
        print("  2. Bot has a Fragment upgraded username")
        print("  3. Owner has /start the bot at least once")
        return 1

    if len(ids) < 4:
        print(f"✗ Only got {len(ids)}/4 emoji IDs")
        return 1

    OUT_PATH.write_text(json.dumps(ids, indent=2), encoding="utf-8")
    print(f"\n✓ Saved {OUT_PATH}")
    print(f"  Add link for users: https://t.me/addemoji/{_set_name(settings).replace('_by_' + _bot_username(settings), '')}")
    print("  (Users don't need to add the pack — buttons work via bot)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
