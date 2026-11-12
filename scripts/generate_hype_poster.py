#!/usr/bin/env python3
"""Generate animated hype price-list GIF + static PNG (real brand logos)."""
from __future__ import annotations

import io
import math
import sqlite3
import sys
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from keyboards.brands import (  # noqa: E402
    BRAND_ICON_URL,
    FAVICON_DOMAIN,
    SIMPLE_ICON_SLUG,
    guess_brand_from_name,
)

OUT_DIR = ROOT / "assets" / "hype"
W, H_BASE = 1080, 200
ROW_H = 78
PAD = 36
BG = (8, 10, 18)
PINK = (255, 80, 180)
CYAN = (80, 200, 255)
GOLD = (255, 190, 60)
HOT_ORANGE = (255, 120, 40)


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = []
    if bold:
        candidates += [
            "C:/Windows/Fonts/arialbd.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ]
    else:
        candidates += [
            "C:/Windows/Fonts/arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
    for path in candidates:
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def icon_url(slug: str) -> str | None:
    if not slug:
        return None
    if slug in BRAND_ICON_URL:
        return BRAND_ICON_URL[slug]
    if slug in FAVICON_DOMAIN:
        d = FAVICON_DOMAIN[slug]
        return f"https://www.google.com/s2/favicons?domain={d}&sz=128"
    si = SIMPLE_ICON_SLUG.get(slug)
    if si:
        return f"https://cdn.jsdelivr.net/npm/simple-icons@v11/icons/{si}.png"
    return None


def fetch_icon(slug: str, size: int = 52) -> Image.Image:
    url = icon_url(slug)
    placeholder = Image.new("RGBA", (size, size), (30, 40, 60, 255))
    if not url:
        return placeholder
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AkundingShop/1.0"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            raw = resp.read()
        img = Image.open(io.BytesIO(raw)).convert("RGBA")
        img = img.resize((size, size), Image.Resampling.LANCZOS)
        # Round mask
        mask = Image.new("L", (size, size), 0)
        md = ImageDraw.Draw(mask)
        md.ellipse((2, 2, size - 2, size - 2), fill=255)
        out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        out.paste(img, (0, 0), mask)
        return out
    except Exception:
        return placeholder


def load_products(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT name, base_price, warranty_days FROM products WHERE active=1 "
        "ORDER BY base_price"
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        name = r["name"]
        slug = guess_brand_from_name(name)
        war = int(r["warranty_days"] or 0)
        if war <= 0:
            war_txt = "No — war"
        elif war >= 365:
            war_txt = "full war"
        else:
            war_txt = f"{war}D — war"
        out.append({
            "name": name,
            "slug": slug,
            "price": float(r["base_price"]),
            "war_txt": war_txt,
            "has_war": war > 0,
        })
    return out


def draw_hot_badge(draw: ImageDraw.ImageDraw, x: int, y: int, pulse: float) -> None:
    scale = 1.0 + 0.12 * math.sin(pulse)
    w, h = int(88 * scale), int(34 * scale)
    x0, y0 = x - w, y - h // 2
    draw.rounded_rectangle((x0, y0, x0 + w, y0 + h), radius=10, fill=HOT_ORANGE)
    f = _font(int(18 * scale), bold=True)
    draw.text((x0 + 10, y0 + 5), "HOT", fill=(20, 10, 0), font=f)
    # flame dots
    for i, dy in enumerate((0, -4, -2)):
        draw.ellipse((x0 + w + 4 + i * 6, y0 + 8 + dy, x0 + w + 12 + i * 6, y0 + 20 + dy),
                     fill=(255, 60 + i * 40, 0))


def gradient_title(width: int, text: str, t: float) -> Image.Image:
    """Animated chrome title strip."""
    f = _font(52, bold=True)
    layer = Image.new("RGBA", (width, 90), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    bbox = d.textbbox((0, 0), text, font=f)
    tw = bbox[2] - bbox[0]
    x = (width - tw) // 2
    phase = t
    color = (
        int(180 + 75 * math.sin(phase)),
        int(90 + 110 * math.sin(phase + 2)),
        int(220 + 35 * math.cos(phase)),
        255,
    )
    d.text((x + 3, 43), text, font=f, fill=(0, 0, 0, 180))
    d.text((x, 40), text, font=f, fill=color)
    return layer


def render_frame(products: list[dict], icons: dict[str, Image.Image], frame_i: int, n_frames: int) -> Image.Image:
    t = frame_i / n_frames * math.pi * 2
    rows = len(products)
    height = H_BASE + rows * ROW_H + 120
    img = Image.new("RGBA", (W, height), BG + (255,))
    draw = ImageDraw.Draw(img)

    # glow header band
    for y in range(0, 180):
        alpha = int(40 * (1 - y / 180))
        draw.line([(0, y), (W, y)], fill=(PINK[0], PINK[1], PINK[2], alpha))

    f_sub = _font(22, bold=True)
    f_bot = _font(20)
    f_row = _font(24)
    f_war = _font(18)
    f_price = _font(26, bold=True)

    draw.text((W // 2, 18), "AKUNDING TIGER™", fill=PINK, font=_font(28, bold=True), anchor="mt")

    title_layer = gradient_title(W, "AKUNDING — SHOP", t)
    img.paste(title_layer, (0, 48), title_layer)

    draw.text((W // 2, 138), "BOT — ORDER ······", fill=CYAN, font=f_bot, anchor="mt")

    # divider
    y0 = H_BASE - 20
    for i in range(0, W - 2 * PAD, 24):
        draw.text((PAD + i, y0), "◆", fill=(60, 70, 90))

    y = H_BASE
    for idx, p in enumerate(products):
        icon = icons.get(p["slug"]) or icons.get("")
        if icon:
            img.paste(icon, (PAD, y + 10), icon)

        x_text = PAD + 64
        draw.text((x_text, y + 8), p["name"][:42], fill=(240, 245, 255), font=f_row)

        war = p["war_txt"]
        if p["has_war"]:
            draw.text((x_text, y + 38), war, fill=(130, 140, 160), font=f_war)
            # strikethrough
            wb = draw.textbbox((x_text, y + 38), war, font=f_war)
            mid = (wb[1] + wb[3]) // 2
            draw.line([(wb[0], mid), (wb[2], mid)], fill=(200, 80, 100), width=2)
        else:
            draw.text((x_text, y + 38), war, fill=(100, 180, 140), font=f_war)

        price = f"{p['price']:.1f}$".replace(".0$", "$")
        draw.text((W - PAD - 120, y + 22), price, fill=GOLD, font=f_price, anchor="rm")
        draw_hot_badge(draw, W - PAD - 8, y + ROW_H // 2, t + idx * 0.4)

        y += ROW_H

    # footer pulse bar
    bar_w = int(W * (0.55 + 0.1 * math.sin(t)))
    draw.rounded_rectangle((PAD, y + 16, PAD + bar_w, y + 52), radius=12, fill=(40, 180, 120))
    draw.text((PAD + 16, y + 22), "OPEN @Akunding_store_bot", fill=(5, 20, 12), font=_font(22, bold=True))
    draw.text((W // 2, y + 68), "akunding.shop  ·  instant delivery  ·  USDT", fill=(120, 130, 150),
              font=f_bot, anchor="mt")

    return img


def main() -> None:
    db = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "shop.db"
    if not db.is_file():
        db = Path("/opt/telegram-shop/shop.db")
    products = load_products(db)
    slugs = {p["slug"] for p in products}
    slugs.add("")
    icons = {s: fetch_icon(s) for s in slugs}

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    n_frames = 24
    frames: list[Image.Image] = []
    for i in range(n_frames):
        fr = render_frame(products, icons, i, n_frames)
        frames.append(fr.convert("P", palette=Image.ADAPTIVE, colors=256))

    png_path = OUT_DIR / "hype_poster.png"
    frames[0].convert("RGB").save(png_path, "PNG", optimize=True)

    gif_path = OUT_DIR / "hype_poster.gif"
    duration = 80  # ms per frame
    frames[0].save(
        gif_path,
        save_all=True,
        append_images=frames[1:],
        duration=duration,
        loop=0,
        disposal=2,
        optimize=False,
    )
    print(f"Wrote {png_path} ({png_path.stat().st_size // 1024} KB)")
    print(f"Wrote {gif_path} ({gif_path.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
