"""
Lightweight i18n for the buyer-facing bot.

A per-update language is held in a ContextVar (set by the LanguageMiddleware for
handlers, and by `use_language(...)` for background sends like the poller and
broadcasts). `t(key, **data)` returns the translated, formatted string with an
English fallback (then the key itself). Only BUYER-facing chrome is translated —
admin commands, the web dashboard, and product/coupon DATA stay as-is.

Translations for ar/hi/vi/zh are machine-generated; a native proofread is
recommended before heavy promotion. English is the source of truth + fallback.
"""
from __future__ import annotations

import contextvars

DEFAULT = "en"
# code -> native display name (used by the language picker)
SUPPORTED = {
    "en": "English",
    "ar": "العربية",
    "hi": "Hinglish",
    "vi": "Tiếng Việt",
    "zh": "简体中文",
}

_lang: contextvars.ContextVar[str] = contextvars.ContextVar("lang", default=DEFAULT)


def normalize(lang: str | None) -> str:
    return lang if lang in SUPPORTED else DEFAULT


def set_current_language(lang: str | None) -> None:
    _lang.set(normalize(lang))


def current_language() -> str:
    return _lang.get()


class use_language:
    """Context manager to run a block under a specific language (poller/broadcast)."""

    def __init__(self, lang: str | None) -> None:
        self.lang = normalize(lang)
        self._token = None

    def __enter__(self) -> "use_language":
        self._token = _lang.set(self.lang)
        return self

    def __exit__(self, *exc) -> bool:
        if self._token is not None:
            _lang.reset(self._token)
        return False


def t(key: str, /, **fmt) -> str:
    """Translate `key` for the current language (fallback: English, then the key).
    `**fmt` fills `{placeholders}` (product name, price, ids, …)."""
    lang = _lang.get()
    s = TRANSLATIONS.get(lang, {}).get(key)
    if s is None:
        s = TRANSLATIONS["en"].get(key, key)
    if fmt:
        try:
            return s.format(**fmt)
        except (KeyError, IndexError, ValueError):
            return s
    return s


# ---------------------------------------------------------------------------
# Catalog. Every language dict MUST share the same key set as "en" (asserted by
# tests/smoke_test.test_i18n). Keep {placeholders} and HTML tags identical
# across languages; only translate the human words.
# ---------------------------------------------------------------------------

EN: dict[str, str] = {
    # --- language picker ---
    "lang.choose": "🌐 <b>Choose your language</b>\n\nPick the language you'd like to use:",
    "lang.changed": "✅ Language updated.",
    "lang.invalid": "Invalid language.",
    "delivery.footer": "🌟 <i>Thank you for your purchase!</i> 💎",
    "delivery.codes_continued": "📜 <b>Your codes continued:</b>",
    "delivery.instructions": "📋 <b>Instructions:</b>\n{body}",

    # --- buttons ---
    "btn.shop": "🛍 Shop",
    "btn.wallet": "💰 Wallet",
    "btn.profile": "👤 My Profile",
    "btn.support": "🆘 Support",
    "btn.history": "📜 History",
    "btn.language": "🌐 Language",
    "btn.profile_short": "👤 Profile",
    "btn.purchase_history": "📜 Orders",
    "btn.deposit": "💳 Deposit",
    "btn.back_menu": "« Back to menu",
    "btn.main_menu": "🏠 Main Menu",
    "btn.back": "Back",
    "btn.back_store": "« Back to Store",
    "btn.buy_now": "🛒 Buy Now",
    "btn.custom_amount": "✏️ Custom Amount",
    "btn.cancel": "❌ Cancel",
    "btn.refresh": "🔄 Refresh",
    "btn.prev": "« Prev",
    "btn.next": "Next »",
    "btn.report_issue": "🆘 Report an issue",
    "btn.shop_more": "🛍 Shop More",
    "btn.check_paid": "🔄 I have paid — check now",
    "btn.cancel_payment": "🛑 Cancel Payment",
    "btn.open_ticket": "🎫 Open a ticket",
    "btn.message_us": "💬 Message @{user}",
    "btn.pay_wallet": "👛 Pay via Wallet",
    "btn.page": "Page {page}/{total}",
    "btn.buy_named": "Buy Now",
    "btn.stock": "(Stock: {n})",
    "btn.out_of_stock": "(Out of stock)",

    # --- start / welcome ---
    "start.welcome": (
        "👑 <b>Welcome to {shop}!</b> ✨\n\n"
        "Hey <b>{name}</b> ⭐\n\n"
        "🎁 Premium digital products at the best prices.\n"
        "⚡ Fast, secure and fully automated delivery.\n\n"
        "<blockquote>🛍 <b>Shop</b> — Browse and buy products\n"
        "💰 <b>Wallet</b> — Add funds and manage balance\n"
        "👤 <b>My Profile</b> — Balance and orders\n"
        "📜 <b>History</b> — Your past purchases\n"
        "🆘 <b>Support</b> — Get help</blockquote>\n\n"
        "Choose an option below to continue! 👇"
    ),

    # --- shop ---
    "shop.list_title": "🛍 <b>Available Products</b> ✨",
    "shop.none": "No products available yet. Check back soon!",
    "shop.tap_page": "Tap a product below (page {page}/{total}):",
    "shop.tap": "Tap a product below:",
    "shop.card_price": "💲 Price: <b>${price}</b> / {unit}",
    "shop.card_stock": "📦 Stock: <b>{n} available</b>",
    "shop.card_bulk": "📊 <b>Bulk pricing:</b>",
    "shop.card_auto": "⚡ <i>Delivery is automatic after payment confirmation.</i>",
    "shop.buy_header": "<b>{name}</b>\n\n💲 Price: ${price} / {unit}\n\n📌 How many {unit}s do you want?",
    "shop.ask_qty": "Reply with the quantity you want (1-1000):",
    "shop.qty_whole": "Send a whole number between 1 and 1000.",
    "shop.qty_range": "Pick a quantity between {min} and {max}.",
    "shop.only_n": "Only {n} in stock right now. Pick a smaller quantity.",
    "shop.product_gone": "Product no longer exists.",
    "shop.unavailable": "This product is no longer available.",
    "shop.blocked": "You cannot place orders right now. Contact support if you think this is a mistake.",
    "shop.rate_limited": "Too many checkout attempts. Please wait about {minutes} minute(s) and try again.",
    "shop.not_found": "Product not found.",
    "shop.how_many": "📌 How many {unit}s do you want?",
    # order summary
    "sum.qty": "🧮 Quantity: <b>{qty}</b> {unit}(s)",
    "sum.subtotal": "💲 Subtotal: <b>${subtotal}</b>",
    "sum.discount": "🎟 Discount: −${discount}",
    "sum.payable": "💎 Final Payable: <b>${payable}</b>",
    "sum.wallet_admin": "💳 Your Wallet: <b>∞ (admin)</b>",
    "sum.wallet": "💳 Your Wallet: <b>${balance}</b>",
    "sum.short": "   <i>(short ${short} for wallet payment — use a crypto method below)</i>",
    "sum.choose": "<i>Choose a payment method below 👇</i>",

    # --- common alerts ---
    "common.start_first": "Type /start first.",
    "common.private_only": "Open the bot in private chat to use this.",
    "common.not_your_order": "This is not your order.",
    "common.not_your_payment": "This is not your payment.",
    "common.order_not_found": "Order not found.",
    "common.order_closed": "Order is no longer open.",
    "common.cancelled": "❌ Order cancelled.",

    # --- profile ---
    "profile.title": "💎 <b>My Profile</b> ✨",
    "profile.name": "👋 Name: <b>{name}</b>",
    "profile.tier": "🏅 Tier: <b>{tier}</b>",
    "profile.balance": "💰 Balance: <b>{balance}</b>",
    "profile.spent": "🛍 Total spent: <b>{spent}</b>",
    "profile.next_tier": "\n🚀 <i>{remaining} more to reach <b>{tier}</b></i>",
    "profile.refreshed": "Profile refreshed.",
    "tier.Diamond": "Diamond",
    "tier.Platinum": "Platinum",
    "tier.Gold": "Gold",
    "tier.Silver": "Silver",
    "tier.Bronze": "Bronze",
    "status.delivered": "Delivered",
    "status.paid": "Paid",
    "status.awaiting_payment": "Awaiting payment",
    "status.draft": "Draft",
    "status.cancelled": "Cancelled",
    "status.expired": "Expired",
    "status.failed": "Failed",

    # --- history ---
    "history.title": "📜 <b>Purchase history</b> ✨",
    "history.none": "No orders yet — tap 🛍 <b>Shop</b> to grab something!",
    "history.older": "<i>… older orders hidden ({n} more)</i>",
    "history.order_num": "<b>Order #{id}</b>",
    "history.delivered_label": "🎁 <b>Delivered:</b>",
    "history.delivered_inline": "🎁 <b>Delivered:</b> <code>{code}</code>",
    "history.more_codes": "   <i>+{n} more (also sent to you in chat)</i>",

    # --- deposit ---
    "deposit.wallet_title": "💰 <b>Wallet</b> ✨",
    "deposit.balance_admin": "💎 Current balance: <b>∞ Unlimited</b> (admin)",
    "deposit.balance": "💎 Current balance: <b>{balance}</b>",
    "deposit.tap_deposit": "<i>Tap 💳 <b>Deposit</b> below to top up.</i>",
    "deposit.prompt": "💰 <b>Deposit</b> ⚡\n\n💲 Minimum: <b>{min}</b> USDT\n\n👇 Reply with the amount you want to add to your wallet.",
    "deposit.min_err": "Send a valid number ≥ {min}.",
    "deposit.max_err": "Max deposit is {max} USDT. Send a smaller amount.",
    "deposit.start_over": "Start over via Deposit.",

    # --- payment ---
    "pay.method_title": "💳 <b>Payment Method</b>",
    "pay.product": "🧾 Product: {name}",
    "pay.quantity": "🔢 Quantity: {qty}",
    "pay.subtotal": "💲 Subtotal: {subtotal}",
    "pay.discount": "🎟 Discount: −{discount}",
    "pay.payable": "💎 Final Payable: {payable}",
    "pay.wallet": "💰 Your Wallet: {balance}",
    "pay.topup_label": "💳 Wallet Top-up",
    "pay.amount": "💵 Amount: {amount}",
    "topup.title": "{method} — Wallet top-up",
    "pay.auto": "🔄 Auto-verification enabled",
    "pay.send_exact": "📤 <b>Send exactly: {amount} {coin}</b>",
    "pay.uid": "🆔 Binance UID: <code>{uid}</code>",
    "pay.address": "🏦 Address:\n<code>{address}</code>",
    "pay.expires": "⏳ Expires in {minutes} minutes.",
    "pay.payment_id": "🧾 Payment ID: <b>#{id}</b>",
    "pay.order_total": "💎 Order Total: <b>{payable} USDT</b>",
    "pay.product_line": "📦 Product: {prefix}<b>{name}</b>",
    "pay.qty_line": "🔢 Quantity: <b>{qty}</b>",
    "pay.important": "⚠️ <b>Important:</b>",
    "pay.send_exact_amount": "• Send the exact amount shown above.",
    "pay.exact_warning": "❗️ <b>Send this EXACT amount — do not round it.</b> A rounded or different amount may not be auto-credited and can delay your order.",
    "pay.via_binance": "• Send via Binance app → <b>Send</b> → choose <b>Binance ID</b>.",
    "pay.use_network": "• Use the <b>{net}</b> network. Wrong network = funds may be lost.",
    "pay.auto_deliver": "🔄 Payment will be verified and delivered automatically.",
    "pay.disabled": "This payment method is currently disabled.",
    "pay.user_missing": "User missing — /start",
    "pay.not_payable": "❌ This order is no longer payable (already paid, cancelled, or expired).",
    "pay.insufficient": "❌ Insufficient wallet balance. Pick a crypto method to pay this order directly, or top up via Deposit.",
    "pay.oos": "❌ Stock ran out just before fulfilment. Your balance is unchanged.",
    "pay.delivered": (
        "✅ <b>Order #{id} delivered</b>\n"
        "Charged: ${charged}\n\n"
        "I sent your codes below.\n\n"
        "🛟 <i>Problem with this order? Don't DM — tap 🆘 Report an issue below and we'll fix it right here.</i>"
    ),
    "pay.codes_head": "📜 <b>Your codes for order #{id}:</b>",
    "pay.confirmed_order": "✅ Payment confirmed — check your messages for delivery.",
    "pay.confirmed_deposit": "✅ Deposit confirmed — balance credited.",
    "pay.expired": "⏰ This payment expired.",
    "pay.cancelled_msg": "❌ This payment was cancelled.",
    "pay.still_pending": "Still pending. Auto-poller checks every {seconds}s.",
    "pay.cancelled_done": "❌ Payment cancelled.",
    "pay.payment_not_found": "Payment not found.",
    "pay.order_missing": "Order missing.",
    "pay.delivered_alert": "Delivered!",

    # --- support ---
    "support.title": "🆘 <b>Support</b> 💬",
    "support.with_orders": (
        "Need help with one of your orders?\n\n"
        "🎫 <b>Open a ticket</b> about an order — tracked and never missed (recommended).\n"
        "💬 Or message us directly.\n\n"
        "⚡ <i>Average response: under 1 hour during business hours.</i>"
    ),
    "support.no_orders": (
        "Need help? Tap below to message us — usually replies within a few hours.\n\n"
        "⚡ <i>Average response: under 1 hour during business hours.</i>"
    ),

    # --- tickets ---
    "ticket.pick_title": "🆘 <b>Report an issue</b> 🎫\n\nPick the order you need help with 👇",
    "ticket.none": "You have no orders to report on yet.",
    "ticket.invalid": "Invalid selection.",
    "ticket.cap": "You already have several open tickets — we'll reply to those first. Please wait for a response before opening another.",
    "ticket.describe": (
        "🆘 <b>Report an issue</b> 📝\n\n"
        "Order <b>#{id}</b> — {name}\n\n"
        "Describe the problem in one message (e.g. code didn't work, wrong item). Our team will reply to you right here."
    ),
    "ticket.send_text": "Please send your issue as a text message.",
    "ticket.too_long": "That's a bit long — please keep it under {max} characters.",
    "ticket.expired": "Session expired — open the order again to report it.",
    "ticket.not_yours": "That order is not yours.",
    "ticket.received": (
        "✅ <b>Ticket #{id} received</b>\n\n"
        "Thanks — we've logged your issue for order <b>#{order}</b> and will reply to you here. 💬"
    ),

    # --- poller notifications ---
    "ticket.reply_dm": "💬 <b>Reply to your ticket #{id}</b> (order #{order})\n\n{body}",
    "notify.balance": "💰 <b>Balance update</b>\n\nAdjustment: {sign}{delta} USDT\nNew balance: {balance} USDT",
    "notify.balance_note": "\nNote: {note}",
    "notify.blocked": "🚫 <b>Purchases disabled</b>\n\nYou can no longer place orders in this shop. Contact support if you need help.",
    "notify.unblocked": "✅ <b>Purchases restored</b>\n\nYou can place orders again.",
    "notify.expired": "⏰ Payment <b>#{id}</b> expired before it was received.",
    "notify.refunded": "⚠️ Payment <b>#{id}</b> received but stock ran out. <b>{amount} {coin}</b> was credited to your wallet as a refund.",
    "notify.delivered": (
        "🎉 <b>Order Delivered!</b> ✨\n"
        "━━━━━━━━━━━━━━━━━\n"
        "📦 Product: <b>{name}</b>\n"
        "🆔 Order: <b>#{id}</b>\n"
        "💰 Paid: <b>{amount} {coin}</b>\n"
        "🔗 TXID: <code>{txid}</code>\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        "🎁 <b>Your codes:</b>"
    ),
    "notify.help": "🛟 <i>Problem with this order? Don't DM — tap 🆘 Report an issue below and we'll fix it right here.</i>",
    "notify.deposit": (
        "💸 <b>Deposit Received!</b> ✨\n"
        "━━━━━━━━━━━━━━━━━\n"
        "💰 Amount: <b>{amount} {coin}</b>\n"
        "🔗 TXID: <code>{txid}</code>\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        "💎 Balance credited — tap /shop to start buying!"
    ),

    # --- broadcast chrome (admin-typed body & product names stay as data) ---
    "bc.restock": "🆕 <b>{n} new stock added for {name}!</b>",
    "bc.available": "📦 Available: <b>{n} items</b>",
    "bc.price": "💲 Price: <b>${price} USDT</b>",
    "bc.scarcity_left": "⚠️ <b>Only {n} left</b> — these move fast.",
    "bc.scarcity_limited": "⚡ <b>Limited drop</b> — these go fast. Lock yours in.",
    "bc.pricedrop": "📉 <b>Price Drop!</b>",
    "bc.was": "💲 Was: <s>{old} USDT</s>",
    "bc.now": "🌞 Now: <b>{new} USDT</b>",
    "bc.limited_offer": "<i>Limited time offer — buy now!</i>",
    "bc.new_product": "🆕 <b>NEW PRODUCT ADDED!</b>",
    "bc.name": "📦 Name: <b>{name}</b>",
    "bc.open_shop": "🌟 <b>Open Shop to buy now!</b>",
    "bc.admin_notification": "👁 <b>Admin Notification:</b>",
}

