"""
Lookup helpers for the third-party "mix" custom-emoji pack (populated via
/grabemoji or scripts/setup_mix_emojis.py).

The pack is OPTIONAL: every helper here degrades gracefully to plain unicode
when the JSON snapshot is missing or doesn't contain the requested emoji.
That means message templates can sprinkle calls to `tge("⭐")` etc. without
having to guard on pack availability — Telegram Premium recipients see the
colored custom emoji, everyone else sees the unicode fallback.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_IDS_PATH = Path(__file__).parent / "mix_emoji_ids.json"
_cached: dict[str, Any] | None = None

# This ID is tagged as 🛍 in the imported News Emoji pack but renders as a
# percent-style symbol in Telegram. It must never be used in buyer-facing UI.
BLOCKED_CUSTOM_EMOJI_IDS = {"5229064374403998351"}


def _load() -> dict[str, Any]:
    """Read + cache mix_emoji_ids.json. Safe to call before the file exists —
    returns an empty dict so every lookup falls back to the unicode glyph."""
    global _cached
    if _cached is not None:
        return _cached
    if not _IDS_PATH.exists():
        _cached = {}
        return _cached
    try:
        _cached = json.loads(_IDS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("mix_icons: failed to load %s: %s", _IDS_PATH, e)
        _cached = {}
    return _cached


def reload() -> None:
    """Drop the in-memory cache. Called from services.emoji_pack after a
    fresh /grabemoji run so the new IDs take effect without a bot restart."""
    global _cached
    _cached = None


def pack_available() -> bool:
    return bool(_load().get("flat"))


def pack_info() -> tuple[str, str, int]:
    data = _load()
    return (
        str(data.get("title") or ""),
        str(data.get("name") or ""),
        int(data.get("count") or 0),
    )


def get(char: str, index: int = 0) -> str | None:
    """Return the Nth custom_emoji_id whose emoji field is exactly `char`.
    Many packs ship multiple stylistic variants under the same unicode
    emoji (different-color stars, hearts, etc.); index selects between
    them deterministically. Returns None when the pack doesn't carry that
    emoji or hasn't been loaded yet."""
    by_emoji = _load().get("by_emoji") or {}
    ids = [
        str(cid)
        for cid in (by_emoji.get(char) or [])
        if str(cid) not in BLOCKED_CUSTOM_EMOJI_IDS
    ]
    if not ids:
        return None
    if index >= len(ids):
        index = index % len(ids)
    return str(ids[index])


def tge(char: str, *, index: int = 0, fallback: str | None = None) -> str:
    """Return an HTML <tg-emoji> tag for `char` when the pack has it, else
    the bare unicode glyph (or a custom fallback string). Recipients without
    Telegram Premium see the fallback as well — Telegram renders the tag's
    inner text whenever the custom emoji can't be displayed."""
    inner = fallback if fallback is not None else char
    cid = get(char, index=index)
    if not cid:
        return inner
    return f'<tg-emoji emoji-id="{cid}">{inner}</tg-emoji>'


def first_id_of(chars: list[str], *, index: int = 0) -> str | None:
    """Walk `chars` in order and return the first custom_emoji_id we have a
    snapshot for. Useful when several emojis would all be acceptable (e.g.
    pink-star OR yellow-star — whichever the pack happens to include)."""
    for ch in chars:
        cid = get(ch, index=index)
        if cid:
            return cid
    return None
