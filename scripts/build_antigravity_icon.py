#!/usr/bin/env python3
"""
Build Antigravity shop icon to match reference: large gradient arch on near-black
rounded square (same visual weight as Meshy/Warp Telegram stickers).
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "brand-logos-curated" / "antigravity.png"
SIZE = 512
# Tight inset — reference arch nearly fills the rounded square.
INSET = 36
CORNER = 104
PLATE = (12, 13, 17, 255)
MARK_URL = "https://www.google.com/s2/favicons?domain=antigravity.google&sz=128"


def _strip_near_black(img) -> None:
    px = img.load()
    w, h = img.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a < 8:
                continue
            if r < 50 and g < 50 and b < 50:
                px[x, y] = (0, 0, 0, 0)


def build(mark_src: bytes | None = None) -> Path:
    from PIL import Image, ImageDraw, ImageEnhance

    if mark_src is None:
        r = httpx.get(MARK_URL, timeout=30.0, follow_redirects=True)
        r.raise_for_status()
        mark_src = r.content

    mark = Image.open(io.BytesIO(mark_src)).convert("RGBA")
    _strip_near_black(mark)
    # Slightly punchier at emoji size.
    mark = ImageEnhance.Color(mark).enhance(1.12)
    mark = ImageEnhance.Contrast(mark).enhance(1.08)

    inner = SIZE - 2 * INSET
    mark = mark.resize((inner, inner), Image.Resampling.LANCZOS)

    plate = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(plate)
    draw.rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), radius=CORNER, fill=PLATE)
    ox = (SIZE - mark.width) // 2
    oy = (SIZE - mark.height) // 2
    plate.paste(mark, (ox, oy), mark)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    plate.save(OUT, format="PNG", optimize=True)
    return OUT


def main() -> int:
    src_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    data = src_path.read_bytes() if src_path and src_path.is_file() else None
    path = build(data)
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