AR: dict[str, str] = {
    "lang.choose": "🌐 <b>اختر لغتك</b>\n\nاختر اللغة التي تريد استخدامها:",
    "lang.changed": "✅ تم تحديث اللغة.",
    "lang.invalid": "لغة غير صالحة.",
    "delivery.footer": "🌟 <i>شكراً لشرائك!</i> 💎",
    "delivery.codes_continued": "📜 <b>تابع أكوادك:</b>",
    "delivery.instructions": "📋 <b>التعليمات:</b>\n{body}",
    "btn.shop": "🛍 المتجر",
    "btn.wallet": "💰 المحفظة",
    "btn.profile": "👤 ملفي",
    "btn.support": "🆘 الدعم",
    "btn.history": "📜 السجل",
    "btn.language": "🌐 اللغة",
    "btn.profile_short": "👤 الملف",
    "btn.purchase_history": "📜 الطلبات",
    "btn.deposit": "💳 إيداع",
    "btn.back_menu": "« رجوع للقائمة",
    "btn.main_menu": "🏠 القائمة الرئيسية",
    "btn.back": "رجوع",
    "btn.back_store": "« رجوع للمتجر",
    "btn.buy_now": "🛒 اشترِ الآن",
    "btn.custom_amount": "✏️ كمية مخصصة",
    "btn.cancel": "❌ إلغاء",
    "btn.refresh": "🔄 تحديث",
    "btn.prev": "« السابق",
    "btn.next": "التالي »",
    "btn.report_issue": "🆘 الإبلاغ عن مشكلة",
    "btn.shop_more": "🛍 تسوّق المزيد",
    "btn.check_paid": "🔄 لقد دفعت — تحقق الآن",
    "btn.cancel_payment": "🛑 إلغاء الدفع",
    "btn.open_ticket": "🎫 فتح تذكرة",
    "btn.message_us": "💬 راسلنا @{user}",
    "btn.pay_wallet": "👛 الدفع من المحفظة",
    "btn.page": "صفحة {page}/{total}",
    "btn.buy_named": "اشتر الآن",
    "btn.stock": "(المتوفر: {n})",
    "btn.out_of_stock": "(نفد المخزون)",
    "start.welcome": (
        "👑 <b>مرحباً بك في {shop}!</b> ✨\n\n"
        "أهلاً <b>{name}</b> ⭐\n\n"
        "🎁 منتجات رقمية مميزة بأفضل الأسعار.\n"
        "⚡ تسليم سريع وآمن وتلقائي بالكامل.\n\n"
        "<blockquote>🛍 <b>المتجر</b> — تصفّح واشترِ المنتجات\n"
        "💰 <b>المحفظة</b> — أضف رصيداً وأدِر حسابك\n"
        "👤 <b>ملفي</b> — الرصيد والطلبات\n"
        "📜 <b>السجل</b> — مشترياتك السابقة\n"
        "🆘 <b>الدعم</b> — احصل على مساعدة</blockquote>\n\n"
        "اختر من الأسفل للمتابعة! 👇"
    ),
    "shop.list_title": "🛍 <b>المنتجات المتوفرة</b> ✨",
    "shop.none": "لا توجد منتجات متوفرة بعد. تحقق لاحقاً!",
    "shop.tap_page": "اضغط على منتج بالأسفل (صفحة {page}/{total}):",
    "shop.tap": "اضغط على منتج بالأسفل:",
    "shop.card_price": "💲 السعر: <b>${price}</b> / {unit}",
    "shop.card_stock": "📦 المخزون: <b>{n} متوفر</b>",
    "shop.card_bulk": "📊 <b>أسعار الجملة:</b>",
    "shop.card_auto": "⚡ <i>التسليم تلقائي بعد تأكيد الدفع.</i>",
    "shop.buy_header": "<b>{name}</b>\n\n💲 السعر: ${price} / {unit}\n\n📌 كم {unit} تريد؟",
    "shop.ask_qty": "أرسل الكمية التي تريدها (1-1000):",
    "shop.qty_whole": "أرسل رقماً صحيحاً بين 1 و1000.",
    "shop.qty_range": "اختر كمية بين {min} و{max}.",
    "shop.only_n": "يتوفر {n} فقط الآن. اختر كمية أقل.",
    "shop.product_gone": "لم يعد المنتج موجوداً.",
    "shop.unavailable": "هذا المنتج لم يعد متاحاً.",
    "shop.blocked": "لا يمكنك تقديم طلبات حالياً. تواصل مع الدعم إذا كان ذلك خطأ.",
    "shop.rate_limited": "محاولات دفع كثيرة جداً. انتظر حوالي {minutes} دقيقة ثم حاول مرة أخرى.",
    "shop.not_found": "المنتج غير موجود.",
    "shop.how_many": "📌 كم {unit} تريد؟",
    "sum.qty": "🧮 الكمية: <b>{qty}</b> {unit}",
    "sum.subtotal": "💲 المجموع الفرعي: <b>${subtotal}</b>",
    "sum.discount": "🎟 الخصم: −${discount}",
    "sum.payable": "💎 المبلغ النهائي: <b>${payable}</b>",
    "sum.wallet_admin": "💳 محفظتك: <b>∞ (مشرف)</b>",
    "sum.wallet": "💳 محفظتك: <b>${balance}</b>",
    "sum.short": "   <i>(ينقصك ${short} للدفع من المحفظة — استخدم طريقة عملة رقمية بالأسفل)</i>",
    "sum.choose": "<i>اختر طريقة دفع بالأسفل 👇</i>",
    "common.start_first": "اكتب /start أولاً.",
    "common.private_only": "افتح البوت في محادثة خاصة لاستخدام هذا.",
    "common.not_your_order": "هذا الطلب ليس لك.",
    "common.not_your_payment": "هذه العملية ليست لك.",
    "common.order_not_found": "الطلب غير موجود.",
    "common.order_closed": "لم يعد الطلب مفتوحاً.",
    "common.cancelled": "❌ تم إلغاء الطلب.",
    "profile.title": "💎 <b>ملفي</b> ✨",
    "profile.name": "👋 الاسم: <b>{name}</b>",
    "profile.tier": "🏅 المستوى: <b>{tier}</b>",
    "profile.balance": "💰 الرصيد: <b>{balance}</b>",
    "profile.spent": "🛍 إجمالي الإنفاق: <b>{spent}</b>",
    "profile.next_tier": "\n🚀 <i>تبقّى {remaining} للوصول إلى <b>{tier}</b></i>",
    "profile.refreshed": "تم تحديث الملف.",
    "tier.Diamond": "ماسي",
    "tier.Platinum": "بلاتيني",
    "tier.Gold": "ذهبي",
    "tier.Silver": "فضي",
    "tier.Bronze": "برونزي",
    "status.delivered": "تم التسليم",
    "status.paid": "مدفوع",
    "status.awaiting_payment": "بانتظار الدفع",
    "status.draft": "مسودة",
    "status.cancelled": "ملغى",
    "status.expired": "منتهٍ",
    "status.failed": "فشل",
    "history.title": "📜 <b>سجل المشتريات</b> ✨",
    "history.none": "لا توجد طلبات بعد — اضغط 🛍 <b>المتجر</b> لاقتناء شيء!",
    "history.older": "<i>… الطلبات الأقدم مخفية ({n} أخرى)</i>",
    "history.order_num": "<b>الطلب #{id}</b>",
    "history.delivered_label": "🎁 <b>تم التسليم:</b>",
    "history.delivered_inline": "🎁 <b>تم التسليم:</b> <code>{code}</code>",
    "history.more_codes": "   <i>+{n} المزيد (أُرسلت إليك في المحادثة أيضاً)</i>",
    "deposit.wallet_title": "💰 <b>المحفظة</b> ✨",
    "deposit.balance_admin": "💎 الرصيد الحالي: <b>∞ غير محدود</b> (مشرف)",
    "deposit.balance": "💎 الرصيد الحالي: <b>{balance}</b>",
    "deposit.tap_deposit": "<i>اضغط 💳 <b>إيداع</b> بالأسفل لإضافة رصيد.</i>",
    "deposit.prompt": "💰 <b>إيداع</b> ⚡\n\n💲 الحد الأدنى: <b>{min}</b> USDT\n\n👇 أرسل المبلغ الذي تريد إضافته إلى محفظتك.",
    "deposit.min_err": "أرسل رقماً صحيحاً ≥ {min}.",
    "deposit.max_err": "أقصى إيداع هو {max} USDT. أرسل مبلغاً أقل.",
    "deposit.start_over": "ابدأ من جديد عبر الإيداع.",
    "pay.method_title": "💳 <b>طريقة الدفع</b>",
    "pay.product": "🧾 المنتج: {name}",
    "pay.quantity": "🔢 الكمية: {qty}",
    "pay.subtotal": "💲 المجموع الفرعي: {subtotal}",
    "pay.discount": "🎟 الخصم: −{discount}",
    "pay.payable": "💎 المبلغ النهائي: {payable}",
    "pay.wallet": "💰 محفظتك: {balance}",
    "pay.topup_label": "💳 شحن المحفظة",
    "pay.amount": "💵 المبلغ: {amount}",
    "topup.title": "{method} — شحن المحفظة",
    "pay.auto": "🔄 التحقق التلقائي مُفعّل",
    "pay.send_exact": "📤 <b>أرسل بالضبط: {amount} {coin}</b>",
    "pay.uid": "🆔 معرّف Binance: <code>{uid}</code>",
    "pay.address": "🏦 العنوان:\n<code>{address}</code>",
    "pay.expires": "⏳ تنتهي خلال {minutes} دقيقة.",
    "pay.payment_id": "🧾 رقم الدفع: <b>#{id}</b>",
    "pay.order_total": "💎 إجمالي الطلب: <b>{payable} USDT</b>",
    "pay.product_line": "📦 المنتج: {prefix}<b>{name}</b>",
    "pay.qty_line": "🔢 الكمية: <b>{qty}</b>",
    "pay.important": "⚠️ <b>مهم:</b>",
    "pay.send_exact_amount": "• أرسل المبلغ بالضبط كما هو موضّح أعلاه.",
    "pay.exact_warning": "❗️ <b>أرسل هذا المبلغ بالضبط — لا تقم بتقريبه.</b> أي مبلغ مُقرّب أو مختلف قد لا يُضاف تلقائيًا وقد يؤخّر طلبك.",
    "pay.via_binance": "• أرسل عبر تطبيق Binance ← <b>Send</b> ← اختر <b>Binance ID</b>.",
    "pay.use_network": "• استخدم شبكة <b>{net}</b>. الشبكة الخاطئة = قد تُفقد الأموال.",
    "pay.auto_deliver": "🔄 سيتم التحقق من الدفع والتسليم تلقائياً.",
    "pay.disabled": "طريقة الدفع هذه معطّلة حالياً.",
    "pay.user_missing": "المستخدم غير موجود — /start",
    "pay.not_payable": "❌ لم يعد بالإمكان دفع هذا الطلب (مدفوع أو ملغى أو منتهٍ).",
    "pay.insufficient": "❌ رصيد المحفظة غير كافٍ. اختر طريقة عملة رقمية لدفع الطلب مباشرة، أو اشحن عبر الإيداع.",
    "pay.oos": "❌ نفد المخزون قبل التسليم مباشرة. رصيدك لم يتغيّر.",
    "pay.delivered": (
        "✅ <b>تم تسليم الطلب #{id}</b>\n"
        "المبلغ المخصوم: ${charged}\n\n"
        "أرسلت أكوادك بالأسفل.\n\n"
        "🛟 <i>مشكلة في الطلب؟ لا تراسل مباشرة — اضغط 🆘 الإبلاغ عن مشكلة بالأسفل وسنحلّها هنا.</i>"
    ),
    "pay.codes_head": "📜 <b>أكوادك للطلب #{id}:</b>",
    "pay.confirmed_order": "✅ تم تأكيد الدفع — تحقق من رسائلك للتسليم.",
    "pay.confirmed_deposit": "✅ تم تأكيد الإيداع — تمت إضافة الرصيد.",
    "pay.expired": "⏰ انتهت صلاحية هذا الدفع.",
    "pay.cancelled_msg": "❌ تم إلغاء هذا الدفع.",
    "pay.still_pending": "ما زال قيد الانتظار. يتحقق النظام كل {seconds} ثانية.",
    "pay.cancelled_done": "❌ تم إلغاء الدفع.",
    "pay.payment_not_found": "الدفع غير موجود.",
    "pay.order_missing": "الطلب مفقود.",
    "pay.delivered_alert": "تم التسليم!",
    "support.title": "🆘 <b>الدعم</b> 💬",
    "support.with_orders": (
        "تحتاج مساعدة في أحد طلباتك؟\n\n"
        "🎫 <b>افتح تذكرة</b> بخصوص طلب — تُتابَع ولا تُفقد أبداً (مُوصى به).\n"
        "💬 أو راسلنا مباشرة.\n\n"
        "⚡ <i>متوسط الرد: أقل من ساعة خلال ساعات العمل.</i>"
    ),
    "support.no_orders": (
        "تحتاج مساعدة؟ اضغط بالأسفل لمراسلتنا — عادةً نرد خلال ساعات.\n\n"
        "⚡ <i>متوسط الرد: أقل من ساعة خلال ساعات العمل.</i>"
    ),
    "ticket.pick_title": "🆘 <b>الإبلاغ عن مشكلة</b> 🎫\n\nاختر الطلب الذي تحتاج مساعدة بشأنه 👇",
    "ticket.none": "ليس لديك طلبات للإبلاغ عنها بعد.",
    "ticket.invalid": "اختيار غير صالح.",
    "ticket.cap": "لديك عدة تذاكر مفتوحة بالفعل — سنرد عليها أولاً. يرجى انتظار الرد قبل فتح أخرى.",
    "ticket.describe": (
        "🆘 <b>الإبلاغ عن مشكلة</b> 📝\n\n"
        "الطلب <b>#{id}</b> — {name}\n\n"
        "صِف المشكلة في رسالة واحدة (مثلاً: الكود لم يعمل، منتج خاطئ). سيرد عليك فريقنا هنا."
    ),
    "ticket.send_text": "يرجى إرسال مشكلتك كرسالة نصية.",
    "ticket.too_long": "هذا طويل قليلاً — يرجى إبقاؤه أقل من {max} حرف.",
    "ticket.expired": "انتهت الجلسة — افتح الطلب مجدداً للإبلاغ عنه.",
    "ticket.not_yours": "هذا الطلب ليس لك.",
    "ticket.received": (
        "✅ <b>تم استلام التذكرة #{id}</b>\n\n"
        "شكراً — سجّلنا مشكلتك للطلب <b>#{order}</b> وسنرد عليك هنا. 💬"
    ),
    "ticket.reply_dm": "💬 <b>رد على تذكرتك #{id}</b> (الطلب #{order})\n\n{body}",
    "notify.balance": "💰 <b>تحديث الرصيد</b>\n\nالتعديل: {sign}{delta} USDT\nالرصيد الجديد: {balance} USDT",
    "notify.balance_note": "\nملاحظة: {note}",
    "notify.blocked": "🚫 <b>تم تعطيل الشراء</b>\n\nلا يمكنك تقديم طلبات في هذا المتجر. تواصل مع الدعم إذا احتجت مساعدة.",
    "notify.unblocked": "✅ <b>تم استعادة الشراء</b>\n\nيمكنك تقديم الطلبات مرة أخرى.",
    "notify.expired": "⏰ انتهت صلاحية الدفع <b>#{id}</b> قبل استلامه.",
    "notify.refunded": "⚠️ تم استلام الدفع <b>#{id}</b> لكن المخزون نفد. تمت إضافة <b>{amount} {coin}</b> إلى محفظتك كاسترداد.",
    "notify.delivered": (
        "🎉 <b>تم تسليم الطلب!</b> ✨\n"
        "━━━━━━━━━━━━━━━━━\n"
        "📦 المنتج: <b>{name}</b>\n"
        "🆔 الطلب: <b>#{id}</b>\n"
        "💰 المدفوع: <b>{amount} {coin}</b>\n"
        "🔗 TXID: <code>{txid}</code>\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        "🎁 <b>أكوادك:</b>"
    ),
    "notify.help": "🛟 <i>مشكلة في الطلب؟ لا تراسل مباشرة — اضغط 🆘 الإبلاغ عن مشكلة بالأسفل وسنحلّها هنا.</i>",
    "notify.deposit": (
        "💸 <b>تم استلام الإيداع!</b> ✨\n"
        "━━━━━━━━━━━━━━━━━\n"
        "💰 المبلغ: <b>{amount} {coin}</b>\n"
        "🔗 TXID: <code>{txid}</code>\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        "💎 تمت إضافة الرصيد — اكتب /shop لبدء الشراء!"
    ),
    "bc.restock": "🆕 <b>تمت إضافة {n} قطعة جديدة لـ {name}!</b>",
    "bc.available": "📦 المتوفر: <b>{n} قطعة</b>",
    "bc.price": "💲 السعر: <b>${price} USDT</b>",
    "bc.scarcity_left": "⚠️ <b>بقي {n} فقط</b> — تنفد بسرعة.",
    "bc.scarcity_limited": "⚡ <b>كمية محدودة</b> — تنفد بسرعة. احجز نصيبك.",
    "bc.pricedrop": "📉 <b>انخفض السعر!</b>",
    "bc.was": "💲 كان: <s>{old} USDT</s>",
    "bc.now": "🌞 الآن: <b>{new} USDT</b>",
    "bc.limited_offer": "<i>عرض لوقت محدود — اشترِ الآن!</i>",
    "bc.new_product": "🆕 <b>أُضيف منتج جديد!</b>",
    "bc.name": "📦 الاسم: <b>{name}</b>",
    "bc.open_shop": "🌟 <b>افتح المتجر للشراء الآن!</b>",
    "bc.admin_notification": "👁 <b>إشعار من الإدارة:</b>",
}

