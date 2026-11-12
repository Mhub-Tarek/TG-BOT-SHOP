#!/usr/bin/env python3
"""Send SAFWAN-style hype price list — Telegram message only (animated header)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from aiogram import Bot
from aiogram.enums import ParseMode

from config import get_settings
from hype_post_builder import OUT, build_hype_html, load_products


async def main() -> None:
    root = Path(__file__).resolve().parent.parent
    db = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "shop.db"
    if not db.is_file():
        db = Path("/opt/telegram-shop/shop.db")

    products = load_products(db)
    html = build_hype_html(products)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "telegram_post.txt").write_text(html, encoding="utf-8")

    bot = Bot(token=get_settings().BOT_TOKEN)
    for admin_id in get_settings().ADMIN_IDS or []:
        await bot.send_message(
            admin_id,
            html,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        print(f"Sent hype message to {admin_id}")

    await bot.session.close()
    print(f"Saved: {OUT / 'telegram_post.txt'}")


if __name__ == "__main__":
    asyncio.run(main())
