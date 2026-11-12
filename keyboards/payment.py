from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import get_settings
from keyboards import icons as ic
from keyboards.buttons import button, icon_id_for_key, text_for_key
from i18n import t


def payment_methods_text(
    *,
    product_name: str,
    quantity: int,
    subtotal: float,
    discount: float,
    payable: float,
    wallet_balance: float,
) -> str:
    """Checkout payment-method screen."""
    lines = [
        t("pay.method_title"),
        "━━━━━━━━━━━━━━━━",
        "",
        t("pay.product", name=product_name),
        t("pay.quantity", qty=quantity),
        t("pay.subtotal", subtotal=ic.money(subtotal)),
    ]
    if discount > 0:
        lines.append(t("pay.discount", discount=ic.money(discount)))
    lines.extend([
        t("pay.payable", payable=ic.money(payable)),
        "",
        t("pay.wallet", balance=ic.money(wallet_balance)),
    ])
    return "\n".join(lines)


def deposit_methods_text(*, amount: float, wallet_balance: float) -> str:
    """Deposit payment-method screen (same layout as checkout)."""
    return "\n".join([
        t("pay.method_title"),
        "━━━━━━━━━━━━━━━━",
        "",
        t("pay.topup_label"),
        t("pay.amount", amount=ic.money(amount)),
        t("pay.payable", payable=ic.money(amount)),
        "",
        t("pay.wallet", balance=ic.money(wallet_balance)),
    ])


def _method_button(method: str, callback_data: str) -> InlineKeyboardButton:
    """One button per row with real brand icon when emoji pack is installed."""
    emoji_id = ic.method_custom_emoji_id(method)
    if emoji_id:
        return InlineKeyboardButton(
            text=ic.method_button_text(method),
            callback_data=callback_data,
            icon_custom_emoji_id=emoji_id,
        )
    return InlineKeyboardButton(
        text=ic.method_button_text(method),
        callback_data=callback_data,
    )


def method_rows(*, include_wallet: bool,
                order_id: int | None = None) -> list[list[InlineKeyboardButton]]:
    """One row per enabled payment method. Shared between the checkout
    summary screen and the deposit-method picker."""
    rows: list[list[InlineKeyboardButton]] = []
    for method in get_settings().enabled_methods():
        if method == "wallet" and not include_wallet:
            continue
        if order_id is not None:
            cb = f"pay:do:{order_id}:{method}"
        else:
            cb = f"dep:do:{method}"
        rows.append([_method_button(method, cb)])
    return rows


def payment_methods_keyboard(order_id: int) -> InlineKeyboardMarkup:
    rows = method_rows(include_wallet=True, order_id=order_id)
    rows.append([
        button("btn.back", callback_data=f"order:show:{order_id}"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def deposit_methods_keyboard() -> InlineKeyboardMarkup:
    rows = method_rows(include_wallet=False)
    rows.append([
        button("btn.back_menu", callback_data="menu:home"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def order_download_rows(order_id: int) -> list[list[InlineKeyboardButton]]:
    return [[
        button("btn.download_txt", callback_data=f"order:download:{order_id}:txt"),
        button("btn.download_excel", callback_data=f"order:download:{order_id}:xlsx"),
    ]]


def download_confirm_keyboard(order_id: int, fmt: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            button("btn.download_yes", callback_data=f"order:download_send:{order_id}:{fmt}"),
            button("btn.cancel", callback_data=f"order:download_cancel:{order_id}"),
        ],
    ])


def delivery_keyboard(order_id: int) -> InlineKeyboardMarkup:
    """Shown after a successful delivery: raise a support ticket about this
    order (lands in the admin dashboard), shop more, or back to the menu."""
    return InlineKeyboardMarkup(inline_keyboard=[
        *order_download_rows(order_id),
        [button("btn.report_issue", callback_data=f"ticket:new:{order_id}")],
        [button("btn.shop_more", callback_data="menu:shop")],
        [button("btn.main_menu", callback_data="menu:home")],
    ])


def _copy_button(key: str, value: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text_for_key(key),
        copy_text={"text": value},
        icon_custom_emoji_id=icon_id_for_key(key),
    )


def pending_payment_keyboard(
    payment_id: int,
    *,
    binance_uid: str = "",
    address: str = "",
    exact_amount: str = "",
) -> InlineKeyboardMarkup:
    rows = []
    if binance_uid:
        rows.append([_copy_button("btn.copy_binance_id", binance_uid)])
    if address:
        rows.append([_copy_button("btn.copy_address", address)])
    if exact_amount:
        rows.append([_copy_button("btn.copy_exact_amount", exact_amount)])
    rows.extend([
        [button("btn.check_paid", callback_data=f"pay:check:{payment_id}")],
        [button("btn.cancel_payment", callback_data=f"pay:cancel:{payment_id}")],
        [button("btn.main_menu", callback_data="menu:home")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)
