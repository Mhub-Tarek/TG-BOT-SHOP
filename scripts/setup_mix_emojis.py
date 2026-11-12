#!/usr/bin/env python3
"""
Fetch a third-party Telegram custom-emoji pack (e.g. the public "Mix Emoji ✨"
pack by @OrderEmoji) and dump the {emoji_char: [custom_emoji_id, ...]} map.

Bots are allowed to SEND any public custom_emoji_id — there's no permission
gate — so the only thing we need from the pack is its IDs. Once saved, the
bot uses them in HTML message bodies via <tg-emoji emoji-id="..."> and on
inline buttons via icon_custom_emoji_id.

Usage (on VPS):
  cd /opt/telegram-shop
  .venv/bin/python scripts/setup_mix_emojis.py <set_short_name>
  .venv/bin/python scripts/setup_mix_emojis.py --probe   # tries common names

The short_name is the URL slug at the end of t.me/addemoji/<short_name> —
copy it from the "Share emoji set" link inside Telegram.

Writes: keyboards/mix_emoji_ids.json (structured, indexed by emoji char)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import get_settings  # noqa: E402

OUT_PATH = ROOT / "keyboards" / "mix_emoji_ids.json"

# Educated guesses for the @OrderEmoji "Mix Emoji ✨" pack. Telegram pack
# short_names are case-sensitive ASCII slugs; the display title "Mix Emoji ✨"
# rules out most of these but they are cheap to probe.
PROBE_CANDIDATES = (
    "MixEmoji",
    "Mix_Emoji",
    "MixEmojiPack",
    "MixEmojis",
    "OrderEmoji",
    "OrderMixEmoji",
    "OrderEmojiMix",
    "mixemoji",
    "mix_emoji",
    "MixEmojiByOrderEmoji",
    "Mix_Emoji_by_OrderEmoji",
)


def _fetch_set(token: str, short_name: str) -> dict[str, Any] | None:
    """Return the raw getStickerSet payload on success, None on 'not found'."""
    base = f"https://api.telegram.org/bot{token}"
    r = httpx.get(f"{base}/getStickerSet", params={"name": short_name}, timeout=30.0)
    body = r.json()
    if body.get("ok"):
        return body["result"]
    # 400 with "STICKERSET_INVALID" / "Bad Request: STICKERSET_INVALID" = not found
    return None


def _structured_ids(set_payload: dict[str, Any]) -> dict[str, Any]:
    """Group sticker IDs by their unicode emoji character. Some packs ship many
    variants under the same emoji (e.g. multiple 🌟 stars in different colors);
    keep them all and let the caller pick by index."""
    by_emoji: dict[str, list[str]] = {}
    flat: list[dict[str, str]] = []
    for st in set_payload.get("stickers", []):
        cid = str(st.get("custom_emoji_id") or "").strip()
        emoji = (st.get("emoji") or "").strip()
        if not cid:
            continue
        by_emoji.setdefault(emoji or "_unknown", []).append(cid)
        flat.append({"emoji": emoji, "id": cid})
    return {
        "name": set_payload.get("name"),
        "title": set_payload.get("title"),
        "by_emoji": by_emoji,
        "flat": flat,
        "count": len(flat),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("short_name", nargs="?", help="Sticker set short_name (URL slug)")
    parser.add_argument("--probe", action="store_true",
                        help="Try a list of common short_name guesses")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.BOT_TOKEN:
        print("✗ BOT_TOKEN missing in .env")
        return 1

    short_names: list[str]
    if args.probe and not args.short_name:
        short_names = list(PROBE_CANDIDATES)
    elif args.short_name:
        # When user passes a name, also probe a few light variants in case of
        # capitalisation drift — cheap insurance.
        n = args.short_name.strip().lstrip("@").rstrip("/")
        short_names = [n]
        # Always also try the bare lowercase form if different.
        if n.lower() != n:
            short_names.append(n.lower())
    else:
        parser.print_help()
        print("\nExample: setup_mix_emojis.py MixEmoji")
        print("         setup_mix_emojis.py --probe")
        return 2

    print(f"Probing {len(short_names)} candidate short_name(s)...")
    for name in short_names:
        payload = _fetch_set(settings.BOT_TOKEN, name)
        if payload:
            data = _structured_ids(payload)
            print(f"✓ Found '{payload.get('title')}' ({name}) — {data['count']} emojis")
            OUT_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                                encoding="utf-8")
            print(f"✓ Saved {OUT_PATH}")
            # Friendly preview of the first 20 entries
            for item in data["flat"][:20]:
                print(f"  {item['emoji']}  {item['id']}")
            if data["count"] > 20:
                print(f"  ... and {data['count'] - 20} more")
            return 0
        print(f"  · {name} — not found")

    print("\n✗ None of the candidate short_names returned a sticker set.")
    print("  Open the pack in Telegram, tap the share icon, copy the URL,")
    print("  and re-run with: scripts/setup_mix_emojis.py <slug-from-url>")
    return 1


if __name__ == "__main__":
    sys.exit(main())
