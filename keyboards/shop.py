from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from i18n import t
from keyboards import hype_emoji as hype
from keyboards.buttons import button

PAGE_SIZE = 14
QTY_PRESETS = [1, 2, 3, 5, 10, 15, 20, 25]
DEFAULT_PRODUCT_ICON = "📦"
# Telegram inline-button text hard limit (API).
MAX_BUTTON_TEXT = 64
# Legacy width reserve kept for compatibility with older tests/callers.
GLYPH_BUDGET = 2


def format_price(price: float) -> str:
    """$18 for whole dollars, $18.50 when cents matter."""
    p = float(price)
    if p == int(p):
        return f"${int(p)}"
    return f"${p:.2f}"


def format_coin_amount(amount: float) -> str:
    """10 for whole amounts, 10.50 when cents matter (no currency symbol)."""
    a = round(float(amount), 2)
    if a == int(a):
        return str(int(a))
    return f"{a:.2f}"


def _truncate(name: str, max_len: int) -> str:
    name = name.strip()
    if max_len < 4:
        max_len = 4
    if len(name) <= max_len:
        return name
    return name[: max_len - 1].rstrip() + "…"


def product_button_text(name: str, price: float, stock: int,
                        *, budget: int = MAX_BUTTON_TEXT) -> str:
    """
    Button label WITHOUT a leading icon (icon is supplied via
    icon_custom_emoji_id). Reference-shop pattern:

      In stock      →  Manus Pro 12m — $45 (Stock: 1)
      Out of stock  →  Manus Pro 12m — $45 (Out of stock)

    Full product name is shown. Truncation only happens if the label
    exceeds Telegram's 64-character button limit.
    """
    name = name.strip()
    price_s = format_price(price)
    if stock <= 0:
        suffix = f" — {price_s} {t('btn.out_of_stock')}"
    else:
        suffix = f" — {price_s} {t('btn.stock', n=stock)}"
    full = f"{name}{suffix}"
    if len(full) <= budget:
        return full
    name_budget = budget - len(suffix)
    if name_budget < 4:
        return full[:budget]
    return f"{_truncate(name, name_budget)}{suffix}"


def product_list_label(name: str, icon: str, price: float, stock: int) -> str:
    """Fallback label when no custom emoji icon is configured."""
    return product_button_text(name, price, stock)


def _product_button(product, stock: int) -> InlineKeyboardButton:
    from keyboards import product_icons as pi

    label = product_button_text(product["name"], float(product["base_price"]), stock)
    emoji_id = pi.icon_id_for_product(product, stock) or hype.custom_id_for_char(DEFAULT_PRODUCT_ICON)
    if emoji_id:
        return InlineKeyboardButton(
            text=label,
            callback_data=f"prod:{product['id']}",
            icon_custom_emoji_id=emoji_id,
        )
    return InlineKeyboardButton(
        text=product_list_label(product["name"], product["emoji"] or "", float(product["base_price"]), stock),
        callback_data=f"prod:{product['id']}",
    )


def products_keyboard(products: list, stock_counts: dict[int, int],
                      page: int, total_pages: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for p in products:
        sc = stock_counts.get(p["id"], 0)
        rows.append([_product_button(p, sc)])

    rows.append([
        button("btn.refresh", callback_data=f"shop:page:{page}"),
        button("btn.back_menu", callback_data="menu:home"),
    ])

    if total_pages > 1:
        nav: list[InlineKeyboardButton] = [
            InlineKeyboardButton(text=t("btn.page", page=page + 1, total=total_pages),
                                 callback_data="noop"),
        ]
        if page > 0:
            nav.insert(0, InlineKeyboardButton(text=t("btn.prev"), callback_data=f"shop:page:{page - 1}"))
        if page + 1 < total_pages:
            nav.append(InlineKeyboardButton(text=t("btn.next"), callback_data=f"shop:page:{page + 1}"))
        rows.append(nav)

    return InlineKeyboardMarkup(inline_keyboard=rows)


def _buy_now_button(product_id: int) -> InlineKeyboardButton:
    emoji_id = hype.buy_button_icon_id()
    if emoji_id:
        return InlineKeyboardButton(
            text=t("btn.buy_now"),
            callback_data=f"buy:{product_id}",
            icon_custom_emoji_id=emoji_id,
            style="primary",
        )
    return InlineKeyboardButton(
        text=hype.clean_button_text(t("btn.buy_now")),
        callback_data=f"buy:{product_id}",
        style="primary",
    )


def product_detail_keyboard(product_id: int, in_stock: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if in_stock:
        rows.append([_buy_now_button(product_id)])
    rows.append([button("btn.back_store", callback_data="shop:page:0")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def quantity_keyboard(product_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for q in QTY_PRESETS:
        kb.button(text=str(q), callback_data=f"qty:{product_id}:{q}")
    kb.row()
    custom = button("btn.custom_amount", callback_data=f"qtyc:{product_id}")
    kb.add(custom)
    kb.row()
    back = button("btn.back", callback_data=f"prod:{product_id}")
    kb.add(back)
    kb.adjust(4, 4, 1, 1)
    return kb.as_markup()


def checkout_summary_keyboard(order_id: int) -> InlineKeyboardMarkup:
    """Summary screen now goes straight to the payment-method picker —
    the intermediate 'Pay Directly' tap is gone since coupons are not
    used in this shop."""
    from keyboards.payment import method_rows
    rows = method_rows(include_wallet=True, order_id=order_id)
    rows.append([
        button("btn.cancel", callback_data=f"order:cancel:{order_id}"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)
