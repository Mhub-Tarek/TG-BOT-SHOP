#!/usr/bin/env python3
"""Build clean SAFWAN-style Telegram hype message."""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from keyboards import hype_emoji as he  # noqa: E402
from keyboards.brands import guess_brand_from_name  # noqa: E402

OUT = ROOT / "assets" / "hype"

_DOUBLE = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "𝔸𝔹ℂ𝔻𝔼𝔽𝔾ℍ𝕀𝕁𝕂𝕃𝕄ℕ𝕆ℙℚℝ𝕊𝕋𝕌𝕍𝕎𝕏𝕐ℤ",
)


def double_struck(s: str) -> str:
    return s.upper().translate(_DOUBLE)


def hype_name(name: str) -> str:
    s = name.strip()
    s = re.sub(r"\b(\d+)\s*Months?\b", r"\1_M", s, flags=re.I)
    s = re.sub(r"\b(\d+)\s*Month\b", r"\1_M", s, flags=re.I)
    s = re.sub(r"\b(\d+)\s*m\b", r"\1_M", s, flags=re.I)
    s = re.sub(r"\b(\d+)\s*Year\b", r"\1_Y", s, flags=re.I)
    s = re.sub(r"\b(\d+)\s*Years?\b", r"\1_Y", s, flags=re.I)
    s = re.sub(r"\b(\d+)\s*D\b", r"\1_D", s, flags=re.I)
    return s.replace("  ", " ")


def war_label(days: int) -> tuple[str, bool]:
    if days <= 0:
        return "No - war", False
    if days >= 365:
        return "full war", True
    return f"{days}D - war", True


def load_products(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT name, emoji, base_price, warranty_days FROM products WHERE active=1 "
        "ORDER BY base_price"
    ).fetchall()
    conn.close()
    out = []
    for i, r in enumerate(rows, 1):
        war, strike = war_label(int(r["warranty_days"] or 0))
        out.append({
            "num": i,
            "line": hype_name(r["name"]),
            "war": war,
            "strike_war": strike,
            "price": f"{float(r['base_price']):g}$",
            "glyph": (r["emoji"] or "📦").strip() or "📦",
            "slug": guess_brand_from_name(r["name"]),
        })
    return out


def build_header() -> list[str]:
    """Short header — max 2 animated stickers so lines don't break on mobile."""
    fire = he.header_fire()
    devil = he.header_devil()
    return [
        f"<b>AKUNDING TIGER™</b> {devil}",
        "",
        f"<b>🐍 {double_struck('AKUNDING - SHOP')} 🐍</b>",
        "",
        f"<b>➵ {double_struck('BOT ORDER')}······</b>",
        "",
        "━━━━━━━━━━━━━━━━",
        "",
    ]


def build_hype_html(products: list[dict]) -> str:
    fire = he.header_fire()
    lines = build_header()

    for p in products:
        n = f"{p['num']}️⃣ " if p["num"] <= 10 else f"{p['num']}. "
        war = f"<s>{p['war']}</s>" if p["strike_war"] else p["war"]
        # Unicode glyph only — brand custom emoji are too wide and wrap lines
        lines.append(
            f"{n}{p['glyph']} {p['line']} - {war} - <b>{p['price']}</b> {fire}<b>HOT</b>"
        )
        lines.append("")

    lines += [
        "━━━━━━━━━━━━━━━━",
        "",
        "⚡ <b>INSTANT</b> · USDT · Wallet · Binance Pay",
        "",
        '👉 <a href="https://t.me/Akunding_store_bot?start=shop">'
        f"{double_struck('OPEN BOT')} — @Akunding_store_bot</a>",
    ]
    return "\n".join(lines)