HI: dict[str, str] = {
    "lang.choose": "🌐 <b>Apni language chuniye</b>\n\nJo language aap use karna chahte hain wo chuniye:",
    "lang.changed": "✅ Language update ho gayi.",
    "lang.invalid": "Invalid language.",
    "delivery.footer": "🌟 <i>Aapki khareedari ke liye shukriya!</i> 💎",
    "delivery.codes_continued": "📜 <b>Aapke codes (continued):</b>",
    "delivery.instructions": "📋 <b>Instructions:</b>\n{body}",
    "btn.shop": "🛍 Shop",
    "btn.wallet": "💰 Wallet",
    "btn.profile": "👤 Meri Profile",
    "btn.support": "🆘 Support",
    "btn.history": "📜 History",
    "btn.language": "🌐 Language",
    "btn.profile_short": "👤 Profile",
    "btn.purchase_history": "📜 Orders",
    "btn.deposit": "💳 Deposit",
    "btn.back_menu": "« Menu par wapas",
    "btn.main_menu": "🏠 Main Menu",
    "btn.back": "Wapas",
    "btn.back_store": "« Store par wapas",
    "btn.buy_now": "🛒 Abhi khariden",
    "btn.custom_amount": "✏️ Custom Amount",
    "btn.cancel": "❌ Cancel",
    "btn.refresh": "🔄 Refresh",
    "btn.prev": "« Pichla",
    "btn.next": "Agla »",
    "btn.report_issue": "🆘 Problem report karein",
    "btn.shop_more": "🛍 Aur shop karein",
    "btn.check_paid": "🔄 Maine pay kar diya — abhi check karein",
    "btn.cancel_payment": "🛑 Payment cancel karein",
    "btn.open_ticket": "🎫 Ticket kholein",
    "btn.message_us": "💬 Message @{user}",
    "btn.pay_wallet": "👛 Wallet se pay karein",
    "btn.page": "Page {page}/{total}",
    "btn.buy_named": "Abhi khariden",
    "btn.stock": "(Stock: {n})",
    "btn.out_of_stock": "(Stock khatam)",
    "start.welcome": (
        "👑 <b>{shop} mein aapka swagat hai!</b> ✨\n\n"
        "Hi <b>{name}</b> ⭐\n\n"
        "🎁 Best price par premium digital products.\n"
        "⚡ Fast, secure aur poori tarah automatic delivery.\n\n"
        "<blockquote>🛍 <b>Shop</b> — Products dekhein aur khariden\n"
        "💰 <b>Wallet</b> — Funds add karein aur balance manage karein\n"
        "👤 <b>Meri Profile</b> — Balance aur orders\n"
        "📜 <b>History</b> — Aapki purani purchases\n"
        "🆘 <b>Support</b> — Madad lein</blockquote>\n\n"
        "Aage badhne ke liye neeche se ek option chuniye! 👇"
    ),
    "shop.list_title": "🛍 <b>Available Products</b> ✨",
    "shop.none": "Abhi koi product available nahi hai. Baad mein check karein!",
    "shop.tap_page": "Neeche kisi product par tap karein (page {page}/{total}):",
    "shop.tap": "Neeche kisi product par tap karein:",
    "shop.card_price": "💲 Price: <b>${price}</b> / {unit}",
    "shop.card_stock": "📦 Stock: <b>{n} available</b>",
    "shop.card_bulk": "📊 <b>Bulk pricing:</b>",
    "shop.card_auto": "⚡ <i>Payment confirm hote hi delivery automatic hai.</i>",
    "shop.buy_header": "<b>{name}</b>\n\n💲 Price: ${price} / {unit}\n\n📌 Aapko kitne {unit} chahiye?",
    "shop.ask_qty": "Aapko jitni quantity chahiye wo bhejein (1-1000):",
    "shop.qty_whole": "1 se 1000 ke beech ek poora number bhejein.",
    "shop.qty_range": "{min} se {max} ke beech quantity chuniye.",
    "shop.only_n": "Abhi sirf {n} stock mein hai. Kam quantity chuniye.",
    "shop.product_gone": "Product ab maujood nahi hai.",
    "shop.unavailable": "Ye product ab available nahi hai.",
    "shop.blocked": "Aap ab orders nahi de sakte. Agar galti lagti hai to support se rabta karein.",
    "shop.rate_limited": "Bahut zyada checkout attempts. Taqreeban {minutes} minute wait karein phir try karein.",
    "shop.not_found": "Product nahi mila.",
    "shop.how_many": "📌 Aapko kitne {unit} chahiye?",
    "sum.qty": "🧮 Quantity: <b>{qty}</b> {unit}",
    "sum.subtotal": "💲 Subtotal: <b>${subtotal}</b>",
    "sum.discount": "🎟 Discount: −${discount}",
    "sum.payable": "💎 Final Payable: <b>${payable}</b>",
    "sum.wallet_admin": "💳 Aapka Wallet: <b>∞ (admin)</b>",
    "sum.wallet": "💳 Aapka Wallet: <b>${balance}</b>",
    "sum.short": "   <i>(wallet payment ke liye ${short} kam hai — neeche crypto method use karein)</i>",
    "sum.choose": "<i>Neeche se payment method chuniye 👇</i>",
    "common.start_first": "Pehle /start type karein.",
    "common.private_only": "Ise use karne ke liye bot ko private chat mein kholein.",
    "common.not_your_order": "Ye order aapka nahi hai.",
    "common.not_your_payment": "Ye payment aapka nahi hai.",
    "common.order_not_found": "Order nahi mila.",
    "common.order_closed": "Ye order ab open nahi hai.",
    "common.cancelled": "❌ Order cancel ho gaya.",
    "profile.title": "💎 <b>Meri Profile</b> ✨",
    "profile.name": "👋 Naam: <b>{name}</b>",
    "profile.tier": "🏅 Tier: <b>{tier}</b>",
    "profile.balance": "💰 Balance: <b>{balance}</b>",
    "profile.spent": "🛍 Total kharch: <b>{spent}</b>",
    "profile.next_tier": "\n🚀 <i><b>{tier}</b> tak pahunchne ke liye {remaining} aur</i>",
    "profile.refreshed": "Profile refresh ho gayi.",
    "tier.Diamond": "Diamond",
    "tier.Platinum": "Platinum",
    "tier.Gold": "Gold",
    "tier.Silver": "Silver",
    "tier.Bronze": "Bronze",
    "status.delivered": "Delivered",
    "status.paid": "Paid",
    "status.awaiting_payment": "Payment ka intezaar",
    "status.draft": "Draft",
    "status.cancelled": "Cancelled",
    "status.expired": "Expired",
    "status.failed": "Failed",
    "history.title": "📜 <b>Purchase history</b> ✨",
    "history.none": "Abhi koi order nahi — kuch lene ke liye 🛍 <b>Shop</b> tap karein!",
    "history.older": "<i>… purane orders chhupe hain ({n} aur)</i>",
    "history.order_num": "<b>Order #{id}</b>",
    "history.delivered_label": "🎁 <b>Delivered:</b>",
    "history.delivered_inline": "🎁 <b>Delivered:</b> <code>{code}</code>",
    "history.more_codes": "   <i>+{n} aur (chat mein bhi bheje gaye)</i>",
    "deposit.wallet_title": "💰 <b>Wallet</b> ✨",
    "deposit.balance_admin": "💎 Current balance: <b>∞ Unlimited</b> (admin)",
    "deposit.balance": "💎 Current balance: <b>{balance}</b>",
    "deposit.tap_deposit": "<i>Top up karne ke liye neeche 💳 <b>Deposit</b> tap karein.</i>",
    "deposit.prompt": "💰 <b>Deposit</b> ⚡\n\n💲 Minimum: <b>{min}</b> USDT\n\n👇 Wallet mein add karne ke liye amount bhejein.",
    "deposit.min_err": "≥ {min} ek valid number bhejein.",
    "deposit.max_err": "Max deposit {max} USDT hai. Chhota amount bhejein.",
    "deposit.start_over": "Deposit se dobara shuru karein.",
    "pay.method_title": "💳 <b>Payment Method</b>",
    "pay.product": "🧾 Product: {name}",
    "pay.quantity": "🔢 Quantity: {qty}",
    "pay.subtotal": "💲 Subtotal: {subtotal}",
    "pay.discount": "🎟 Discount: −{discount}",
    "pay.payable": "💎 Final Payable: {payable}",
    "pay.wallet": "💰 Aapka Wallet: {balance}",
    "pay.topup_label": "💳 Wallet Top-up",
    "pay.amount": "💵 Amount: {amount}",
    "topup.title": "{method} — Wallet top-up",
    "pay.auto": "🔄 Auto-verification on hai",
    "pay.send_exact": "📤 <b>Bilkul itna bhejein: {amount} {coin}</b>",
    "pay.uid": "🆔 Binance UID: <code>{uid}</code>",
    "pay.address": "🏦 Address:\n<code>{address}</code>",
    "pay.expires": "⏳ {minutes} minute mein expire ho jayega.",
    "pay.payment_id": "🧾 Payment ID: <b>#{id}</b>",
    "pay.order_total": "💎 Order Total: <b>{payable} USDT</b>",
    "pay.product_line": "📦 Product: {prefix}<b>{name}</b>",
    "pay.qty_line": "🔢 Quantity: <b>{qty}</b>",
    "pay.important": "⚠️ <b>Zaroori:</b>",
    "pay.send_exact_amount": "• Upar dikhaya gaya exact amount hi bhejein.",
    "pay.exact_warning": "❗️ <b>Bilkul yahi EXACT amount bhejein — round mat karein.</b> Round ya alag amount auto-credit nahi ho sakta aur aapka order delay ho sakta hai.",
    "pay.via_binance": "• Binance app se bhejein → <b>Send</b> → <b>Binance ID</b> chuniye.",
    "pay.use_network": "• <b>{net}</b> network use karein. Galat network = paisa doob sakta hai.",
    "pay.auto_deliver": "🔄 Payment verify hote hi automatic deliver hoga.",
    "pay.disabled": "Ye payment method abhi disabled hai.",
    "pay.user_missing": "User missing — /start",
    "pay.not_payable": "❌ Ye order ab payable nahi hai (already paid, cancelled, ya expired).",
    "pay.insufficient": "❌ Wallet balance kam hai. Order ko direct pay karne ke liye crypto method chuniye, ya Deposit se top up karein.",
    "pay.oos": "❌ Delivery se thoda pehle stock khatam ho gaya. Aapka balance same hai.",
    "pay.delivered": (
        "✅ <b>Order #{id} delivered</b>\n"
        "Charged: ${charged}\n\n"
        "Aapke codes neeche bheje hain.\n\n"
        "🛟 <i>Order mein problem? DM mat karein — neeche 🆘 Problem report karein tap karein, hum yahin theek kar denge.</i>"
    ),
    "pay.codes_head": "📜 <b>Order #{id} ke aapke codes:</b>",
    "pay.confirmed_order": "✅ Payment confirm — delivery ke liye apne messages check karein.",
    "pay.confirmed_deposit": "✅ Deposit confirm — balance add ho gaya.",
    "pay.expired": "⏰ Ye payment expire ho gaya.",
    "pay.cancelled_msg": "❌ Ye payment cancel ho gaya.",
    "pay.still_pending": "Abhi pending hai. Auto-poller har {seconds}s check karta hai.",
    "pay.cancelled_done": "❌ Payment cancel ho gaya.",
    "pay.payment_not_found": "Payment nahi mila.",
    "pay.order_missing": "Order nahi mila.",
    "pay.delivered_alert": "Deliver ho gaya!",
    "support.title": "🆘 <b>Support</b> 💬",
    "support.with_orders": (
        "Apne kisi order mein madad chahiye?\n\n"
        "🎫 <b>Ticket kholein</b> kisi order ke liye — track hota hai, kabhi miss nahi hota (recommended).\n"
        "💬 Ya humein direct message karein.\n\n"
        "⚡ <i>Average response: business hours mein 1 ghante ke andar.</i>"
    ),
    "support.no_orders": (
        "Madad chahiye? Humein message karne ke liye neeche tap karein — aksar kuch ghanton mein reply.\n\n"
        "⚡ <i>Average response: business hours mein 1 ghante ke andar.</i>"
    ),
    "ticket.pick_title": "🆘 <b>Problem report karein</b> 🎫\n\nJis order mein madad chahiye wo chuniye 👇",
    "ticket.none": "Abhi report karne ke liye koi order nahi hai.",
    "ticket.invalid": "Invalid selection.",
    "ticket.cap": "Aapke pehle se kai open tickets hain — hum pehle un par reply karenge. Naya kholne se pehle reply ka intezaar karein.",
    "ticket.describe": (
        "🆘 <b>Problem report karein</b> 📝\n\n"
        "Order <b>#{id}</b> — {name}\n\n"
        "Problem ek message mein batayein (jaise code kaam nahi kiya, galat item). Hamari team yahin reply karegi."
    ),
    "ticket.send_text": "Apni problem text message ke roop mein bhejein.",
    "ticket.too_long": "Thoda lamba hai — kripya {max} characters se kam rakhein.",
    "ticket.expired": "Session expire ho gaya — report karne ke liye order dobara kholein.",
    "ticket.not_yours": "Ye order aapka nahi hai.",
    "ticket.received": (
        "✅ <b>Ticket #{id} mil gaya</b>\n\n"
        "Shukriya — order <b>#{order}</b> ke liye aapki problem note kar li, hum yahin reply karenge. 💬"
    ),
    "ticket.reply_dm": "💬 <b>Aapki ticket #{id} ka reply</b> (order #{order})\n\n{body}",
    "notify.balance": "💰 <b>Balance update</b>\n\nAdjustment: {sign}{delta} USDT\nNaya balance: {balance} USDT",
    "notify.balance_note": "\nNote: {note}",
    "notify.blocked": "🚫 <b>Kharidari band</b>\n\nAap is shop se order nahi kar sakte. Madad ke liye support se rabta karein.",
    "notify.unblocked": "✅ <b>Kharidari dobara</b>\n\nAap phir se order kar sakte hain.",
    "notify.expired": "⏰ Payment <b>#{id}</b> milne se pehle expire ho gaya.",
    "notify.refunded": "⚠️ Payment <b>#{id}</b> mila lekin stock khatam ho gaya. <b>{amount} {coin}</b> refund ke roop mein aapke wallet mein add kiya gaya.",
    "notify.delivered": (
        "🎉 <b>Order Delivered!</b> ✨\n"
        "━━━━━━━━━━━━━━━━━\n"
        "📦 Product: <b>{name}</b>\n"
        "🆔 Order: <b>#{id}</b>\n"
        "💰 Paid: <b>{amount} {coin}</b>\n"
        "🔗 TXID: <code>{txid}</code>\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        "🎁 <b>Aapke codes:</b>"
    ),
    "notify.help": "🛟 <i>Order mein problem? DM mat karein — neeche 🆘 Problem report karein tap karein, hum yahin theek kar denge.</i>",
    "notify.deposit": (
        "💸 <b>Deposit mil gaya!</b> ✨\n"
        "━━━━━━━━━━━━━━━━━\n"
        "💰 Amount: <b>{amount} {coin}</b>\n"
        "🔗 TXID: <code>{txid}</code>\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        "💎 Balance add ho gaya — kharidari shuru karne ke liye /shop tap karein!"
    ),
    "bc.restock": "🆕 <b>{name} ke liye {n} naya stock add hua!</b>",
    "bc.available": "📦 Available: <b>{n} items</b>",
    "bc.price": "💲 Price: <b>${price} USDT</b>",
    "bc.scarcity_left": "⚠️ <b>Sirf {n} bache</b> — jaldi khatam hote hain.",
    "bc.scarcity_limited": "⚡ <b>Limited drop</b> — jaldi jate hain. Apna abhi lock karein.",
    "bc.pricedrop": "📉 <b>Price Drop!</b>",
    "bc.was": "💲 Tha: <s>{old} USDT</s>",
    "bc.now": "🌞 Ab: <b>{new} USDT</b>",
    "bc.limited_offer": "<i>Limited time offer — abhi khariden!</i>",
    "bc.new_product": "🆕 <b>NAYA PRODUCT ADD HUA!</b>",
    "bc.name": "📦 Naam: <b>{name}</b>",
    "bc.open_shop": "🌟 <b>Abhi kharidne ke liye Shop kholein!</b>",
    "bc.admin_notification": "👁 <b>Admin Notification:</b>",
}

