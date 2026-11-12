"""
Helpers for resolving and snapshotting third-party Telegram custom-emoji packs.

Bots may include any public custom_emoji_id in their messages, so as long as
we know the IDs we can render the pack's emojis in our flows. To populate
those IDs from a pack we don't own, we use two raw HTTP calls:

  1. getCustomEmojiStickers([id]) → returns the sticker info, including
     `set_name` (the URL slug of the owning pack).
  2. getStickerSet(name) → returns every sticker in that pack with its
     `custom_emoji_id`.

Result is written to keyboards/mix_emoji_ids.json and consumed by
keyboards/mix_icons.py.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "keyboards" / "mix_emoji_ids.json"


def _bot_token(bot) -> str:
    # aiogram 3 Bot exposes `.token` directly. Module-level so the function
    # below stays a single httpx call instead of pulling in the full aiogram
    # request stack.
    return getattr(bot, "token", "")


def _structured_ids(set_payload: dict[str, Any]) -> dict[str, Any]:
    """Group sticker IDs by their unicode emoji character. Some packs ship
    many variants under the same emoji (e.g. multiple stars in different
    colors); keep them all and let the caller pick by index."""
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


async def grab_pack_by_custom_emoji_id(
    *, bot, custom_emoji_id: str,
) -> dict[str, Any] | None:
    """Given a single custom_emoji_id from any pack, resolve the owning set
    and snapshot every emoji ID to disk. Returns the structured payload (or
    None if the lookup failed).

    The bot account doesn't need to own or have added the pack — Telegram's
    getCustomEmojiStickers endpoint works for any public custom emoji."""
    token = _bot_token(bot)
    if not token:
        log.warning("grab_pack: bot has no token attribute")
        return None

    base = f"https://api.telegram.org/bot{token}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            f"{base}/getCustomEmojiStickers",
            json={"custom_emoji_ids": [str(custom_emoji_id)]},
        )
        body = r.json()
        if not body.get("ok") or not body.get("result"):
            log.info("grab_pack: getCustomEmojiStickers miss for %s: %s",
                     custom_emoji_id, body)
            return None
        set_name = (body["result"][0].get("set_name") or "").strip()
        if not set_name:
            log.info("grab_pack: no set_name in sticker payload: %s", body)
            return None

        r = await client.get(f"{base}/getStickerSet", params={"name": set_name})
        body = r.json()
        if not body.get("ok"):
            log.info("grab_pack: getStickerSet(%s) failed: %s", set_name, body)
            return None

        data = _structured_ids(body["result"])

    OUT_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    log.info("grab_pack: saved %s (%s) — %d emojis",
             data.get("title"), data.get("name"), data.get("count"))

    # Reset any in-memory cache so the next render uses fresh IDs.
    try:
        from keyboards import mix_icons
        mix_icons.reload()
    except Exception:
        pass
    return data
