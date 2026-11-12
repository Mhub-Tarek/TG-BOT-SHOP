#!/usr/bin/env python3
"""Send hype GIF + PNG to admin."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiogram import Bot
from aiogram.types import BufferedInputFile, FSInputFile
from config import get_settings

CAPTION = (
    "🐍 <b>AKUNDING SHOP</b> — live prices\n\n"
    "⚡ Animated list · real brand logos\n"
    "👉 <a href=\"https://t.me/Akunding_store_bot\">@Akunding_store_bot</a>\n"
    "🛡 <a href=\"https://refund.akunding.shop\">Refund check</a>"
)


async def main() -> None:
    root = Path(__file__).resolve().parent.parent
    gif_path = root / "assets" / "hype" / "hype_poster.gif"
    png_path = root / "assets" / "hype" / "hype_poster.png"
    if not gif_path.is_file():
        print("Run generate_hype_poster.py first")
        return

    settings = get_settings()
    bot = Bot(token=settings.BOT_TOKEN)
    for admin_id in settings.ADMIN_IDS or []:
        await bot.send_animation(
            admin_id,
            FSInputFile(gif_path),
            caption=CAPTION,
            parse_mode="HTML",
        )
        if png_path.is_file():
            await bot.send_document(
                admin_id,
                FSInputFile(png_path),
                caption="HD static version (for channel post)",
            )
        print(f"Sent GIF + PNG to {admin_id}")
    await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