VI: dict[str, str] = {
    "lang.choose": "🌐 <b>Chọn ngôn ngữ của bạn</b>\n\nChọn ngôn ngữ bạn muốn dùng:",
    "lang.changed": "✅ Đã cập nhật ngôn ngữ.",
    "lang.invalid": "Ngôn ngữ không hợp lệ.",
    "delivery.footer": "🌟 <i>Cảm ơn bạn đã mua hàng!</i> 💎",
    "delivery.codes_continued": "📜 <b>Mã của bạn (tiếp tục):</b>",
    "delivery.instructions": "📋 <b>Hướng dẫn:</b>\n{body}",
    "btn.shop": "🛍 Cửa hàng",
    "btn.wallet": "💰 Ví",
    "btn.profile": "👤 Hồ sơ",
    "btn.support": "🆘 Hỗ trợ",
    "btn.history": "📜 Lịch sử",
    "btn.language": "🌐 Ngôn ngữ",
    "btn.profile_short": "👤 Hồ sơ",
    "btn.purchase_history": "📜 Đơn hàng",
    "btn.deposit": "💳 Nạp tiền",
    "btn.back_menu": "« Về menu",
    "btn.main_menu": "🏠 Menu chính",
    "btn.back": "Quay lại",
    "btn.back_store": "« Về cửa hàng",
    "btn.buy_now": "🛒 Mua ngay",
    "btn.custom_amount": "✏️ Số lượng tùy chọn",
    "btn.cancel": "❌ Hủy",
    "btn.refresh": "🔄 Làm mới",
    "btn.prev": "« Trước",
    "btn.next": "Sau »",
    "btn.report_issue": "🆘 Báo lỗi",
    "btn.shop_more": "🛍 Mua thêm",
    "btn.check_paid": "🔄 Tôi đã thanh toán — kiểm tra ngay",
    "btn.cancel_payment": "🛑 Hủy thanh toán",
    "btn.open_ticket": "🎫 Mở yêu cầu hỗ trợ",
    "btn.message_us": "💬 Nhắn tin @{user}",
    "btn.pay_wallet": "👛 Thanh toán bằng Ví",
    "btn.page": "Trang {page}/{total}",
    "btn.buy_named": "Mua ngay",
    "btn.stock": "(Còn: {n})",
    "btn.out_of_stock": "(Hết hàng)",
    "start.welcome": (
        "👑 <b>Chào mừng đến {shop}!</b> ✨\n\n"
        "Chào <b>{name}</b> ⭐\n\n"
        "🎁 Sản phẩm số cao cấp với giá tốt nhất.\n"
        "⚡ Giao hàng nhanh, an toàn và hoàn toàn tự động.\n\n"
        "<blockquote>🛍 <b>Cửa hàng</b> — Xem và mua sản phẩm\n"
        "💰 <b>Ví</b> — Nạp tiền và quản lý số dư\n"
        "👤 <b>Hồ sơ</b> — Số dư và đơn hàng\n"
        "📜 <b>Lịch sử</b> — Các lần mua trước\n"
        "🆘 <b>Hỗ trợ</b> — Nhận trợ giúp</blockquote>\n\n"
        "Chọn một mục bên dưới để tiếp tục! 👇"
    ),
    "shop.list_title": "🛍 <b>Sản phẩm có sẵn</b> ✨",
    "shop.none": "Chưa có sản phẩm nào. Hãy quay lại sau!",
    "shop.tap_page": "Chạm vào một sản phẩm bên dưới (trang {page}/{total}):",
    "shop.tap": "Chạm vào một sản phẩm bên dưới:",
    "shop.card_price": "💲 Giá: <b>${price}</b> / {unit}",
    "shop.card_stock": "📦 Kho: <b>còn {n}</b>",
    "shop.card_bulk": "📊 <b>Giá theo số lượng:</b>",
    "shop.card_auto": "⚡ <i>Giao hàng tự động sau khi xác nhận thanh toán.</i>",
    "shop.buy_header": "<b>{name}</b>\n\n💲 Giá: ${price} / {unit}\n\n📌 Bạn muốn mua bao nhiêu {unit}?",
    "shop.ask_qty": "Nhập số lượng bạn muốn (1-1000):",
    "shop.qty_whole": "Gửi một số nguyên từ 1 đến 1000.",
    "shop.qty_range": "Chọn số lượng từ {min} đến {max}.",
    "shop.only_n": "Hiện chỉ còn {n}. Hãy chọn số lượng ít hơn.",
    "shop.product_gone": "Sản phẩm không còn tồn tại.",
    "shop.unavailable": "Sản phẩm này không còn khả dụng.",
    "shop.blocked": "Bạn không thể đặt hàng lúc này. Liên hệ hỗ trợ nếu bạn cho rằng đây là nhầm lẫn.",
    "shop.rate_limited": "Quá nhiều lần tạo đơn. Vui lòng chờ khoảng {minutes} phút rồi thử lại.",
    "shop.not_found": "Không tìm thấy sản phẩm.",
    "shop.how_many": "📌 Bạn muốn mua bao nhiêu {unit}?",
    "sum.qty": "🧮 Số lượng: <b>{qty}</b> {unit}",
    "sum.subtotal": "💲 Tạm tính: <b>${subtotal}</b>",
    "sum.discount": "🎟 Giảm giá: −${discount}",
    "sum.payable": "💎 Phải trả: <b>${payable}</b>",
    "sum.wallet_admin": "💳 Ví của bạn: <b>∞ (admin)</b>",
    "sum.wallet": "💳 Ví của bạn: <b>${balance}</b>",
    "sum.short": "   <i>(thiếu ${short} để trả bằng ví — dùng phương thức crypto bên dưới)</i>",
    "sum.choose": "<i>Chọn phương thức thanh toán bên dưới 👇</i>",
    "common.start_first": "Hãy gõ /start trước.",
    "common.private_only": "Hãy mở bot trong cuộc trò chuyện riêng để dùng tính năng này.",
    "common.not_your_order": "Đây không phải đơn hàng của bạn.",
    "common.not_your_payment": "Đây không phải thanh toán của bạn.",
    "common.order_not_found": "Không tìm thấy đơn hàng.",
    "common.order_closed": "Đơn hàng không còn mở.",
    "common.cancelled": "❌ Đã hủy đơn hàng.",
    "profile.title": "💎 <b>Hồ sơ của tôi</b> ✨",
    "profile.name": "👋 Tên: <b>{name}</b>",
    "profile.tier": "🏅 Hạng: <b>{tier}</b>",
    "profile.balance": "💰 Số dư: <b>{balance}</b>",
    "profile.spent": "🛍 Tổng chi tiêu: <b>{spent}</b>",
    "profile.next_tier": "\n🚀 <i>Cần thêm {remaining} để đạt <b>{tier}</b></i>",
    "profile.refreshed": "Đã làm mới hồ sơ.",
    "tier.Diamond": "Kim cương",
    "tier.Platinum": "Bạch kim",
    "tier.Gold": "Vàng",
    "tier.Silver": "Bạc",
    "tier.Bronze": "Đồng",
    "status.delivered": "Đã giao",
    "status.paid": "Đã thanh toán",
    "status.awaiting_payment": "Chờ thanh toán",
    "status.draft": "Nháp",
    "status.cancelled": "Đã hủy",
    "status.expired": "Hết hạn",
    "status.failed": "Thất bại",
    "history.title": "📜 <b>Lịch sử mua hàng</b> ✨",
    "history.none": "Chưa có đơn nào — chạm 🛍 <b>Cửa hàng</b> để mua sắm!",
    "history.older": "<i>… đã ẩn các đơn cũ hơn (còn {n})</i>",
    "history.order_num": "<b>Đơn #{id}</b>",
    "history.delivered_label": "🎁 <b>Đã giao:</b>",
    "history.delivered_inline": "🎁 <b>Đã giao:</b> <code>{code}</code>",
    "history.more_codes": "   <i>+{n} nữa (cũng đã gửi cho bạn trong chat)</i>",
    "deposit.wallet_title": "💰 <b>Ví</b> ✨",
    "deposit.balance_admin": "💎 Số dư hiện tại: <b>∞ Không giới hạn</b> (admin)",
    "deposit.balance": "💎 Số dư hiện tại: <b>{balance}</b>",
    "deposit.tap_deposit": "<i>Chạm 💳 <b>Nạp tiền</b> bên dưới để nạp.</i>",
    "deposit.prompt": "💰 <b>Nạp tiền</b> ⚡\n\n💲 Tối thiểu: <b>{min}</b> USDT\n\n👇 Nhập số tiền bạn muốn thêm vào ví.",
    "deposit.min_err": "Hãy gửi một số hợp lệ ≥ {min}.",
    "deposit.max_err": "Nạp tối đa là {max} USDT. Hãy gửi số nhỏ hơn.",
    "deposit.start_over": "Hãy bắt đầu lại qua Nạp tiền.",
    "pay.method_title": "💳 <b>Phương thức thanh toán</b>",
    "pay.product": "🧾 Sản phẩm: {name}",
    "pay.quantity": "🔢 Số lượng: {qty}",
    "pay.subtotal": "💲 Tạm tính: {subtotal}",
    "pay.discount": "🎟 Giảm giá: −{discount}",
    "pay.payable": "💎 Phải trả: {payable}",
    "pay.wallet": "💰 Ví của bạn: {balance}",
    "pay.topup_label": "💳 Nạp ví",
    "pay.amount": "💵 Số tiền: {amount}",
    "topup.title": "{method} — Nạp ví",
    "pay.auto": "🔄 Đã bật tự động xác minh",
    "pay.send_exact": "📤 <b>Gửi chính xác: {amount} {coin}</b>",
    "pay.uid": "🆔 Binance UID: <code>{uid}</code>",
    "pay.address": "🏦 Địa chỉ:\n<code>{address}</code>",
    "pay.expires": "⏳ Hết hạn sau {minutes} phút.",
    "pay.payment_id": "🧾 Mã thanh toán: <b>#{id}</b>",
    "pay.order_total": "💎 Tổng đơn: <b>{payable} USDT</b>",
    "pay.product_line": "📦 Sản phẩm: {prefix}<b>{name}</b>",
    "pay.qty_line": "🔢 Số lượng: <b>{qty}</b>",
    "pay.important": "⚠️ <b>Quan trọng:</b>",
    "pay.send_exact_amount": "• Gửi đúng số tiền hiển thị ở trên.",
    "pay.exact_warning": "❗️ <b>Gửi CHÍNH XÁC số tiền này — đừng làm tròn.</b> Số tiền làm tròn hoặc khác có thể không được tự động cộng và làm chậm đơn hàng của bạn.",
    "pay.via_binance": "• Gửi qua app Binance → <b>Send</b> → chọn <b>Binance ID</b>.",
    "pay.use_network": "• Dùng mạng <b>{net}</b>. Sai mạng = có thể mất tiền.",
    "pay.auto_deliver": "🔄 Thanh toán sẽ được xác minh và giao tự động.",
    "pay.disabled": "Phương thức thanh toán này hiện đang tắt.",
    "pay.user_missing": "Thiếu người dùng — /start",
    "pay.not_payable": "❌ Đơn này không còn thanh toán được (đã trả, đã hủy hoặc hết hạn).",
    "pay.insufficient": "❌ Số dư ví không đủ. Hãy chọn phương thức crypto để trả trực tiếp, hoặc nạp qua Nạp tiền.",
    "pay.oos": "❌ Hết hàng ngay trước khi giao. Số dư của bạn không đổi.",
    "pay.delivered": (
        "✅ <b>Đơn #{id} đã giao</b>\n"
        "Đã trừ: ${charged}\n\n"
        "Tôi đã gửi mã của bạn bên dưới.\n\n"
        "🛟 <i>Có vấn đề với đơn này? Đừng nhắn riêng — chạm 🆘 Báo lỗi bên dưới, chúng tôi sẽ xử lý ngay tại đây.</i>"
    ),
    "pay.codes_head": "📜 <b>Mã của bạn cho đơn #{id}:</b>",
    "pay.confirmed_order": "✅ Đã xác nhận thanh toán — kiểm tra tin nhắn để nhận hàng.",
    "pay.confirmed_deposit": "✅ Đã xác nhận nạp tiền — số dư đã được cộng.",
    "pay.expired": "⏰ Thanh toán này đã hết hạn.",
    "pay.cancelled_msg": "❌ Thanh toán này đã bị hủy.",
    "pay.still_pending": "Vẫn đang chờ. Hệ thống tự kiểm tra mỗi {seconds}s.",
    "pay.cancelled_done": "❌ Đã hủy thanh toán.",
    "pay.payment_not_found": "Không tìm thấy thanh toán.",
    "pay.order_missing": "Thiếu đơn hàng.",
    "pay.delivered_alert": "Đã giao!",
    "support.title": "🆘 <b>Hỗ trợ</b> 💬",
    "support.with_orders": (
        "Cần trợ giúp với một đơn hàng của bạn?\n\n"
        "🎫 <b>Mở yêu cầu hỗ trợ</b> cho một đơn — được theo dõi và không bao giờ bị bỏ sót (khuyến nghị).\n"
        "💬 Hoặc nhắn tin trực tiếp cho chúng tôi.\n\n"
        "⚡ <i>Phản hồi trung bình: dưới 1 giờ trong giờ làm việc.</i>"
    ),
    "support.no_orders": (
        "Cần trợ giúp? Chạm bên dưới để nhắn cho chúng tôi — thường trả lời trong vài giờ.\n\n"
        "⚡ <i>Phản hồi trung bình: dưới 1 giờ trong giờ làm việc.</i>"
    ),
    "ticket.pick_title": "🆘 <b>Báo lỗi</b> 🎫\n\nChọn đơn bạn cần trợ giúp 👇",
    "ticket.none": "Bạn chưa có đơn nào để báo lỗi.",
    "ticket.invalid": "Lựa chọn không hợp lệ.",
    "ticket.cap": "Bạn đã có vài yêu cầu đang mở — chúng tôi sẽ trả lời những cái đó trước. Vui lòng đợi phản hồi trước khi mở cái mới.",
    "ticket.describe": (
        "🆘 <b>Báo lỗi</b> 📝\n\n"
        "Đơn <b>#{id}</b> — {name}\n\n"
        "Hãy mô tả vấn đề trong một tin nhắn (vd: mã không dùng được, sai sản phẩm). Đội ngũ của chúng tôi sẽ trả lời ngay tại đây."
    ),
    "ticket.send_text": "Vui lòng gửi vấn đề của bạn dưới dạng tin nhắn văn bản.",
    "ticket.too_long": "Hơi dài — vui lòng giữ dưới {max} ký tự.",
    "ticket.expired": "Phiên đã hết — mở lại đơn để báo lỗi.",
    "ticket.not_yours": "Đơn này không phải của bạn.",
    "ticket.received": (
        "✅ <b>Đã nhận yêu cầu #{id}</b>\n\n"
        "Cảm ơn — chúng tôi đã ghi nhận vấn đề cho đơn <b>#{order}</b> và sẽ trả lời bạn tại đây. 💬"
    ),
    "ticket.reply_dm": "💬 <b>Phản hồi cho yêu cầu #{id}</b> (đơn #{order})\n\n{body}",
    "notify.balance": "💰 <b>Cập nhật số dư</b>\n\nĐiều chỉnh: {sign}{delta} USDT\nSố dư mới: {balance} USDT",
    "notify.balance_note": "\nGhi chú: {note}",
    "notify.blocked": "🚫 <b>Đã khóa mua hàng</b>\n\nBạn không thể đặt hàng tại cửa hàng này. Liên hệ hỗ trợ nếu cần.",
    "notify.unblocked": "✅ <b>Đã mở lại mua hàng</b>\n\nBạn có thể đặt hàng lại.",
    "notify.expired": "⏰ Thanh toán <b>#{id}</b> đã hết hạn trước khi nhận được.",
    "notify.refunded": "⚠️ Đã nhận thanh toán <b>#{id}</b> nhưng hết hàng. <b>{amount} {coin}</b> đã được hoàn vào ví của bạn.",
    "notify.delivered": (
        "🎉 <b>Đơn đã giao!</b> ✨\n"
        "━━━━━━━━━━━━━━━━━\n"
        "📦 Sản phẩm: <b>{name}</b>\n"
        "🆔 Đơn: <b>#{id}</b>\n"
        "💰 Đã trả: <b>{amount} {coin}</b>\n"
        "🔗 TXID: <code>{txid}</code>\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        "🎁 <b>Mã của bạn:</b>"
    ),
    "notify.help": "🛟 <i>Có vấn đề với đơn này? Đừng nhắn riêng — chạm 🆘 Báo lỗi bên dưới, chúng tôi sẽ xử lý ngay tại đây.</i>",
    "notify.deposit": (
        "💸 <b>Đã nhận nạp tiền!</b> ✨\n"
        "━━━━━━━━━━━━━━━━━\n"
        "💰 Số tiền: <b>{amount} {coin}</b>\n"
        "🔗 TXID: <code>{txid}</code>\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        "💎 Đã cộng số dư — gõ /shop để bắt đầu mua!"
    ),
    "bc.restock": "🆕 <b>Đã thêm {n} hàng mới cho {name}!</b>",
    "bc.available": "📦 Còn: <b>{n} sản phẩm</b>",
    "bc.price": "💲 Giá: <b>${price} USDT</b>",
    "bc.scarcity_left": "⚠️ <b>Chỉ còn {n}</b> — bán rất nhanh.",
    "bc.scarcity_limited": "⚡ <b>Số lượng giới hạn</b> — hết nhanh. Giữ ngay phần của bạn.",
    "bc.pricedrop": "📉 <b>Giảm giá!</b>",
    "bc.was": "💲 Trước: <s>{old} USDT</s>",
    "bc.now": "🌞 Nay: <b>{new} USDT</b>",
    "bc.limited_offer": "<i>Ưu đãi có hạn — mua ngay!</i>",
    "bc.new_product": "🆕 <b>SẢN PHẨM MỚI!</b>",
    "bc.name": "📦 Tên: <b>{name}</b>",
    "bc.open_shop": "🌟 <b>Mở Cửa hàng để mua ngay!</b>",
    "bc.admin_notification": "👁 <b>Thông báo từ quản trị:</b>",
}

ZH: dict[str, str] = {
    "lang.choose": "🌐 <b>选择您的语言</b>\n\n请选择您想使用的语言：",
    "lang.changed": "✅ 语言已更新。",
    "lang.invalid": "无效的语言。",
    "delivery.footer": "🌟 <i>感谢您的购买！</i> 💎",
    "delivery.codes_continued": "📜 <b>您的代码（续）：</b>",
    "delivery.instructions": "📋 <b>使用说明：</b>\n{body}",
    "btn.shop": "🛍 商店",
    "btn.wallet": "💰 钱包",
    "btn.profile": "👤 我的资料",
    "btn.support": "🆘 客服",
    "btn.history": "📜 历史",
    "btn.language": "🌐 语言",
    "btn.profile_short": "👤 资料",
    "btn.purchase_history": "📜 订单",
    "btn.deposit": "💳 充值",
    "btn.back_menu": "« 返回菜单",
    "btn.main_menu": "🏠 主菜单",
    "btn.back": "返回",
    "btn.back_store": "« 返回商店",
    "btn.buy_now": "🛒 立即购买",
    "btn.custom_amount": "✏️ 自定义数量",
    "btn.cancel": "❌ 取消",
    "btn.refresh": "🔄 刷新",
    "btn.prev": "« 上一页",
    "btn.next": "下一页 »",
    "btn.report_issue": "🆘 反馈问题",
    "btn.shop_more": "🛍 继续购物",
    "btn.check_paid": "🔄 我已付款 — 立即查询",
    "btn.cancel_payment": "🛑 取消付款",
    "btn.open_ticket": "🎫 创建工单",
    "btn.message_us": "💬 联系 @{user}",
    "btn.pay_wallet": "👛 用钱包支付",
    "btn.page": "第 {page}/{total} 页",
    "btn.buy_named": "立即购买",
    "btn.stock": "(库存：{n})",
    "btn.out_of_stock": "(已售罄)",
    "start.welcome": (
        "👑 <b>欢迎来到 {shop}！</b> ✨\n\n"
        "你好 <b>{name}</b> ⭐\n\n"
        "🎁 最优价格的优质数字产品。\n"
        "⚡ 快速、安全、全自动发货。\n\n"
        "<blockquote>🛍 <b>商店</b> — 浏览并购买产品\n"
        "💰 <b>钱包</b> — 充值并管理余额\n"
        "👤 <b>我的资料</b> — 余额与订单\n"
        "📜 <b>历史</b> — 您过去的购买\n"
        "🆘 <b>客服</b> — 获取帮助</blockquote>\n\n"
        "请选择下方选项继续！👇"
    ),
    "shop.list_title": "🛍 <b>在售产品</b> ✨",
    "shop.none": "暂无产品，请稍后再来！",
    "shop.tap_page": "点击下方产品（第 {page}/{total} 页）：",
    "shop.tap": "点击下方产品：",
    "shop.card_price": "💲 价格：<b>${price}</b> / {unit}",
    "shop.card_stock": "📦 库存：<b>{n} 件可用</b>",
    "shop.card_bulk": "📊 <b>批量价格：</b>",
    "shop.card_auto": "⚡ <i>付款确认后自动发货。</i>",
    "shop.buy_header": "<b>{name}</b>\n\n💲 价格：${price} / {unit}\n\n📌 您要买多少 {unit}？",
    "shop.ask_qty": "请回复您想要的数量（1-1000）：",
    "shop.qty_whole": "请发送 1 到 1000 之间的整数。",
    "shop.qty_range": "请选择 {min} 到 {max} 之间的数量。",
    "shop.only_n": "现在仅剩 {n} 件，请选择更少的数量。",
    "shop.product_gone": "产品已不存在。",
    "shop.unavailable": "该产品已不可用。",
    "shop.blocked": "您目前无法下单。如认为有误，请联系客服。",
    "shop.rate_limited": "结账尝试过多。请等待约 {minutes} 分钟后再试。",
    "shop.not_found": "未找到产品。",
    "shop.how_many": "📌 您要买多少 {unit}？",
    "sum.qty": "🧮 数量：<b>{qty}</b> {unit}",
    "sum.subtotal": "💲 小计：<b>${subtotal}</b>",
    "sum.discount": "🎟 折扣：−${discount}",
    "sum.payable": "💎 应付：<b>${payable}</b>",
    "sum.wallet_admin": "💳 您的钱包：<b>∞（管理员）</b>",
    "sum.wallet": "💳 您的钱包：<b>${balance}</b>",
    "sum.short": "   <i>（钱包支付还差 ${short} — 请使用下方加密方式）</i>",
    "sum.choose": "<i>请选择下方付款方式 👇</i>",
    "common.start_first": "请先输入 /start。",
    "common.private_only": "请在私聊中打开机器人以使用此功能。",
    "common.not_your_order": "这不是您的订单。",
    "common.not_your_payment": "这不是您的付款。",
    "common.order_not_found": "未找到订单。",
    "common.order_closed": "订单已不再开放。",
    "common.cancelled": "❌ 订单已取消。",
    "profile.title": "💎 <b>我的资料</b> ✨",
    "profile.name": "👋 名称：<b>{name}</b>",
    "profile.tier": "🏅 等级：<b>{tier}</b>",
    "profile.balance": "💰 余额：<b>{balance}</b>",
    "profile.spent": "🛍 累计消费：<b>{spent}</b>",
    "profile.next_tier": "\n🚀 <i>再消费 {remaining} 即可达到 <b>{tier}</b></i>",
    "profile.refreshed": "资料已刷新。",
    "tier.Diamond": "钻石",
    "tier.Platinum": "铂金",
    "tier.Gold": "黄金",
    "tier.Silver": "白银",
    "tier.Bronze": "青铜",
    "status.delivered": "已发货",
    "status.paid": "已付款",
    "status.awaiting_payment": "待付款",
    "status.draft": "草稿",
    "status.cancelled": "已取消",
    "status.expired": "已过期",
    "status.failed": "失败",
    "history.title": "📜 <b>购买历史</b> ✨",
    "history.none": "还没有订单 — 点击 🛍 <b>商店</b> 来选购吧！",
    "history.older": "<i>… 较早的订单已隐藏（还有 {n} 个）</i>",
    "history.order_num": "<b>订单 #{id}</b>",
    "history.delivered_label": "🎁 <b>已发货：</b>",
    "history.delivered_inline": "🎁 <b>已发货：</b> <code>{code}</code>",
    "history.more_codes": "   <i>+{n} 更多（也已在聊天中发给您）</i>",
    "deposit.wallet_title": "💰 <b>钱包</b> ✨",
    "deposit.balance_admin": "💎 当前余额：<b>∞ 无限</b>（管理员）",
    "deposit.balance": "💎 当前余额：<b>{balance}</b>",
    "deposit.tap_deposit": "<i>点击下方 💳 <b>充值</b> 来充值。</i>",
    "deposit.prompt": "💰 <b>充值</b> ⚡\n\n💲 最低：<b>{min}</b> USDT\n\n👇 请回复您想充入钱包的金额。",
    "deposit.min_err": "请发送一个 ≥ {min} 的有效数字。",
    "deposit.max_err": "最高充值为 {max} USDT，请发送更小的金额。",
    "deposit.start_over": "请通过充值重新开始。",
    "pay.method_title": "💳 <b>付款方式</b>",
    "pay.product": "🧾 产品：{name}",
    "pay.quantity": "🔢 数量：{qty}",
    "pay.subtotal": "💲 小计：{subtotal}",
    "pay.discount": "🎟 折扣：−{discount}",
    "pay.payable": "💎 应付：{payable}",
    "pay.wallet": "💰 您的钱包：{balance}",
    "pay.topup_label": "💳 钱包充值",
    "pay.amount": "💵 金额：{amount}",
    "topup.title": "{method} — 钱包充值",
    "pay.auto": "🔄 已启用自动验证",
    "pay.send_exact": "📤 <b>请精确发送：{amount} {coin}</b>",
    "pay.uid": "🆔 Binance UID：<code>{uid}</code>",
    "pay.address": "🏦 地址：\n<code>{address}</code>",
    "pay.expires": "⏳ {minutes} 分钟后过期。",
    "pay.payment_id": "🧾 付款编号：<b>#{id}</b>",
    "pay.order_total": "💎 订单总额：<b>{payable} USDT</b>",
    "pay.product_line": "📦 产品：{prefix}<b>{name}</b>",
    "pay.qty_line": "🔢 数量：<b>{qty}</b>",
    "pay.important": "⚠️ <b>重要：</b>",
    "pay.send_exact_amount": "• 请精确发送上方显示的金额。",
    "pay.exact_warning": "❗️ <b>请精确发送此金额 — 不要凑整。</b> 凑整或不同的金额可能无法自动到账，并会延误您的订单。",
    "pay.via_binance": "• 通过 Binance 应用发送 → <b>Send</b> → 选择 <b>Binance ID</b>。",
    "pay.use_network": "• 请使用 <b>{net}</b> 网络。网络错误 = 资金可能丢失。",
    "pay.auto_deliver": "🔄 付款将被自动验证并发货。",
    "pay.disabled": "该付款方式当前已停用。",
    "pay.user_missing": "用户缺失 — /start",
    "pay.not_payable": "❌ 此订单已无法付款（已付款、已取消或已过期）。",
    "pay.insufficient": "❌ 钱包余额不足。请选择加密方式直接支付，或通过充值补充。",
    "pay.oos": "❌ 发货前库存刚好售罄，您的余额未变动。",
    "pay.delivered": (
        "✅ <b>订单 #{id} 已发货</b>\n"
        "已扣：${charged}\n\n"
        "我已在下方发送您的代码。\n\n"
        "🛟 <i>订单有问题？请勿私信 — 点击下方 🆘 反馈问题，我们会在这里为您解决。</i>"
    ),
    "pay.codes_head": "📜 <b>订单 #{id} 的代码：</b>",
    "pay.confirmed_order": "✅ 付款已确认 — 请查看消息以接收发货。",
    "pay.confirmed_deposit": "✅ 充值已确认 — 余额已到账。",
    "pay.expired": "⏰ 此付款已过期。",
    "pay.cancelled_msg": "❌ 此付款已取消。",
    "pay.still_pending": "仍在等待中。系统每 {seconds} 秒自动查询一次。",
    "pay.cancelled_done": "❌ 付款已取消。",
    "pay.payment_not_found": "未找到付款。",
    "pay.order_missing": "订单缺失。",
    "pay.delivered_alert": "已发货！",
    "support.title": "🆘 <b>客服</b> 💬",
    "support.with_orders": (
        "需要某个订单的帮助吗？\n\n"
        "🎫 <b>创建工单</b>（针对某个订单）— 可追踪、绝不遗漏（推荐）。\n"
        "💬 或直接联系我们。\n\n"
        "⚡ <i>平均响应：工作时间内 1 小时内。</i>"
    ),
    "support.no_orders": (
        "需要帮助吗？点击下方联系我们 — 通常几小时内回复。\n\n"
        "⚡ <i>平均响应：工作时间内 1 小时内。</i>"
    ),
    "ticket.pick_title": "🆘 <b>反馈问题</b> 🎫\n\n请选择需要帮助的订单 👇",
    "ticket.none": "您还没有可反馈的订单。",
    "ticket.invalid": "选择无效。",
    "ticket.cap": "您已有多个未处理的工单 — 我们会先回复这些。请等待回复后再创建新工单。",
    "ticket.describe": (
        "🆘 <b>反馈问题</b> 📝\n\n"
        "订单 <b>#{id}</b> — {name}\n\n"
        "请用一条消息描述问题（例如代码无效、产品错误）。我们的团队会在这里回复您。"
    ),
    "ticket.send_text": "请以文字消息发送您的问题。",
    "ticket.too_long": "有点长 — 请保持在 {max} 个字符以内。",
    "ticket.expired": "会话已过期 — 请重新打开订单进行反馈。",
    "ticket.not_yours": "该订单不属于您。",
    "ticket.received": (
        "✅ <b>已收到工单 #{id}</b>\n\n"
        "谢谢 — 我们已记录订单 <b>#{order}</b> 的问题，会在这里回复您。💬"
    ),
    "ticket.reply_dm": "💬 <b>对您工单 #{id} 的回复</b>（订单 #{order}）\n\n{body}",
    "notify.balance": "💰 <b>余额更新</b>\n\n调整：{sign}{delta} USDT\n新余额：{balance} USDT",
    "notify.balance_note": "\n备注：{note}",
    "notify.blocked": "🚫 <b>已禁止购买</b>\n\n您无法在本店下单。如需帮助请联系客服。",
    "notify.unblocked": "✅ <b>已恢复购买</b>\n\n您可以再次下单。",
    "notify.expired": "⏰ 付款 <b>#{id}</b> 在收到前已过期。",
    "notify.refunded": "⚠️ 已收到付款 <b>#{id}</b>，但库存售罄。<b>{amount} {coin}</b> 已作为退款充入您的钱包。",
    "notify.delivered": (
        "🎉 <b>订单已发货！</b> ✨\n"
        "━━━━━━━━━━━━━━━━━\n"
        "📦 产品：<b>{name}</b>\n"
        "🆔 订单：<b>#{id}</b>\n"
        "💰 已付：<b>{amount} {coin}</b>\n"
        "🔗 TXID：<code>{txid}</code>\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        "🎁 <b>您的代码：</b>"
    ),
    "notify.help": "🛟 <i>订单有问题？请勿私信 — 点击下方 🆘 反馈问题，我们会在这里为您解决。</i>",
    "notify.deposit": (
        "💸 <b>已收到充值！</b> ✨\n"
        "━━━━━━━━━━━━━━━━━\n"
        "💰 金额：<b>{amount} {coin}</b>\n"
        "🔗 TXID：<code>{txid}</code>\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        "💎 余额已到账 — 输入 /shop 开始购买！"
    ),
    "bc.restock": "🆕 <b>{name} 新增 {n} 件库存！</b>",
    "bc.available": "📦 可用：<b>{n} 件</b>",
    "bc.price": "💲 价格：<b>${price} USDT</b>",
    "bc.scarcity_left": "⚠️ <b>仅剩 {n} 件</b> — 很快就会售罄。",
    "bc.scarcity_limited": "⚡ <b>限量上架</b> — 很快售罄，赶快下手。",
    "bc.pricedrop": "📉 <b>降价！</b>",
    "bc.was": "💲 原价：<s>{old} USDT</s>",
    "bc.now": "🌞 现价：<b>{new} USDT</b>",
    "bc.limited_offer": "<i>限时优惠 — 立即购买！</i>",
    "bc.new_product": "🆕 <b>新品上架！</b>",
    "bc.name": "📦 名称：<b>{name}</b>",
    "bc.open_shop": "🌟 <b>打开商店立即购买！</b>",
    "bc.admin_notification": "👁 <b>管理员通知：</b>",
}

