from __future__ import annotations

from aiogram.types import InlineKeyboardButton

from i18n import t
from keyboards import hype_emoji as hype
from keyboards import icons as payment_icons


BUTTON_ICON_ALIASES: dict[str, tuple[str, ...]] = {
    "btn.shop": ("\U0001f6cd",),
    "btn.shop_more": ("\U0001f6cd",),
    "btn.buy_named": ("\U0001f4b8", "\U0001f4b5"),
    "btn.wallet": ("\U0001f4b5", "\U0001f4b8"),
    "btn.deposit": ("\U0001f4b5", "\U0001f4b8"),
    "btn.pay_wallet": ("\U0001f4b5", "\U0001f4b8"),
    "btn.profile": ("\U0001f642", "\U0001f440"),
    "btn.profile_short": ("\U0001f642", "\U0001f440"),
    "btn.support": ("\U0001f4ac",),
    "btn.report_issue": ("\u2757\ufe0f", "\U0001f4ac"),
    "btn.open_ticket": ("\U0001f4cc", "\U0001f4ac"),
    "btn.message_us": ("\U0001f4ac",),
    "btn.history": ("\U0001f4cc",),
    "btn.purchase_history": ("\U0001f4cc",),
    "btn.refer": ("\u2b50\ufe0f", "\U0001f389"),
    "btn.api_link": ("\U0001f517",),
    "btn.api_regenerate": ("\U0001f504",),
    "btn.api_regenerate_yes": ("\u2705", "\U0001f504"),
    "btn.copy_link": ("\U0001f517",),
    "btn.copy_binance_id": ("\U0001f194", "\U0001f517"),
    "btn.copy_address": ("\U0001f3e6", "\U0001f517"),
    "btn.copy_exact_amount": ("\U0001f4b8", "\U0001f4b5"),
    "btn.language": ("\U0001f310",),
    "btn.back": ("\U0001f504",),
    "btn.back_menu": ("\U0001f3e0",),
    "btn.main_menu": ("\U0001f3e0",),
    "btn.back_store": ("\U0001f6cd",),
    "btn.back_orders": ("\U0001f4cc",),
    "btn.cancel": ("\u274c",),
    "btn.cancel_payment": ("\u274c",),
    "btn.check_paid": ("\U0001f504",),
    "btn.refresh": ("\U0001f504",),
    "btn.custom_amount": ("\u270f\ufe0f",),
    "btn.download_txt": ("\u2b07\ufe0f", "\U0001f4ce"),
    "btn.download_excel": ("\U0001f4ca", "\u2b07\ufe0f"),
    "btn.download_yes": ("\u2714\ufe0f", "\U0001f7e2"),
}


def icon_id_for_key(key: str) -> str | None:
    if key in {"btn.shop", "btn.shop_more", "btn.back_store"}:
        return hype.store_icon_id()
    if key == "btn.buy_named":
        return hype.buy_button_icon_id()
    if key in {"btn.wallet", "btn.deposit", "btn.pay_wallet"}:
        wallet_id = payment_icons.method_custom_emoji_id("wallet")
        if wallet_id:
            return wallet_id
    for char in BUTTON_ICON_ALIASES.get(key, ()):
        emoji_id = hype.custom_id_for_char(char)
        if emoji_id:
            return emoji_id
    return None


def text_for_key(key: str, **kwargs) -> str:
    return hype.clean_button_text(t(key, **kwargs))


def button(key: str, *, callback_data: str | None = None,
           url: str | None = None, style: str | None = None,
           **kwargs) -> InlineKeyboardButton:
    payload = {
        "text": text_for_key(key, **kwargs),
        "callback_data": callback_data,
        "url": url,
        "icon_custom_emoji_id": icon_id_for_key(key),
    }
    if style:
        payload["style"] = style
    return InlineKeyboardButton(**payload)
