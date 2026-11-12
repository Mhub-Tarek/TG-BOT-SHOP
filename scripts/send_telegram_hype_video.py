#!/usr/bin/env python3
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiogram import Bot
from aiogram.types import FSInputFile
from config import get_settings

CAPTION = (
    "🐍 <b>AKUNDING SHOP</b> — same style as the reference clip\n"
    "👉 <a href=\"https://t.me/Akunding_store_bot\">@Akunding_store_bot</a>"
)


async def main() -> None:
    root = Path(__file__).resolve().parent.parent
    video = root / "assets" / "hype" / "telegram_hype_scroll.mp4"
    txt = root / "assets" / "hype" / "telegram_post.txt"
    if not video.is_file():
        print("Missing video — run render_safwan_telegram_video.py first")
        return

    bot = Bot(token=get_settings().BOT_TOKEN)
    for admin_id in get_settings().ADMIN_IDS or []:
        await bot.send_video(admin_id, FSInputFile(video), caption=CAPTION, parse_mode="HTML")
        if txt.is_file():
            await bot.send_message(
                admin_id,
                txt.read_text(encoding="utf-8"),
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        print(f"Sent video + text to {admin_id}")
    await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