DOWNLOAD_EXPORT_STRINGS: dict[str, str] = {
    "btn.download_txt": "📄 TXT",
    "btn.download_excel": "📊 Excel",
    "btn.download_yes": "✅ Send file",
    "btn.back_orders": "◀️ Back to Orders",
    "history.order_button": "#{id} {name}",
    "download.confirm_title": "📥 <b>Confirm Download</b>",
    "download.product": "📦 Product: <b>{name}</b>",
    "download.order": "🧾 Order: <b>#{id}</b>",
    "download.format": "🗂 Format: <b>{format}</b>",
    "download.format_txt": "TXT (.txt)",
    "download.format_xlsx": "Excel (.xlsx)",
    "download.items": "🔢 Items: <b>{n}</b>",
    "download.prompt": "Generate this file and send it here?",
    "download.not_delivered": "This order is not delivered yet.",
    "download.empty": "No delivered data found for this order.",
    "download.sent": "File sent ✅",
    "download.cancelled": "Download cancelled.",
    "download.caption": "{name}\nOrder #{id}\n{n} item(s)",
}

for _cat in (EN, AR, HI, VI, ZH):
    _cat.update(DOWNLOAD_EXPORT_STRINGS)


BUYER_HYPE_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "btn.shop": "🛍 Shop Deals",
        "btn.wallet": "💰 Wallet",
        "btn.profile": "👤 Profile",
        "btn.support": "🆘 Support",
        "btn.history": "📜 Orders",
        "btn.language": "🌐 Language",
        "btn.profile_short": "👤 Profile",
        "btn.purchase_history": "📜 Orders",
        "btn.deposit": "💳 Deposit",
        "btn.back_menu": "◀️ Main Menu",
        "btn.main_menu": "🏠 Main Menu",
        "btn.back_store": "◀️ Store",
        "btn.buy_now": "Buy Now",
        "btn.custom_amount": "✏️ Custom Qty",
        "btn.cancel": "❌ Cancel",
        "btn.refresh": "🔄 Refresh",
        "btn.report_issue": "🆘 Report issue",
        "btn.shop_more": "🛍 Buy Again",
        "btn.check_paid": "🔄 Check Payment",
        "btn.cancel_payment": "❌ Cancel Payment",
        "btn.open_ticket": "🎫 Open Ticket",
        "btn.message_us": "💬 Message @{user}",
        "btn.stock": "(Stock: {n})",
        "btn.out_of_stock": "(Out of stock)",
        "start.welcome": (
            "{crown} <b>{shop}</b> {sparkle}\n"
            "<b>Premium digital drops. Instant delivery.</b>\n\n"
            "Hey <b>{name}</b> {fire}\n\n"
            "<blockquote>{rocket} <b>Shop</b> — live stock + fast checkout\n"
            "{diamond} <b>Wallet</b> — top up and buy faster\n"
            "👤 <b>Profile</b> — balance, tier and orders\n"
            "📜 <b>History</b> — reopen delivered orders\n"
            "🆘 <b>Support</b> — order-linked help</blockquote>\n\n"
            "{arrow} <b>Choose your move below.</b>"
        ),
        "shop.list_title": "🛍 <b>Live Digital Drops</b> ✨",
        "shop.tap_page": "Pick a product below — page {page}/{total}:",
        "shop.tap": "Pick a product below:",
        "shop.card_price": "💸 Deal price: <b>${price}</b> / {unit}",
        "shop.card_stock": "📦 Stock: <b>{n} ready</b>",
        "shop.card_bulk": "📊 <b>Bulk deals:</b>",
        "shop.card_auto": "⚡ <i>Instant auto-delivery after payment confirmation.</i>",
        "shop.buy_header": "⚡ <b>{name}</b>\n\n💸 ${price} / {unit}\n📌 Choose your quantity below:",
        "shop.ask_qty": "Send the quantity you want, from 1 to 1000:",
        "shop.only_n": "Only {n} ready right now. Pick a smaller quantity.",
        "sum.qty": "🔢 Quantity: <b>{qty}</b> {unit}",
        "sum.subtotal": "💵 Subtotal: <b>${subtotal}</b>",
        "sum.discount": "🎟 Discount: −${discount}",
        "sum.payable": "💎 Payable now: <b>${payable}</b>",
        "sum.wallet_admin": "👛 Wallet: <b>∞ admin</b>",
        "sum.wallet": "👛 Wallet: <b>${balance}</b>",
        "sum.short": "   <i>Wallet is short ${short}; use a crypto method below.</i>",
        "sum.choose": "<i>Pick your payment lane below 👇</i>",
        "profile.title": "💎 <b>Buyer Profile</b> ✨",
        "profile.name": "👋 Name: <b>{name}</b>",
        "profile.tier": "🏅 Tier: <b>{tier}</b>",
        "profile.balance": "💰 Balance: <b>{balance}</b>",
        "profile.spent": "🛍 Total spent: <b>{spent}</b>",
        "profile.next_tier": "\n🚀 <i>{remaining} more to unlock <b>{tier}</b></i>",
        "history.title": "📜 <b>Your Orders</b> ✨",
        "history.none": "No orders yet — tap 🛍 <b>Shop</b> to grab your first drop.",
        "history.order_num": "<b>Order #{id}</b>",
        "history.delivered_label": "🎁 <b>Delivered data:</b>",
        "history.delivered_inline": "🎁 <b>Delivered:</b> <code>{code}</code>",
        "history.more_codes": "   <i>+{n} more — open downloads for the full file</i>",
        "deposit.wallet_title": "💰 <b>Wallet Hub</b> ✨",
        "deposit.balance_admin": "💎 Current balance: <b>∞ unlimited</b> (admin)",
        "deposit.balance": "💎 Current balance: <b>{balance}</b>",
        "deposit.tap_deposit": "<i>Tap 💳 <b>Deposit</b> to load funds and checkout faster.</i>",
        "deposit.prompt": "💰 <b>Wallet Deposit</b> ⚡\n\n💲 Minimum: <b>{min}</b> USDT\n\n👇 Reply with the amount you want to add.",
        "pay.method_title": "💳 <b>Choose Payment</b>",
        "pay.product": "📦 Product: <b>{name}</b>",
        "pay.quantity": "🔢 Quantity: <b>{qty}</b>",
        "pay.subtotal": "💵 Subtotal: <b>{subtotal}</b>",
        "pay.payable": "💎 Payable: <b>{payable}</b>",
        "pay.wallet": "👛 Wallet: <b>{balance}</b>",
        "pay.topup_label": "💰 Wallet Top-up",
        "topup.title": "{method} — Wallet top-up",
        "pay.auto": "⚡ Auto-check is live",
        "pay.send_exact": "📤 <b>Send exactly {amount} {coin}</b>",
        "pay.order_total": "💎 Order Total: <b>{payable} USDT</b>",
        "pay.product_line": "📦 Product: {prefix}<b>{name}</b>",
        "pay.qty_line": "🔢 Quantity: <b>{qty}</b>",
        "pay.important": "⚠️ <b>Important:</b>",
        "pay.send_exact_amount": "• Send the exact amount shown above.",
        "pay.exact_warning": "❗️ <b>Send this EXACT amount — do not round it.</b> A different amount can delay delivery.",
        "pay.auto_deliver": "🚀 Confirmed payments deliver automatically.",
        "pay.delivered": (
            "🎉 <b>Order #{id} delivered</b> ✨\n"
            "Charged: <b>${charged}</b>\n\n"
            "Your data is below. You can also download TXT/Excel from this order.\n\n"
            "🛟 <i>Problem with this order? Tap 🆘 Report issue below and we will fix it here.</i>"
        ),
        "pay.codes_head": "📜 <b>Your data for order #{id}:</b>",
        "pay.confirmed_order": "✅ Payment confirmed — delivery is on the way.",
        "pay.confirmed_deposit": "✅ Deposit confirmed — wallet loaded.",
        "pay.still_pending": "Still pending. Auto-check runs every {seconds}s.",
        "support.title": "🆘 <b>Support Desk</b> 💬",
        "support.with_orders": (
            "Need help with an order?\n\n"
            "🎫 <b>Open a ticket</b> so the order stays attached.\n"
            "💬 Or message us directly.\n\n"
            "⚡ <i>Average response: under 1 hour during business hours.</i>"
        ),
        "support.no_orders": (
            "Need help? Message us directly below.\n\n"
            "⚡ <i>Average response: under 1 hour during business hours.</i>"
        ),
        "ticket.pick_title": "🆘 <b>Report an issue</b> 🎫\n\nPick the order you need help with 👇",
        "ticket.describe": (
            "🎫 <b>Order #{id}</b>\n"
            "📦 Product: <b>{name}</b>\n\n"
            "Describe the issue in one message. Include what you expected and what happened."
        ),
        "ticket.received": (
            "✅ <b>Ticket #{id} opened</b>\n\n"
            "Order #{order} is attached. We will reply here."
        ),
    },
    "ar": {
        "btn.shop": "🛍 عروض المتجر",
        "btn.wallet": "💰 المحفظة",
        "btn.profile": "👤 الملف",
        "btn.support": "🆘 الدعم",
        "btn.history": "📜 الطلبات",
        "btn.language": "🌐 اللغة",
        "btn.profile_short": "👤 الملف",
        "btn.purchase_history": "📜 الطلبات",
        "btn.deposit": "💳 إيداع",
        "btn.back_menu": "◀️ القائمة الرئيسية",
        "btn.main_menu": "🏠 القائمة الرئيسية",
        "btn.back_store": "◀️ المتجر",
        "btn.buy_now": "اشترِ الآن",
        "btn.custom_amount": "✏️ كمية مخصصة",
        "btn.cancel": "❌ إلغاء",
        "btn.refresh": "🔄 تحديث",
        "btn.report_issue": "🆘 بلّغ عن مشكلة",
        "btn.shop_more": "🛍 اشترِ مرة أخرى",
        "btn.check_paid": "🔄 تحقق من الدفع",
        "btn.cancel_payment": "❌ إلغاء الدفع",
        "btn.open_ticket": "🎫 افتح تذكرة",
        "btn.message_us": "💬 راسل @{user}",
        "btn.stock": "(المتوفر: {n})",
        "btn.out_of_stock": "(نفد المخزون)",
        "start.welcome": (
            "{crown} <b>{shop}</b> {sparkle}\n"
            "<b>منتجات رقمية مميزة وتسليم فوري.</b>\n\n"
            "أهلاً <b>{name}</b> {fire}\n\n"
            "<blockquote>{rocket} <b>المتجر</b> — مخزون مباشر ودفع سريع\n"
            "{diamond} <b>المحفظة</b> — اشحن وادفع أسرع\n"
            "👤 <b>الملف</b> — الرصيد والمستوى والطلبات\n"
            "📜 <b>السجل</b> — افتح طلباتك المسلمة\n"
            "🆘 <b>الدعم</b> — مساعدة مرتبطة بالطلب</blockquote>\n\n"
            "{arrow} <b>اختر خطوتك بالأسفل.</b>"
        ),
        "shop.list_title": "🛍 <b>المنتجات المباشرة</b> ✨",
        "shop.tap_page": "اختر منتجاً بالأسفل — الصفحة {page}/{total}:",
        "shop.tap": "اختر منتجاً بالأسفل:",
        "shop.card_price": "💸 سعر العرض: <b>${price}</b> / {unit}",
        "shop.card_stock": "📦 المخزون: <b>{n} جاهز</b>",
        "shop.card_bulk": "📊 <b>عروض الكميات:</b>",
        "shop.card_auto": "⚡ <i>تسليم تلقائي فوري بعد تأكيد الدفع.</i>",
        "shop.buy_header": "⚡ <b>{name}</b>\n\n💸 ${price} / {unit}\n📌 اختر الكمية بالأسفل:",
        "shop.ask_qty": "أرسل الكمية المطلوبة من 1 إلى 1000:",
        "shop.only_n": "المتوفر الآن {n} فقط. اختر كمية أصغر.",
        "sum.qty": "🔢 الكمية: <b>{qty}</b> {unit}",
        "sum.subtotal": "💵 المجموع: <b>${subtotal}</b>",
        "sum.discount": "🎟 الخصم: −${discount}",
        "sum.payable": "💎 المطلوب الآن: <b>${payable}</b>",
        "sum.wallet_admin": "👛 المحفظة: <b>∞ مشرف</b>",
        "sum.wallet": "👛 المحفظة: <b>${balance}</b>",
        "sum.short": "   <i>المحفظة ينقصها ${short}; استخدم طريقة كريبتو بالأسفل.</i>",
        "sum.choose": "<i>اختر طريقة الدفع بالأسفل 👇</i>",
        "profile.title": "💎 <b>ملف المشتري</b> ✨",
        "profile.name": "👋 الاسم: <b>{name}</b>",
        "profile.tier": "🏅 المستوى: <b>{tier}</b>",
        "profile.balance": "💰 الرصيد: <b>{balance}</b>",
        "profile.spent": "🛍 إجمالي الإنفاق: <b>{spent}</b>",
        "profile.next_tier": "\n🚀 <i>تبقّى {remaining} لفتح <b>{tier}</b></i>",
        "history.title": "📜 <b>طلباتك</b> ✨",
        "history.none": "لا توجد طلبات بعد — اضغط 🛍 <b>المتجر</b> لأول شراء.",
        "history.order_num": "<b>الطلب #{id}</b>",
        "history.delivered_label": "🎁 <b>البيانات المسلمة:</b>",
        "history.delivered_inline": "🎁 <b>تم التسليم:</b> <code>{code}</code>",
        "history.more_codes": "   <i>+{n} أخرى — افتح التنزيلات للملف الكامل</i>",
        "deposit.wallet_title": "💰 <b>مركز المحفظة</b> ✨",
        "deposit.balance_admin": "💎 الرصيد الحالي: <b>∞ غير محدود</b> (مشرف)",
        "deposit.balance": "💎 الرصيد الحالي: <b>{balance}</b>",
        "deposit.tap_deposit": "<i>اضغط 💳 <b>إيداع</b> لإضافة رصيد والدفع أسرع.</i>",
        "deposit.prompt": "💰 <b>إيداع للمحفظة</b> ⚡\n\n💲 الحد الأدنى: <b>{min}</b> USDT\n\n👇 أرسل المبلغ الذي تريد إضافته.",
        "pay.method_title": "💳 <b>اختر الدفع</b>",
        "pay.product": "📦 المنتج: <b>{name}</b>",
        "pay.quantity": "🔢 الكمية: <b>{qty}</b>",
        "pay.subtotal": "💵 المجموع: <b>{subtotal}</b>",
        "pay.payable": "💎 المطلوب: <b>{payable}</b>",
        "pay.wallet": "👛 المحفظة: <b>{balance}</b>",
        "pay.topup_label": "💰 شحن المحفظة",
        "topup.title": "{method} — شحن المحفظة",
        "pay.auto": "⚡ التحقق التلقائي يعمل",
        "pay.send_exact": "📤 <b>أرسل بالضبط {amount} {coin}</b>",
        "pay.order_total": "💎 إجمالي الطلب: <b>{payable} USDT</b>",
        "pay.product_line": "📦 المنتج: {prefix}<b>{name}</b>",
        "pay.qty_line": "🔢 الكمية: <b>{qty}</b>",
        "pay.important": "⚠️ <b>مهم:</b>",
        "pay.send_exact_amount": "• أرسل المبلغ الظاهر بالضبط.",
        "pay.exact_warning": "❗️ <b>أرسل هذا المبلغ بالضبط — لا تقرّبه.</b> أي مبلغ مختلف قد يؤخر التسليم.",
        "pay.auto_deliver": "🚀 المدفوعات المؤكدة تُسلّم تلقائياً.",
        "pay.delivered": (
            "🎉 <b>تم تسليم الطلب #{id}</b> ✨\n"
            "المخصوم: <b>${charged}</b>\n\n"
            "بياناتك بالأسفل. يمكنك أيضاً تنزيل TXT/Excel من هذا الطلب.\n\n"
            "🛟 <i>مشكلة في الطلب؟ اضغط 🆘 بلّغ عن مشكلة بالأسفل وسنحلها هنا.</i>"
        ),
        "pay.codes_head": "📜 <b>بياناتك للطلب #{id}:</b>",
        "pay.confirmed_order": "✅ تم تأكيد الدفع — التسليم في الطريق.",
        "pay.confirmed_deposit": "✅ تم تأكيد الإيداع — تم شحن المحفظة.",
        "pay.still_pending": "ما زال معلقاً. التحقق التلقائي يعمل كل {seconds} ثانية.",
        "support.title": "🆘 <b>مكتب الدعم</b> 💬",
        "support.with_orders": (
            "تحتاج مساعدة في طلب؟\n\n"
            "🎫 <b>افتح تذكرة</b> حتى يبقى الطلب مرفقاً.\n"
            "💬 أو راسلنا مباشرة.\n\n"
            "⚡ <i>متوسط الرد: أقل من ساعة خلال أوقات العمل.</i>"
        ),
        "support.no_orders": (
            "تحتاج مساعدة؟ راسلنا مباشرة بالأسفل.\n\n"
            "⚡ <i>متوسط الرد: أقل من ساعة خلال أوقات العمل.</i>"
        ),
        "ticket.pick_title": "🆘 <b>بلّغ عن مشكلة</b> 🎫\n\nاختر الطلب الذي تحتاج مساعدة بشأنه 👇",
        "ticket.describe": (
            "🎫 <b>الطلب #{id}</b>\n"
            "📦 المنتج: <b>{name}</b>\n\n"
            "اشرح المشكلة في رسالة واحدة. اذكر المتوقع وما حدث."
        ),
        "ticket.received": (
            "✅ <b>تم فتح التذكرة #{id}</b>\n\n"
            "الطلب #{order} مرفق. سنرد هنا."
        ),
    },
    "hi": {
        "btn.shop": "🛍 Shop Deals",
        "btn.wallet": "💰 Wallet",
        "btn.profile": "👤 Profile",
        "btn.support": "🆘 Support",
        "btn.history": "📜 Orders",
        "btn.language": "🌐 Language",
        "btn.profile_short": "👤 Profile",
        "btn.purchase_history": "📜 Orders",
        "btn.deposit": "💳 Deposit",
        "btn.back_menu": "◀️ Main Menu",
        "btn.main_menu": "🏠 Main Menu",
        "btn.back_store": "◀️ Store",
        "btn.buy_now": "Abhi khariden",
        "btn.custom_amount": "✏️ Custom Qty",
        "btn.cancel": "❌ Cancel",
        "btn.refresh": "🔄 Refresh",
        "btn.report_issue": "🆘 Problem report",
        "btn.shop_more": "🛍 Dobara khariden",
        "btn.check_paid": "🔄 Payment check",
        "btn.cancel_payment": "❌ Payment cancel",
        "btn.open_ticket": "🎫 Ticket kholo",
        "btn.message_us": "💬 Message @{user}",
        "btn.stock": "(Stock: {n})",
        "btn.out_of_stock": "(Stock khatam)",
        "start.welcome": (
            "{crown} <b>{shop}</b> {sparkle}\n"
            "<b>Premium digital drops. Instant delivery.</b>\n\n"
            "Hey <b>{name}</b> {fire}\n\n"
            "<blockquote>{rocket} <b>Shop</b> — live stock + fast checkout\n"
            "{diamond} <b>Wallet</b> — top up aur fast buy\n"
            "👤 <b>Profile</b> — balance, tier aur orders\n"
            "📜 <b>History</b> — delivered orders dobara kholo\n"
            "🆘 <b>Support</b> — order-linked help</blockquote>\n\n"
            "{arrow} <b>Neeche apna move chuniye.</b>"
        ),
        "shop.list_title": "🛍 <b>Live Digital Drops</b> ✨",
        "shop.tap_page": "Neeche product chuniye — page {page}/{total}:",
        "shop.tap": "Neeche product chuniye:",
        "shop.card_price": "💸 Deal price: <b>${price}</b> / {unit}",
        "shop.card_stock": "📦 Stock: <b>{n} ready</b>",
        "shop.card_bulk": "📊 <b>Bulk deals:</b>",
        "shop.card_auto": "⚡ <i>Payment confirm hote hi instant auto-delivery.</i>",
        "shop.buy_header": "⚡ <b>{name}</b>\n\n💸 ${price} / {unit}\n📌 Neeche quantity chuniye:",
        "shop.ask_qty": "1 se 1000 tak quantity bhejein:",
        "shop.only_n": "Abhi sirf {n} ready hain. Chhoti quantity chuniye.",
        "sum.qty": "🔢 Quantity: <b>{qty}</b> {unit}",
        "sum.subtotal": "💵 Subtotal: <b>${subtotal}</b>",
        "sum.discount": "🎟 Discount: −${discount}",
        "sum.payable": "💎 Payable now: <b>${payable}</b>",
        "sum.wallet_admin": "👛 Wallet: <b>∞ admin</b>",
        "sum.wallet": "👛 Wallet: <b>${balance}</b>",
        "sum.short": "   <i>Wallet mein ${short} kam hai; neeche crypto method use karein.</i>",
        "sum.choose": "<i>Neeche payment lane chuniye 👇</i>",
        "profile.title": "💎 <b>Buyer Profile</b> ✨",
        "profile.name": "👋 Naam: <b>{name}</b>",
        "profile.tier": "🏅 Tier: <b>{tier}</b>",
        "profile.balance": "💰 Balance: <b>{balance}</b>",
        "profile.spent": "🛍 Total kharch: <b>{spent}</b>",
        "profile.next_tier": "\n🚀 <i><b>{tier}</b> unlock karne ke liye {remaining} aur</i>",
        "history.title": "📜 <b>Aapke Orders</b> ✨",
        "history.none": "Abhi koi order nahi — pehla drop lene ke liye 🛍 <b>Shop</b> tap karein.",
        "history.order_num": "<b>Order #{id}</b>",
        "history.delivered_label": "🎁 <b>Delivered data:</b>",
        "history.delivered_inline": "🎁 <b>Delivered:</b> <code>{code}</code>",
        "history.more_codes": "   <i>+{n} aur — full file ke liye downloads kholo</i>",
        "deposit.wallet_title": "💰 <b>Wallet Hub</b> ✨",
        "deposit.balance_admin": "💎 Current balance: <b>∞ unlimited</b> (admin)",
        "deposit.balance": "💎 Current balance: <b>{balance}</b>",
        "deposit.tap_deposit": "<i>Funds load karne aur fast checkout ke liye 💳 <b>Deposit</b> tap karein.</i>",
        "deposit.prompt": "💰 <b>Wallet Deposit</b> ⚡\n\n💲 Minimum: <b>{min}</b> USDT\n\n👇 Jo amount add karna hai wo bhejein.",
        "pay.method_title": "💳 <b>Payment chuniye</b>",
        "pay.product": "📦 Product: <b>{name}</b>",
        "pay.quantity": "🔢 Quantity: <b>{qty}</b>",
        "pay.subtotal": "💵 Subtotal: <b>{subtotal}</b>",
        "pay.payable": "💎 Payable: <b>{payable}</b>",
        "pay.wallet": "👛 Wallet: <b>{balance}</b>",
        "pay.topup_label": "💰 Wallet Top-up",
        "topup.title": "{method} — Wallet top-up",
        "pay.auto": "⚡ Auto-check live hai",
        "pay.send_exact": "📤 <b>Bilkul {amount} {coin} bhejein</b>",
        "pay.order_total": "💎 Order Total: <b>{payable} USDT</b>",
        "pay.product_line": "📦 Product: {prefix}<b>{name}</b>",
        "pay.qty_line": "🔢 Quantity: <b>{qty}</b>",
        "pay.important": "⚠️ <b>Important:</b>",
        "pay.send_exact_amount": "• Upar dikhaya exact amount bhejein.",
        "pay.exact_warning": "❗️ <b>Ye EXACT amount bhejein — round mat karein.</b> Alag amount delivery delay kar sakta hai.",
        "pay.auto_deliver": "🚀 Confirmed payments automatically deliver hote hain.",
        "pay.delivered": (
            "🎉 <b>Order #{id} delivered</b> ✨\n"
            "Charged: <b>${charged}</b>\n\n"
            "Aapka data neeche hai. Is order se TXT/Excel bhi download kar sakte hain.\n\n"
            "🛟 <i>Order mein problem? Neeche 🆘 Problem report tap karein, hum yahin fix karenge.</i>"
        ),
        "pay.codes_head": "📜 <b>Order #{id} ka data:</b>",
        "pay.confirmed_order": "✅ Payment confirmed — delivery aa rahi hai.",
        "pay.confirmed_deposit": "✅ Deposit confirmed — wallet loaded.",
        "pay.still_pending": "Abhi pending hai. Auto-check har {seconds}s chalta hai.",
        "support.title": "🆘 <b>Support Desk</b> 💬",
        "support.with_orders": (
            "Order mein help chahiye?\n\n"
            "🎫 <b>Ticket kholo</b> taake order attached rahe.\n"
            "💬 Ya direct message karein.\n\n"
            "⚡ <i>Average response: business hours mein 1 hour se kam.</i>"
        ),
        "support.no_orders": (
            "Help chahiye? Neeche direct message karein.\n\n"
            "⚡ <i>Average response: business hours mein 1 hour se kam.</i>"
        ),
        "ticket.pick_title": "🆘 <b>Problem report karein</b> 🎫\n\nJis order mein help chahiye wo chuniye 👇",
        "ticket.describe": (
            "🎫 <b>Order #{id}</b>\n"
            "📦 Product: <b>{name}</b>\n\n"
            "Problem ek message mein describe karein. Expected aur actual dono likhein."
        ),
        "ticket.received": (
            "✅ <b>Ticket #{id} opened</b>\n\n"
            "Order #{order} attached hai. Hum yahin reply karenge."
        ),
    },
    "vi": {
        "btn.shop": "🛍 Ưu đãi",
        "btn.wallet": "💰 Ví",
        "btn.profile": "👤 Hồ sơ",
        "btn.support": "🆘 Hỗ trợ",
        "btn.history": "📜 Đơn hàng",
        "btn.language": "🌐 Ngôn ngữ",
        "btn.profile_short": "👤 Hồ sơ",
        "btn.purchase_history": "📜 Đơn hàng",
        "btn.deposit": "💳 Nạp tiền",
        "btn.back_menu": "◀️ Menu chính",
        "btn.main_menu": "🏠 Menu chính",
        "btn.back_store": "◀️ Cửa hàng",
        "btn.buy_now": "Mua ngay",
        "btn.custom_amount": "✏️ SL tùy chỉnh",
        "btn.cancel": "❌ Hủy",
        "btn.refresh": "🔄 Làm mới",
        "btn.report_issue": "🆘 Báo lỗi",
        "btn.shop_more": "🛍 Mua lại",
        "btn.check_paid": "🔄 Kiểm tra thanh toán",
        "btn.cancel_payment": "❌ Hủy thanh toán",
        "btn.open_ticket": "🎫 Mở yêu cầu",
        "btn.message_us": "💬 Nhắn @{user}",
        "btn.stock": "(Còn: {n})",
        "btn.out_of_stock": "(Hết hàng)",
        "start.welcome": (
            "{crown} <b>{shop}</b> {sparkle}\n"
            "<b>Sản phẩm số cao cấp. Giao tự động.</b>\n\n"
            "Xin chào <b>{name}</b> {fire}\n\n"
            "<blockquote>{rocket} <b>Cửa hàng</b> — hàng sẵn và thanh toán nhanh\n"
            "{diamond} <b>Ví</b> — nạp tiền để mua nhanh hơn\n"
            "👤 <b>Hồ sơ</b> — số dư, hạng và đơn hàng\n"
            "📜 <b>Lịch sử</b> — mở lại đơn đã giao\n"
            "🆘 <b>Hỗ trợ</b> — trợ giúp theo từng đơn</blockquote>\n\n"
            "{arrow} <b>Chọn bước tiếp theo bên dưới.</b>"
        ),
        "shop.list_title": "🛍 <b>Sản phẩm đang mở bán</b> ✨",
        "shop.tap_page": "Chọn sản phẩm bên dưới — trang {page}/{total}:",
        "shop.tap": "Chọn sản phẩm bên dưới:",
        "shop.card_price": "💸 Giá ưu đãi: <b>${price}</b> / {unit}",
        "shop.card_stock": "📦 Kho: <b>{n} sẵn sàng</b>",
        "shop.card_bulk": "📊 <b>Ưu đãi số lượng:</b>",
        "shop.card_auto": "⚡ <i>Tự động giao ngay sau khi xác nhận thanh toán.</i>",
        "shop.buy_header": "⚡ <b>{name}</b>\n\n💸 ${price} / {unit}\n📌 Chọn số lượng bên dưới:",
        "shop.ask_qty": "Gửi số lượng bạn muốn, từ 1 đến 1000:",
        "shop.only_n": "Hiện chỉ còn {n}. Hãy chọn số lượng nhỏ hơn.",
        "sum.qty": "🔢 Số lượng: <b>{qty}</b> {unit}",
        "sum.subtotal": "💵 Tạm tính: <b>${subtotal}</b>",
        "sum.discount": "🎟 Giảm giá: −${discount}",
        "sum.payable": "💎 Cần trả: <b>${payable}</b>",
        "sum.wallet_admin": "👛 Ví: <b>∞ admin</b>",
        "sum.wallet": "👛 Ví: <b>${balance}</b>",
        "sum.short": "   <i>Ví còn thiếu ${short}; dùng phương thức crypto bên dưới.</i>",
        "sum.choose": "<i>Chọn cách thanh toán bên dưới 👇</i>",
        "profile.title": "💎 <b>Hồ sơ người mua</b> ✨",
        "profile.name": "👋 Tên: <b>{name}</b>",
        "profile.tier": "🏅 Hạng: <b>{tier}</b>",
        "profile.balance": "💰 Số dư: <b>{balance}</b>",
        "profile.spent": "🛍 Tổng chi tiêu: <b>{spent}</b>",
        "profile.next_tier": "\n🚀 <i>Cần thêm {remaining} để mở <b>{tier}</b></i>",
        "history.title": "📜 <b>Đơn hàng của bạn</b> ✨",
        "history.none": "Chưa có đơn nào — chạm 🛍 <b>Cửa hàng</b> để lấy sản phẩm đầu tiên.",
        "history.order_num": "<b>Đơn #{id}</b>",
        "history.delivered_label": "🎁 <b>Dữ liệu đã giao:</b>",
        "history.delivered_inline": "🎁 <b>Đã giao:</b> <code>{code}</code>",
        "history.more_codes": "   <i>+{n} nữa — mở tải xuống để lấy đủ file</i>",
        "deposit.wallet_title": "💰 <b>Trung tâm ví</b> ✨",
        "deposit.balance_admin": "💎 Số dư hiện tại: <b>∞ không giới hạn</b> (admin)",
        "deposit.balance": "💎 Số dư hiện tại: <b>{balance}</b>",
        "deposit.tap_deposit": "<i>Chạm 💳 <b>Nạp tiền</b> để thanh toán nhanh hơn.</i>",
        "deposit.prompt": "💰 <b>Nạp vào ví</b> ⚡\n\n💲 Tối thiểu: <b>{min}</b> USDT\n\n👇 Nhập số tiền bạn muốn thêm.",
        "pay.method_title": "💳 <b>Chọn thanh toán</b>",
        "pay.product": "📦 Sản phẩm: <b>{name}</b>",
        "pay.quantity": "🔢 Số lượng: <b>{qty}</b>",
        "pay.subtotal": "💵 Tạm tính: <b>{subtotal}</b>",
        "pay.payable": "💎 Cần trả: <b>{payable}</b>",
        "pay.wallet": "👛 Ví: <b>{balance}</b>",
        "pay.topup_label": "💰 Nạp ví",
        "topup.title": "{method} — Nạp ví",
        "pay.auto": "⚡ Tự động kiểm tra đang bật",
        "pay.send_exact": "📤 <b>Gửi đúng {amount} {coin}</b>",
        "pay.order_total": "💎 Tổng đơn: <b>{payable} USDT</b>",
        "pay.product_line": "📦 Sản phẩm: {prefix}<b>{name}</b>",
        "pay.qty_line": "🔢 Số lượng: <b>{qty}</b>",
        "pay.important": "⚠️ <b>Quan trọng:</b>",
        "pay.send_exact_amount": "• Gửi đúng số tiền hiển thị ở trên.",
        "pay.exact_warning": "❗️ <b>Gửi ĐÚNG số tiền này — không làm tròn.</b> Số tiền khác có thể làm chậm giao hàng.",
        "pay.auto_deliver": "🚀 Thanh toán xác nhận sẽ tự động giao hàng.",
        "pay.delivered": (
            "🎉 <b>Đơn #{id} đã giao</b> ✨\n"
            "Đã trừ: <b>${charged}</b>\n\n"
            "Dữ liệu của bạn ở bên dưới. Bạn cũng có thể tải TXT/Excel từ đơn này.\n\n"
            "🛟 <i>Có vấn đề? Chạm 🆘 Báo lỗi bên dưới, chúng tôi sẽ xử lý tại đây.</i>"
        ),
        "pay.codes_head": "📜 <b>Dữ liệu cho đơn #{id}:</b>",
        "pay.confirmed_order": "✅ Đã xác nhận thanh toán — đang giao.",
        "pay.confirmed_deposit": "✅ Đã xác nhận nạp tiền — ví đã được cộng.",
        "pay.still_pending": "Vẫn đang chờ. Tự động kiểm tra mỗi {seconds} giây.",
        "support.title": "🆘 <b>Bàn hỗ trợ</b> 💬",
        "support.with_orders": (
            "Cần hỗ trợ một đơn hàng?\n\n"
            "🎫 <b>Mở yêu cầu</b> để đơn luôn được đính kèm.\n"
            "💬 Hoặc nhắn trực tiếp cho chúng tôi.\n\n"
            "⚡ <i>Phản hồi trung bình: dưới 1 giờ trong giờ làm việc.</i>"
        ),
        "support.no_orders": (
            "Cần hỗ trợ? Nhắn trực tiếp bên dưới.\n\n"
            "⚡ <i>Phản hồi trung bình: dưới 1 giờ trong giờ làm việc.</i>"
        ),
        "ticket.pick_title": "🆘 <b>Báo lỗi</b> 🎫\n\nChọn đơn bạn cần hỗ trợ 👇",
        "ticket.describe": (
            "🎫 <b>Đơn #{id}</b>\n"
            "📦 Sản phẩm: <b>{name}</b>\n\n"
            "Mô tả vấn đề trong một tin nhắn. Nêu điều bạn mong đợi và điều đã xảy ra."
        ),
        "ticket.received": (
            "✅ <b>Đã mở yêu cầu #{id}</b>\n\n"
            "Đơn #{order} đã được đính kèm. Chúng tôi sẽ trả lời tại đây."
        ),
    },
    "zh": {
        "btn.shop": "🛍 优惠商品",
        "btn.wallet": "💰 钱包",
        "btn.profile": "👤 资料",
        "btn.support": "🆘 支持",
        "btn.history": "📜 订单",
        "btn.language": "🌐 语言",
        "btn.profile_short": "👤 资料",
        "btn.purchase_history": "📜 订单",
        "btn.deposit": "💳 充值",
        "btn.back_menu": "◀️ 主菜单",
        "btn.main_menu": "🏠 主菜单",
        "btn.back_store": "◀️ 商店",
        "btn.buy_now": "立即购买",
        "btn.custom_amount": "✏️ 自定义数量",
        "btn.cancel": "❌ 取消",
        "btn.refresh": "🔄 刷新",
        "btn.report_issue": "🆘 反馈问题",
        "btn.shop_more": "🛍 再买一次",
        "btn.check_paid": "🔄 检查付款",
        "btn.cancel_payment": "❌ 取消付款",
        "btn.open_ticket": "🎫 创建工单",
        "btn.message_us": "💬 联系 @{user}",
        "btn.stock": "(库存：{n})",
        "btn.out_of_stock": "(已售罄)",
        "start.welcome": (
            "{crown} <b>{shop}</b> {sparkle}\n"
            "<b>优质数字商品，自动即时交付。</b>\n\n"
            "你好 <b>{name}</b> {fire}\n\n"
            "<blockquote>{rocket} <b>商店</b> — 实时库存，快速结账\n"
            "{diamond} <b>钱包</b> — 充值后购买更快\n"
            "👤 <b>资料</b> — 余额、等级和订单\n"
            "📜 <b>历史</b> — 重新打开已交付订单\n"
            "🆘 <b>支持</b> — 按订单处理帮助</blockquote>\n\n"
            "{arrow} <b>请在下方选择操作。</b>"
        ),
        "shop.list_title": "🛍 <b>实时数字商品</b> ✨",
        "shop.tap_page": "请选择下方商品 — 第 {page}/{total} 页：",
        "shop.tap": "请选择下方商品：",
        "shop.card_price": "💸 优惠价：<b>${price}</b> / {unit}",
        "shop.card_stock": "📦 库存：<b>{n} 可用</b>",
        "shop.card_bulk": "📊 <b>批量优惠：</b>",
        "shop.card_auto": "⚡ <i>付款确认后自动即时交付。</i>",
        "shop.buy_header": "⚡ <b>{name}</b>\n\n💸 ${price} / {unit}\n📌 请在下方选择数量：",
        "shop.ask_qty": "请发送 1 到 1000 的购买数量：",
        "shop.only_n": "当前只有 {n} 件可用，请选择更小数量。",
        "sum.qty": "🔢 数量：<b>{qty}</b> {unit}",
        "sum.subtotal": "💵 小计：<b>${subtotal}</b>",
        "sum.discount": "🎟 折扣：−${discount}",
        "sum.payable": "💎 现在应付：<b>${payable}</b>",
        "sum.wallet_admin": "👛 钱包：<b>∞ admin</b>",
        "sum.wallet": "👛 钱包：<b>${balance}</b>",
        "sum.short": "   <i>钱包还差 ${short}；请使用下方加密付款方式。</i>",
        "sum.choose": "<i>请在下方选择付款方式 👇</i>",
        "profile.title": "💎 <b>买家资料</b> ✨",
        "profile.name": "👋 名称：<b>{name}</b>",
        "profile.tier": "🏅 等级：<b>{tier}</b>",
        "profile.balance": "💰 余额：<b>{balance}</b>",
        "profile.spent": "🛍 累计消费：<b>{spent}</b>",
        "profile.next_tier": "\n🚀 <i>再消费 {remaining} 即可解锁 <b>{tier}</b></i>",
        "history.title": "📜 <b>您的订单</b> ✨",
        "history.none": "还没有订单 — 点击 🛍 <b>商店</b> 获取第一件商品。",
        "history.order_num": "<b>订单 #{id}</b>",
        "history.delivered_label": "🎁 <b>已交付数据：</b>",
        "history.delivered_inline": "🎁 <b>已交付：</b> <code>{code}</code>",
        "history.more_codes": "   <i>+{n} 更多 — 打开下载获取完整文件</i>",
        "deposit.wallet_title": "💰 <b>钱包中心</b> ✨",
        "deposit.balance_admin": "💎 当前余额：<b>∞ 无限制</b>（管理员）",
        "deposit.balance": "💎 当前余额：<b>{balance}</b>",
        "deposit.tap_deposit": "<i>点击 💳 <b>充值</b> 加载资金，结账更快。</i>",
        "deposit.prompt": "💰 <b>钱包充值</b> ⚡\n\n💲 最低：<b>{min}</b> USDT\n\n👇 请回复您要充值的金额。",
        "pay.method_title": "💳 <b>选择付款</b>",
        "pay.product": "📦 商品：<b>{name}</b>",
        "pay.quantity": "🔢 数量：<b>{qty}</b>",
        "pay.subtotal": "💵 小计：<b>{subtotal}</b>",
        "pay.payable": "💎 应付：<b>{payable}</b>",
        "pay.wallet": "👛 钱包：<b>{balance}</b>",
        "pay.topup_label": "💰 钱包充值",
        "topup.title": "{method} — 钱包充值",
        "pay.auto": "⚡ 自动检查已开启",
        "pay.send_exact": "📤 <b>请准确发送 {amount} {coin}</b>",
        "pay.order_total": "💎 订单总额：<b>{payable} USDT</b>",
        "pay.product_line": "📦 商品：{prefix}<b>{name}</b>",
        "pay.qty_line": "🔢 数量：<b>{qty}</b>",
        "pay.important": "⚠️ <b>重要：</b>",
        "pay.send_exact_amount": "• 请发送上方显示的准确金额。",
        "pay.exact_warning": "❗️ <b>请发送完全相同的金额 — 不要四舍五入。</b> 金额不同可能延迟交付。",
        "pay.auto_deliver": "🚀 付款确认后会自动交付。",
        "pay.delivered": (
            "🎉 <b>订单 #{id} 已交付</b> ✨\n"
            "已扣款：<b>${charged}</b>\n\n"
            "您的数据在下方。也可以从此订单下载 TXT/Excel。\n\n"
            "🛟 <i>订单有问题？点击下方 🆘 反馈问题，我们会在这里处理。</i>"
        ),
        "pay.codes_head": "📜 <b>订单 #{id} 的数据：</b>",
        "pay.confirmed_order": "✅ 付款已确认 — 正在交付。",
        "pay.confirmed_deposit": "✅ 充值已确认 — 钱包已到账。",
        "pay.still_pending": "仍在等待。自动检查每 {seconds} 秒运行一次。",
        "support.title": "🆘 <b>支持中心</b> 💬",
        "support.with_orders": (
            "订单需要帮助？\n\n"
            "🎫 <b>创建工单</b>，订单会自动关联。\n"
            "💬 或直接联系我们。\n\n"
            "⚡ <i>平均回复：工作时间内少于 1 小时。</i>"
        ),
        "support.no_orders": (
            "需要帮助？请点击下方直接联系我们。\n\n"
            "⚡ <i>平均回复：工作时间内少于 1 小时。</i>"
        ),
        "ticket.pick_title": "🆘 <b>反馈问题</b> 🎫\n\n请选择需要帮助的订单 👇",
        "ticket.describe": (
            "🎫 <b>订单 #{id}</b>\n"
            "📦 商品：<b>{name}</b>\n\n"
            "请用一条消息描述问题，包括您期望的结果和实际发生的情况。"
        ),
        "ticket.received": (
            "✅ <b>工单 #{id} 已创建</b>\n\n"
            "订单 #{order} 已关联。我们会在这里回复。"
        ),
    },
}

