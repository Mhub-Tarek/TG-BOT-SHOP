#!/usr/bin/env python3
"""Send SAFWAN-style hype price-list to ADMIN_IDS. Run on VPS only."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiogram import Bot
from aiogram.enums import ParseMode
from config import get_settings

# Matches SAFWAN TIGER / Claude & Gpt Ai group style from reference video
HYPE_HTML = """
<b>ＡＫＵＮＤＩＮＧ ＴＩＧＥＲ™</b> 👹

<b>🐍 ＡＫＵＮＤＩＮＧ－ＳＨＯＰ 🐍</b>

<code>ＢＯＴ－ＯＲＤＥＲ．．．．．．．</code>

1️⃣ ChatGPT Go_3M Code — No — war — <b>0.5$</b> 🔥<b>HOT</b>

🤖 ChatGPT Plus_1M — <s>30D — war</s> — <b>7.0$</b> 🔥<b>HOT</b>

✨ Gemini AI Pro_18M — No — war — <b>1.8$</b> 🔥<b>HOT</b>

🎨 Meshy AI Pro_30D — <s>30D — war</s> — <b>2.0$</b> 🔥<b>HOT</b>

🎓 Coursera Premium_12M — <s>30D — war</s> — <b>3.0$</b> 🔥<b>HOT</b>

🎙 ElevenLabs_1M Creator — <s>3D — war</s> — <b>3.0$</b> 🔥<b>HOT</b>

🎮 Discord Nitro_1M Code — No — war — <b>1.0$</b> 🔥<b>HOT</b>

⚡ Warp Build_1Y — No — war — <b>12$</b> 🔥<b>HOT</b>

🔗 n8n Starter_1Y — No — war — <b>18$</b> 🔥<b>HOT</b>

🎤 Wispr Flow Pro_12M — No — war — <b>20$</b> 🔥<b>HOT</b>

💻 Replit Core_1Y — No — war — <b>30$</b> 🔥<b>HOT</b>

📊 Gamma Pro_1Y — No — war — <b>35$</b> 🔥<b>HOT</b>

🎙 ElevenLabs Creator_1Y — <s>365D — war</s> — <b>40$</b> 🔥<b>HOT</b>

🗄 Supabase Pro_1Y — No — war — <b>40$</b> 🔥<b>HOT</b>

🤖 Manus Pro_12M — No — war — <b>45$</b> 🔥<b>HOT</b>

━━━━━━━━━━━━━━━━
⚡ <b>INSTANT</b> · USDT · Wallet · Binance Pay
👉 <a href="https://t.me/Akunding_store_bot?start=shop">ＯＰＥＮ ＢＯＴ — @Akunding_store_bot</a>
""".strip()


async def main() -> None:
    settings = get_settings()
    bot = Bot(token=settings.BOT_TOKEN)
    admins = settings.ADMIN_IDS or []
    if not admins:
        print("No ADMIN_IDS")
        return
    for admin_id in admins:
        await bot.send_message(admin_id, HYPE_HTML, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        print(f"Sent to {admin_id}")
    await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