for _code, _strings in BUYER_HYPE_STRINGS.items():
    {"en": EN, "ar": AR, "hi": HI, "vi": VI, "zh": ZH}[_code].update(_strings)


REFER_EARN_STRINGS: dict[str, dict[str, str]] = {
    _code: {
        "btn.refer": "\U0001f381 Refer & Earn",
        "btn.copy_link": "\U0001f517 Copy Link",
        "sum.referral_discount": "\U0001f381 Referral discount: -${discount}",
        "refer.screen": (
            "\U0001f381 <b>Refer & Earn</b>\n"
            "<i>Earn ${reward} per friend - they save {discount}%</i>\n\n"
            "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\n"
            "\U0001f4ca <b>Your Stats</b>\n"
            "<blockquote>\U0001f4b5 Earned   <b>${earned}</b>\n"
            "\U0001f465 Referrals <b>{referrals}</b>\n"
            "\u2705 Rewarded  <b>{rewarded}</b>\n"
            "\u23f3 Pending   <b>{pending}</b></blockquote>\n\n"
            "\U0001f517 <b>Your Link</b>\n"
            "<code>{link}</code>\n"
            "<i>Tap Copy Link, then share with friends.</i>\n\n"
            "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\n"
            "\U0001f3af <b>Rewards</b>\n"
            "<blockquote>\U0001f464 <b>You get</b>\n"
            "- ${reward} bonus -> balance\n"
            "- One-time per friend\n"
            "- Spendable on any product\n\n"
            "\U0001f91d <b>Your friend gets</b>\n"
            "- {discount}% OFF first {orders} orders\n"
            "- Auto-applied at checkout</blockquote>\n\n"
            "\u2699\ufe0f <b>How It Works</b>\n"
            "<blockquote>1. Share your link \U0001f517\n"
            "2. Friend joins via your link\n"
            "3. Friend buys ${min}+\n"
            "4. You get ${reward} instantly \u2705</blockquote>\n\n"
            "\u26a0\ufe0f <b>Rules</b>\n"
            "<blockquote>- Triggers on friend's first qualifying order only\n"
            "- Orders under ${min} don't count\n"
            "- Self-referrals and fake accounts rejected\n"
            "- Bonus is spendable, not withdrawable</blockquote>"
        ),
    }
    for _code in ("en", "ar", "hi", "vi", "zh")
}

for _code, _strings in REFER_EARN_STRINGS.items():
    {"en": EN, "ar": AR, "hi": HI, "vi": VI, "zh": ZH}[_code].update(_strings)

for _cat in (EN, AR, HI, VI, ZH):
    _welcome = _cat.get("start.welcome", "")
    if "Refer & Earn" not in _welcome:
        _cat["start.welcome"] = _welcome.replace(
            "</blockquote>\n\n{arrow}",
            "\n\U0001f381 <b>Refer & Earn</b> - invite friends + earn rewards</blockquote>\n\n{arrow}",
        )


RESELLER_API_STRINGS: dict[str, dict[str, str]] = {
    _code: {
        "btn.api_link": "\U0001f517 API Link",
        "btn.api_regenerate": "\U0001f504 Regenerate",
        "btn.api_regenerate_yes": "\u2705 Yes, regenerate",
        "api.screen_new": (
            "\U0001f517 <b>Reseller API Link</b>\n\n"
            "\U0001f511 <b>Your API key was created.</b>\n"
            "<code>{key}</code>\n\n"
            "\U0001f4d6 <b>API Docs:</b>\n"
            "<code>{docs}</code>\n\n"
            "\u26a0\ufe0f <b>Keep this key secret.</b>\n"
            "<i>It is shown only now. Later screens show prefix <code>{prefix}</code>.</i>"
        ),
        "api.screen_existing": (
            "\U0001f517 <b>Reseller API Link</b>\n\n"
            "\U0001f511 Active key prefix: <code>{prefix}</code>\n"
            "<i>The full key is hidden for security. Regenerate if you lost it.</i>\n\n"
            "\U0001f4d6 <b>API Docs:</b>\n"
            "<code>{docs}</code>\n\n"
            "\u26a0\ufe0f <b>Keep your API key secret.</b>"
        ),
        "api.regen_confirm": (
            "\u26a0\ufe0f <b>Regenerate API key?</b>\n\n"
            "Your old key will stop working immediately.\n"
            "Docs stay here: <code>{docs}</code>"
        ),
        "api.regenerated": "API key regenerated.",
    }
    for _code in ("en", "ar", "hi", "vi", "zh")
}

for _code, _strings in RESELLER_API_STRINGS.items():
    {"en": EN, "ar": AR, "hi": HI, "vi": VI, "zh": ZH}[_code].update(_strings)


LOCALIZED_LANGUAGE_FIXES: dict[str, dict[str, str]] = {
    "ar": {
        "btn.download_txt": "\U0001f4c4 تنزيل TXT",
        "btn.download_excel": "\U0001f4ca تنزيل Excel",
        "btn.download_yes": "\u2705 إرسال الملف",
        "btn.back_orders": "\u25c0\ufe0f رجوع إلى الطلبات",
        "btn.refer": "\U0001f381 الإحالة والربح",
        "btn.copy_link": "\U0001f517 نسخ الرابط",
        "btn.api_link": "\U0001f517 رابط API",
        "btn.api_regenerate": "\U0001f504 إعادة توليد",
        "btn.api_regenerate_yes": "\u2705 نعم، أعد التوليد",
        "sum.referral_discount": "\U0001f381 خصم الإحالة: -${discount}",
        "download.confirm_title": "\U0001f4e5 <b>تأكيد التنزيل</b>",
        "download.product": "\U0001f4e6 المنتج: <b>{name}</b>",
        "download.order": "\U0001f9fe الطلب: <b>#{id}</b>",
        "download.format": "\U0001f5c2 الصيغة: <b>{format}</b>",
        "download.items": "\U0001f522 العناصر: <b>{n}</b>",
        "download.prompt": "هل تريد إنشاء هذا الملف وإرساله هنا؟",
        "download.not_delivered": "لم يتم تسليم هذا الطلب بعد.",
        "download.empty": "لا توجد بيانات مسلّمة لهذا الطلب.",
        "download.sent": "تم إرسال الملف \u2705",
        "download.cancelled": "تم إلغاء التنزيل.",
        "download.caption": "{name}\nالطلب #{id}\n{n} عنصر",
        "refer.screen": (
            "\U0001f381 <b>الإحالة والربح</b>\n"
            "<i>اربح ${reward} عن كل صديق — وهو يحصل على خصم {discount}%</i>\n\n"
            "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\n"
            "\U0001f4ca <b>إحصائياتك</b>\n"
            "<blockquote>\U0001f4b5 الأرباح   <b>${earned}</b>\n"
            "\U0001f465 الإحالات <b>{referrals}</b>\n"
            "\u2705 المكافآت  <b>{rewarded}</b>\n"
            "\u23f3 قيد الانتظار   <b>{pending}</b></blockquote>\n\n"
            "\U0001f517 <b>رابطك</b>\n"
            "<code>{link}</code>\n"
            "<i>اضغط نسخ الرابط، ثم شاركه مع أصدقائك.</i>\n\n"
            "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\n"
            "\U0001f3af <b>المكافآت</b>\n"
            "<blockquote>\U0001f464 <b>أنت تحصل على</b>\n"
            "- مكافأة ${reward} تضاف إلى الرصيد\n"
            "- مرة واحدة لكل صديق\n"
            "- قابلة للصرف على أي منتج\n\n"
            "\U0001f91d <b>صديقك يحصل على</b>\n"
            "- خصم {discount}% على أول {orders} طلبات\n"
            "- يطبق تلقائياً عند الدفع</blockquote>\n\n"
            "\u2699\ufe0f <b>طريقة العمل</b>\n"
            "<blockquote>1. شارك رابطك \U0001f517\n"
            "2. ينضم صديقك عبر الرابط\n"
            "3. يشتري صديقك بقيمة ${min}+\n"
            "4. تحصل على ${reward} فوراً \u2705</blockquote>\n\n"
            "\u26a0\ufe0f <b>القواعد</b>\n"
            "<blockquote>- المكافأة لأول طلب مؤهل فقط من الصديق\n"
            "- الطلبات أقل من ${min} لا تحتسب\n"
            "- الإحالات الذاتية والحسابات المزيفة مرفوضة\n"
            "- المكافأة قابلة للصرف وليست للسحب</blockquote>"
        ),
        "api.screen_new": (
            "\U0001f517 <b>رابط API للموزعين</b>\n\n"
            "\U0001f511 <b>تم إنشاء مفتاح API الخاص بك.</b>\n"
            "<code>{key}</code>\n\n"
            "\U0001f4d6 <b>وثائق API:</b>\n"
            "<code>{docs}</code>\n\n"
            "\u26a0\ufe0f <b>احتفظ بهذا المفتاح بسرية.</b>\n"
            "<i>يظهر الآن فقط. لاحقاً سيظهر البادئ <code>{prefix}</code>.</i>"
        ),
        "api.screen_existing": (
            "\U0001f517 <b>رابط API للموزعين</b>\n\n"
            "\U0001f511 بادئ المفتاح النشط: <code>{prefix}</code>\n"
            "<i>المفتاح الكامل مخفي للأمان. أعد توليده إذا فقدته.</i>\n\n"
            "\U0001f4d6 <b>وثائق API:</b>\n"
            "<code>{docs}</code>\n\n"
            "\u26a0\ufe0f <b>احتفظ بمفتاح API بسرية.</b>"
        ),
        "api.regen_confirm": (
            "\u26a0\ufe0f <b>إعادة توليد مفتاح API؟</b>\n\n"
            "سيتوقف مفتاحك القديم فوراً.\n"
            "تبقى الوثائق هنا: <code>{docs}</code>"
        ),
        "api.regenerated": "تمت إعادة توليد مفتاح API.",
    },
    "hi": {
        "btn.shop": "\U0001f6cd Deals dekho",
        "btn.wallet": "\U0001f4b0 Wallet balance",
        "btn.profile": "\U0001f464 Mera Profile",
        "btn.profile_short": "\U0001f464 Mera Profile",
        "btn.support": "\U0001f198 Help Center",
        "btn.history": "\U0001f4dc Mere Orders",
        "btn.purchase_history": "\U0001f4dc Mere Orders",
        "btn.language": "\U0001f310 Bhasha",
        "btn.deposit": "\U0001f4b3 Paise add",
        "btn.back_menu": "\u25c0\ufe0f Menu par wapas",
        "btn.main_menu": "\U0001f3e0 Main menu",
        "btn.back_store": "\u25c0\ufe0f Store par wapas",
        "btn.custom_amount": "\u270f\ufe0f Apni quantity",
        "btn.cancel": "\u274c Radd karein",
        "btn.refresh": "\U0001f504 Reload",
        "btn.message_us": "\U0001f4ac @{user} ko message",
        "btn.page": "Pg {page}/{total}",
        "btn.stock": "(Bacha stock: {n})",
        "btn.download_txt": "\U0001f4c4 TXT download",
        "btn.download_excel": "\U0001f4ca Excel download",
        "btn.download_yes": "\u2705 File bhejen",
        "btn.back_orders": "\u25c0\ufe0f Orders par wapas",
        "btn.refer": "\U0001f381 Refer karo & Earn karo",
        "btn.copy_link": "\U0001f517 Link copy",
        "btn.api_link": "\U0001f517 API ka link",
        "btn.api_regenerate": "\U0001f504 Nayi key banao",
        "btn.api_regenerate_yes": "\u2705 Haan, nayi key",
        "sum.referral_discount": "\U0001f381 Referral discount: -${discount}",
        "download.confirm_title": "\U0001f4e5 <b>Download confirm karein</b>",
        "download.prompt": "Ye file generate karke yahin bhej dein?",
        "download.not_delivered": "Ye order abhi deliver nahi hua.",
        "download.empty": "Is order ka delivered data nahi mila.",
        "download.sent": "File bhej di \u2705",
        "download.cancelled": "Download radd ho gaya.",
        "refer.screen": (
            "\U0001f381 <b>Refer karo & Earn karo</b>\n"
            "<i>Har dost par ${reward} kamao — unhe {discount}% saving milegi</i>\n\n"
            "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\n"
            "\U0001f4ca <b>Aapke Stats</b>\n"
            "<blockquote>\U0001f4b5 Earned   <b>${earned}</b>\n"
            "\U0001f465 Referrals <b>{referrals}</b>\n"
            "\u2705 Rewarded  <b>{rewarded}</b>\n"
            "\u23f3 Pending   <b>{pending}</b></blockquote>\n\n"
            "\U0001f517 <b>Aapka Link</b>\n"
            "<code>{link}</code>\n"
            "<i>Copy Link dabao, phir friends ke saath share karo.</i>\n\n"
            "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\n"
            "\U0001f3af <b>Rewards</b>\n"
            "<blockquote>\U0001f464 <b>Aapko milega</b>\n"
            "- ${reward} bonus balance mein\n"
            "- Har dost par ek baar\n"
            "- Kisi bhi product par spend kar sakte ho\n\n"
            "\U0001f91d <b>Dost ko milega</b>\n"
            "- Pehle {orders} orders par {discount}% OFF\n"
            "- Checkout par auto-apply</blockquote>\n\n"
            "\u2699\ufe0f <b>Kaise kaam karta hai</b>\n"
            "<blockquote>1. Apna link share karo \U0001f517\n"
            "2. Dost link se join kare\n"
            "3. Dost ${min}+ kharide\n"
            "4. Aapko ${reward} turant milta hai \u2705</blockquote>\n\n"
            "\u26a0\ufe0f <b>Rules</b>\n"
            "<blockquote>- Friend ke pehle qualifying order par hi trigger hota hai\n"
            "- ${min} se kam orders count nahi hote\n"
            "- Self-referrals aur fake accounts reject honge\n"
            "- Bonus spendable hai, withdrawable nahi</blockquote>"
        ),
        "api.screen_new": (
            "\U0001f517 <b>Reseller API ka link</b>\n\n"
            "\U0001f511 <b>Aapki API key ban gayi.</b>\n"
            "<code>{key}</code>\n\n"
            "\U0001f4d6 <b>API docs:</b>\n"
            "<code>{docs}</code>\n\n"
            "\u26a0\ufe0f <b>Is key ko secret rakhein.</b>\n"
            "<i>Ye sirf ab dikh rahi hai. Baad mein prefix <code>{prefix}</code> dikhega.</i>"
        ),
        "api.screen_existing": (
            "\U0001f517 <b>Reseller API ka link</b>\n\n"
            "\U0001f511 Active key prefix: <code>{prefix}</code>\n"
            "<i>Security ke liye full key hidden hai. Lost ho gayi to regenerate karein.</i>\n\n"
            "\U0001f4d6 <b>API docs:</b>\n"
            "<code>{docs}</code>\n\n"
            "\u26a0\ufe0f <b>Apni API key secret rakhein.</b>"
        ),
        "api.regen_confirm": (
            "\u26a0\ufe0f <b>API key regenerate karein?</b>\n\n"
            "Purani key turant band ho jayegi.\n"
            "Docs yahin hain: <code>{docs}</code>"
        ),
        "api.regenerated": "API key regenerate ho gayi.",
        "start.welcome": (
            "{crown} <b>{shop}</b> {sparkle}\n"
            "<b>Premium digital products. Turant delivery.</b>\n\n"
            "Namaste <b>{name}</b> {fire}\n\n"
            "<blockquote>{rocket} <b>Shop</b> — live stock + fast checkout\n"
            "{diamond} <b>Wallet</b> — top up karke faster buy karo\n"
            "\U0001f464 <b>Profile</b> — balance, tier aur orders\n"
            "\U0001f4dc <b>History</b> — delivered orders dobara kholo\n"
            "\U0001f198 <b>Support</b> — order-linked help\n"
            "\U0001f381 <b>Refer karo & Earn karo</b> - friends bulao aur rewards kamao</blockquote>\n\n"
            "{arrow} <b>Apna move choose karo.</b>"
        ),
        "delivery.instructions": "\U0001f4cb <b>Nirdesh:</b>\n{body}",
        "deposit.balance": "\U0001f48e Abhi ka balance: <b>{balance}</b>",
        "deposit.balance_admin": "\U0001f48e Abhi ka balance: <b>\u221e unlimited</b> (admin)",
        "deposit.wallet_title": "\U0001f4b0 <b>Wallet ka control center</b> \u2728",
        "download.product": "\U0001f4e6 Product naam: <b>{name}</b>",
        "download.order": "\U0001f9fe Order number: <b>#{id}</b>",
        "download.format": "\U0001f5c2 File format: <b>{format}</b>",
        "download.items": "\U0001f522 Items count: <b>{n}</b>",
        "download.caption": "{name}\nOrder #{id}\n{n} item(s) ready",
        "history.delivered_inline": "\U0001f381 <b>Delivered code:</b> <code>{code}</code>",
        "history.delivered_label": "\U0001f381 <b>Delivered data yahan hai:</b>",
        "history.order_num": "<b>Order number #{id}</b>",
        "lang.invalid": "Language valid nahi hai.",
        "notify.balance_note": "\nNote yeh hai: {note}",
        "pay.address": "\U0001f3e6 Address yahan hai:\n<code>{address}</code>",
        "pay.amount": "\U0001f4b5 Amount dena hai: {amount}",
        "pay.confirmed_deposit": "\u2705 Deposit confirm ho gaya — wallet load ho gaya.",
        "pay.discount": "\U0001f39f Discount mila: \u2212{discount}",
        "pay.important": "\u26a0\ufe0f <b>Important dhyan dein:</b>",
        "pay.order_total": "\U0001f48e Order total: <b>{payable} USDT</b>",
        "pay.payable": "\U0001f48e Payable amount: <b>{payable}</b>",
        "pay.payment_id": "\U0001f9fe Payment number: <b>#{id}</b>",
        "pay.product": "\U0001f4e6 Product naam: <b>{name}</b>",
        "pay.product_line": "\U0001f4e6 Product naam: {prefix}<b>{name}</b>",
        "pay.qty_line": "\U0001f522 Quantity chuni: <b>{qty}</b>",
        "pay.quantity": "\U0001f522 Quantity chuni: <b>{qty}</b>",
        "pay.subtotal": "\U0001f4b5 Subtotal amount: <b>{subtotal}</b>",
        "pay.topup_label": "\U0001f4b0 Wallet top-up amount",
        "pay.uid": "\U0001f194 UID Binance: <code>{uid}</code>",
        "pay.user_missing": "User nahi mila — /start bhejein",
        "pay.wallet": "\U0001f45b Wallet balance: <b>{balance}</b>",
        "profile.balance": "\U0001f4b0 Balance amount: <b>{balance}</b>",
        "profile.tier": "\U0001f3c5 Tier level: <b>{tier}</b>",
        "profile.title": "\U0001f48e <b>Buyer Profile details</b> \u2728",
        "shop.card_bulk": "\U0001f4ca <b>Bulk deals available:</b>",
        "shop.card_price": "\U0001f4b8 Deal ka price: <b>${price}</b> / {unit}",
        "shop.card_stock": "\U0001f4e6 Stock ready: <b>{n}</b>",
        "shop.list_title": "\U0001f6cd <b>Live Digital Drops dekho</b> \u2728",
        "status.cancelled": "Cancel ho gaya",
        "status.delivered": "Deliver ho gaya",
        "status.draft": "Draft mein hai",
        "status.expired": "Expire ho gaya",
        "status.failed": "Fail ho gaya",
        "status.paid": "Paid ho gaya",
        "sum.discount": "\U0001f39f Discount mila: \u2212${discount}",
        "sum.payable": "\U0001f48e Abhi payable: <b>${payable}</b>",
        "sum.qty": "\U0001f522 Quantity chuni: <b>{qty}</b> {unit}",
        "sum.referral_discount": "\U0001f381 Referral chhoot: -${discount}",
        "sum.subtotal": "\U0001f4b5 Subtotal amount: <b>${subtotal}</b>",
        "sum.wallet": "\U0001f45b Wallet balance: <b>${balance}</b>",
        "sum.wallet_admin": "\U0001f45b Wallet: <b>\u221e admin unlimited</b>",
        "support.title": "\U0001f198 <b>Support Desk help</b> \U0001f4ac",
        "ticket.invalid": "Selection valid nahi hai.",
        "topup.title": "{method} — Wallet top-up amount",
        "tier.Bronze": "Bronze level",
        "tier.Silver": "Silver level",
        "tier.Gold": "Gold level",
        "tier.Platinum": "Platinum level",
        "tier.Diamond": "Diamond level",
    },
    "vi": {
        "btn.download_txt": "\U0001f4c4 Tải TXT",
        "btn.download_excel": "\U0001f4ca Tải Excel",
        "btn.download_yes": "\u2705 Gửi file",
        "btn.back_orders": "\u25c0\ufe0f Quay lại đơn hàng",
        "btn.refer": "\U0001f381 Giới thiệu & nhận thưởng",
        "btn.copy_link": "\U0001f517 Sao chép link",
        "btn.api_link": "\U0001f517 Link API",
        "btn.api_regenerate": "\U0001f504 Tạo lại",
        "btn.api_regenerate_yes": "\u2705 Có, tạo lại",
        "sum.referral_discount": "\U0001f381 Giảm giá giới thiệu: -${discount}",
        "pay.uid": "\U0001f194 UID Binance: <code>{uid}</code>",
        "download.confirm_title": "\U0001f4e5 <b>Xác nhận tải xuống</b>",
        "download.product": "\U0001f4e6 Sản phẩm: <b>{name}</b>",
        "download.order": "\U0001f9fe Đơn hàng: <b>#{id}</b>",
        "download.format": "\U0001f5c2 Định dạng: <b>{format}</b>",
        "download.items": "\U0001f522 Số mục: <b>{n}</b>",
        "download.prompt": "Tạo file này và gửi vào chat này?",
        "download.not_delivered": "Đơn hàng này chưa được giao.",
        "download.empty": "Không tìm thấy dữ liệu đã giao cho đơn hàng này.",
        "download.sent": "Đã gửi file \u2705",
        "download.cancelled": "Đã hủy tải xuống.",
        "download.caption": "{name}\nĐơn #{id}\n{n} mục",
        "refer.screen": (
            "\U0001f381 <b>Giới thiệu & nhận thưởng</b>\n"
            "<i>Nhận ${reward} cho mỗi bạn bè — họ tiết kiệm {discount}%</i>\n\n"
            "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\n"
            "\U0001f4ca <b>Thống kê của bạn</b>\n"
            "<blockquote>\U0001f4b5 Đã nhận   <b>${earned}</b>\n"
            "\U0001f465 Lượt giới thiệu <b>{referrals}</b>\n"
            "\u2705 Đã thưởng  <b>{rewarded}</b>\n"
            "\u23f3 Đang chờ   <b>{pending}</b></blockquote>\n\n"
            "\U0001f517 <b>Link của bạn</b>\n"
            "<code>{link}</code>\n"
            "<i>Bấm Sao chép link, rồi chia sẻ với bạn bè.</i>\n\n"
            "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\n"
            "\U0001f3af <b>Phần thưởng</b>\n"
            "<blockquote>\U0001f464 <b>Bạn nhận</b>\n"
            "- ${reward} thưởng vào số dư\n"
            "- Một lần cho mỗi bạn bè\n"
            "- Dùng được cho mọi sản phẩm\n\n"
            "\U0001f91d <b>Bạn của bạn nhận</b>\n"
            "- Giảm {discount}% cho {orders} đơn đầu\n"
            "- Tự áp dụng khi thanh toán</blockquote>\n\n"
            "\u2699\ufe0f <b>Cách hoạt động</b>\n"
            "<blockquote>1. Chia sẻ link của bạn \U0001f517\n"
            "2. Bạn bè tham gia qua link\n"
            "3. Bạn bè mua từ ${min}+\n"
            "4. Bạn nhận ${reward} ngay \u2705</blockquote>\n\n"
            "\u26a0\ufe0f <b>Quy định</b>\n"
            "<blockquote>- Chỉ kích hoạt cho đơn đủ điều kiện đầu tiên của bạn bè\n"
            "- Đơn dưới ${min} không được tính\n"
            "- Tự giới thiệu và tài khoản giả sẽ bị từ chối\n"
            "- Thưởng dùng để mua hàng, không rút được</blockquote>"
        ),
        "api.screen_new": (
            "\U0001f517 <b>Link API reseller</b>\n\n"
            "\U0001f511 <b>API key của bạn đã được tạo.</b>\n"
            "<code>{key}</code>\n\n"
            "\U0001f4d6 <b>Tài liệu API:</b>\n"
            "<code>{docs}</code>\n\n"
            "\u26a0\ufe0f <b>Giữ key này bí mật.</b>\n"
            "<i>Key chỉ hiển thị lúc này. Sau đó chỉ hiện prefix <code>{prefix}</code>.</i>"
        ),
        "api.screen_existing": (
            "\U0001f517 <b>Link API reseller</b>\n\n"
            "\U0001f511 Prefix key đang dùng: <code>{prefix}</code>\n"
            "<i>Full key được ẩn để bảo mật. Hãy tạo lại nếu bạn làm mất.</i>\n\n"
            "\U0001f4d6 <b>Tài liệu API:</b>\n"
            "<code>{docs}</code>\n\n"
            "\u26a0\ufe0f <b>Giữ API key của bạn bí mật.</b>"
        ),
        "api.regen_confirm": (
            "\u26a0\ufe0f <b>Tạo lại API key?</b>\n\n"
            "Key cũ sẽ ngừng hoạt động ngay.\n"
            "Tài liệu vẫn ở đây: <code>{docs}</code>"
        ),
        "api.regenerated": "Đã tạo lại API key.",
    },
    "zh": {
        "btn.download_txt": "\U0001f4c4 下载 TXT",
        "btn.download_excel": "\U0001f4ca 下载 Excel",
        "btn.download_yes": "\u2705 发送文件",
        "btn.back_orders": "\u25c0\ufe0f 返回订单",
        "btn.refer": "\U0001f381 推荐赚奖励",
        "btn.copy_link": "\U0001f517 复制链接",
        "btn.api_link": "\U0001f517 API 链接",
        "btn.api_regenerate": "\U0001f504 重新生成",
        "btn.api_regenerate_yes": "\u2705 是，重新生成",
        "sum.referral_discount": "\U0001f381 推荐折扣：-${discount}",
        "download.confirm_title": "\U0001f4e5 <b>确认下载</b>",
        "download.product": "\U0001f4e6 商品：<b>{name}</b>",
        "download.order": "\U0001f9fe 订单：<b>#{id}</b>",
        "download.format": "\U0001f5c2 格式：<b>{format}</b>",
        "download.items": "\U0001f522 数量：<b>{n}</b>",
        "download.prompt": "生成这个文件并发送到这里？",
        "download.not_delivered": "该订单尚未交付。",
        "download.empty": "未找到该订单的已交付数据。",
        "download.sent": "文件已发送 \u2705",
        "download.cancelled": "下载已取消。",
        "download.caption": "{name}\n订单 #{id}\n{n} 项",
        "refer.screen": (
            "\U0001f381 <b>推荐赚奖励</b>\n"
            "<i>每位好友赚 ${reward} — 好友节省 {discount}%</i>\n\n"
            "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\n"
            "\U0001f4ca <b>你的统计</b>\n"
            "<blockquote>\U0001f4b5 已赚   <b>${earned}</b>\n"
            "\U0001f465 推荐数 <b>{referrals}</b>\n"
            "\u2705 已奖励  <b>{rewarded}</b>\n"
            "\u23f3 待处理   <b>{pending}</b></blockquote>\n\n"
            "\U0001f517 <b>你的链接</b>\n"
            "<code>{link}</code>\n"
            "<i>点击复制链接，然后分享给好友。</i>\n\n"
            "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\n"
            "\U0001f3af <b>奖励</b>\n"
            "<blockquote>\U0001f464 <b>你获得</b>\n"
            "- ${reward} 奖励到账户余额\n"
            "- 每位好友仅一次\n"
            "- 可用于购买任何商品\n\n"
            "\U0001f91d <b>好友获得</b>\n"
            "- 前 {orders} 个订单享 {discount}% OFF\n"
            "- 结账时自动应用</blockquote>\n\n"
            "\u2699\ufe0f <b>如何运作</b>\n"
            "<blockquote>1. 分享你的链接 \U0001f517\n"
            "2. 好友通过链接加入\n"
            "3. 好友购买 ${min}+\n"
            "4. 你立即获得 ${reward} \u2705</blockquote>\n\n"
            "\u26a0\ufe0f <b>规则</b>\n"
            "<blockquote>- 仅好友第一个符合条件的订单触发\n"
            "- 低于 ${min} 的订单不计算\n"
            "- 自我推荐和虚假账户会被拒绝\n"
            "- 奖励可消费，不可提现</blockquote>"
        ),
        "api.screen_new": (
            "\U0001f517 <b>分销 API 链接</b>\n\n"
            "\U0001f511 <b>你的 API key 已创建。</b>\n"
            "<code>{key}</code>\n\n"
            "\U0001f4d6 <b>API 文档：</b>\n"
            "<code>{docs}</code>\n\n"
            "\u26a0\ufe0f <b>请保管好这个 key。</b>\n"
            "<i>它只会现在完整显示。之后只显示前缀 <code>{prefix}</code>。</i>"
        ),
        "api.screen_existing": (
            "\U0001f517 <b>分销 API 链接</b>\n\n"
            "\U0001f511 当前 key 前缀：<code>{prefix}</code>\n"
            "<i>出于安全，完整 key 已隐藏。如果丢失请重新生成。</i>\n\n"
            "\U0001f4d6 <b>API 文档：</b>\n"
            "<code>{docs}</code>\n\n"
            "\u26a0\ufe0f <b>请保管好你的 API key。</b>"
        ),
        "api.regen_confirm": (
            "\u26a0\ufe0f <b>重新生成 API key？</b>\n\n"
            "旧 key 会立即停止工作。\n"
            "文档仍在这里：<code>{docs}</code>"
        ),
        "api.regenerated": "API key 已重新生成。",
    },
}

for _code, _strings in LOCALIZED_LANGUAGE_FIXES.items():
    {"ar": AR, "hi": HI, "vi": VI, "zh": ZH}[_code].update(_strings)

_REFER_WELCOME_LINE_EN = "\n\U0001f381 <b>Refer & Earn</b> - invite friends + earn rewards"
_REFER_WELCOME_LINES = {
    "ar": "\n\U0001f381 <b>الإحالة والربح</b> - ادعُ أصدقاءك واربح مكافآت",
    "hi": "\n\U0001f381 <b>Refer karo & Earn karo</b> - friends bulao aur rewards kamao",
    "vi": "\n\U0001f381 <b>Giới thiệu & nhận thưởng</b> - mời bạn bè và nhận thưởng",
    "zh": "\n\U0001f381 <b>推荐赚奖励</b> - 邀请好友并获得奖励",
}
for _code, _line in _REFER_WELCOME_LINES.items():
    _cat = {"ar": AR, "hi": HI, "vi": VI, "zh": ZH}[_code]
    _cat["start.welcome"] = _cat.get("start.welcome", "").replace(
        _REFER_WELCOME_LINE_EN,
        _line,
    )


BROADCAST_HYPE_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "btn.buy_named": "Buy Now",
        "bc.restock": "<b>RESTOCK JUST LANDED - {n} new stock added for {name}</b>",
        "bc.available": "\U0001f4e6 Vault status: <b>{n} ready for instant delivery</b>",
        "bc.price": "\U0001f4b8 Deal price: <b>${price} USDT</b>",
        "bc.scarcity_left": "\u26a0\ufe0f <b>Only {n} left</b> - this batch can disappear fast.",
        "bc.scarcity_limited": "\u26a1\ufe0f <b>Limited drop</b> - hot stock, instant delivery, no waiting.",
        "bc.pricedrop": "\U0001f4a5 <b>PRICE DROP ALERT</b>",
        "bc.was": "\U0001f53b Was: <s>{old} USDT</s>",
        "bc.now": "\U0001f4b8 Now live: <b>{new} USDT</b>",
        "bc.limited_offer": "<i>Lock the cheaper price while it is live.</i>",
        "bc.new_product": "<b>NEW DROP IS LIVE</b>",
        "bc.name": "\U0001f4e6 Featured: <b>{name}</b>",
        "bc.open_shop": "<b>Tap below to open the product and checkout.</b>",
        "bc.admin_notification": "<b>Akunding Store Broadcast</b>",
    },
}

for _code, _strings in BROADCAST_HYPE_STRINGS.items():
    {"en": EN, "ar": AR, "hi": HI, "vi": VI, "zh": ZH}[_code].update(_strings)


PAYMENT_COPY_BUTTON_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "btn.copy_binance_id": "\U0001f194 Copy Binance ID",
        "btn.copy_address": "\U0001f3e6 Copy address",
        "btn.copy_exact_amount": "\U0001f4b8 Copy exact amount",
    },
    "ar": {
        "btn.copy_binance_id": "\U0001f194 نسخ Binance ID",
        "btn.copy_address": "\U0001f3e6 نسخ العنوان",
        "btn.copy_exact_amount": "\U0001f4b8 نسخ المبلغ بالضبط",
    },
    "hi": {
        "btn.copy_binance_id": "\U0001f194 Binance ID copy",
        "btn.copy_address": "\U0001f3e6 Address copy",
        "btn.copy_exact_amount": "\U0001f4b8 Exact amount copy",
    },
    "vi": {
        "btn.copy_binance_id": "\U0001f194 Sao chép Binance ID",
        "btn.copy_address": "\U0001f3e6 Sao chép địa chỉ",
        "btn.copy_exact_amount": "\U0001f4b8 Sao chép số tiền chính xác",
    },
    "zh": {
        "btn.copy_binance_id": "\U0001f194 复制 Binance ID",
        "btn.copy_address": "\U0001f3e6 复制地址",
        "btn.copy_exact_amount": "\U0001f4b8 复制准确金额",
    },
}

for _code, _strings in PAYMENT_COPY_BUTTON_STRINGS.items():
    {"en": EN, "ar": AR, "hi": HI, "vi": VI, "zh": ZH}[_code].update(_strings)


TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": EN, "ar": AR, "hi": HI, "vi": VI, "zh": ZH,
}
