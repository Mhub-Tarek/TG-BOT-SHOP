# AI Project Guide — Telegram Shop (Akunding Store)

**Read this file first.** It explains this project for the owner (Ahmed) and for any AI assistant helping later.

The owner is **not highly technical**. Prefer doing changes for them (edit files, push to VPS, restart) rather than long manual instructions. Explain outcomes in plain language.

---

## What this project is

A **self-hosted Telegram shop** that sells digital products (license keys, promo codes, accounts, etc.).

| Component | Role |
|-----------|------|
| **Telegram bot** | Customers browse, buy, pay, receive codes automatically |
| **Admin dashboard** | Web UI at port 8000 — products, stock, orders, users, coupons |
| **Payments** | USDT via wallet balance, Binance Pay UID, TRC20, BEP20 |
| **Database** | **SQLite** file on the VPS (`shop.db`) — no Supabase, no external DB |

**Entry point:** `run.py` runs the bot + FastAPI dashboard in **one process** (systemd service on VPS).

**Tech stack:** Python 3.12, aiogram 3, FastAPI, Jinja2, aiosqlite, APScheduler, httpx, pydantic-settings.

---

## Live production setup (IMPORTANT)

| Item | Value |
|------|--------|
| **Hosting** | Hostinger VPS (KVM 1), Ubuntu 24.04 |
| **Server IP** | `72.60.191.224` |
| **Telegram bot** | [@Akunding_store_bot](https://t.me/Akunding_store_bot) |
| **Shop display name** | Akunding Store |
| **Admin dashboard** | http://72.60.191.224:8000 |
| **App on server** | `/opt/telegram-shop` |
| **Python venv on server** | `/opt/telegram-shop/.venv` |
| **Database file** | `/opt/telegram-shop/shop.db` |
| **System service** | `telegram-shop` (systemd, auto-starts on reboot) |
| **Service user** | `shopbot` (non-login system user) |
| **Logs** | `/var/log/telegram-shop/app.log` and `journalctl -u telegram-shop` |
| **Owner PC path** | `C:\Users\ahmed\Desktop\telegram-shop` |

### Rule: only ONE bot instance at a time

Telegram allows **one** polling connection per bot token. If both the VPS and the owner's PC run `python run.py`, the bot breaks with `TelegramConflictError`.

- **Production = VPS only**
- **Local PC** = testing only; stop VPS bot first (`systemctl stop telegram-shop`), or stop local when done

---

## Database — SQLite on VPS (not Supabase)

**There is no separate database service.** Everything is stored in one file:

```
/opt/telegram-shop/shop.db
```

| Question | Answer |
|----------|--------|
| Does Hostinger replace Supabase? | **Yes, for this project.** The VPS runs the app; SQLite is the database on the same machine. |
| Do we need Supabase? | **No**, unless you later want cloud Postgres, multi-server scaling, or a remote dashboard DB. |
| Who creates tables? | `db.init_db()` on startup — schema + migrations in `db.py` `_migrate()` |
| Backups | **Manual** — copy `shop.db` periodically (Hostinger does not auto-backup app data) |
| Journal mode | WAL enabled for safer concurrent reads |

### Main tables

| Table | Purpose |
|-------|---------|
| `users` | Telegram users, wallet `balance`, `referrer_id`, username, first_name |
| `products` | Name, price, emoji, description, features, **brand**, **icon_custom_emoji_id**, **delivery_instructions**, active flag |
| `stock_items` | Digital codes/keys per product (`content`, `sold` flag, `order_id` when sold) |
| `orders` | Purchases — `payable`, status, payment method, coupon discount |
| `payments` | Pending/completed deposits — amount, coin, network, txid; `order_id` NULL = wallet top-up |
| `coupons` | Discount codes (percent or fixed) |
| `pricing_tiers` | Bulk quantity discounts per product |
| `balance_adjustments` | Audit log when admin changes user balance |
| `settings` | Key/value store (reserved for future use) |

### Product columns (migrations)

Added via `_migrate()` in `db.py`:

- `icon_custom_emoji_id` — optional manual Telegram custom emoji override
- `brand` — slug from `keyboards/brands.py` (e.g. `elevenlabs`, `gemini`)
- `delivery_instructions` — optional text sent to buyer after successful delivery (never shown before purchase)
- `sort_order` — admin-controlled display order (May 2026). Backfilled from `id` on first migration; new products land at the end (`MAX(sort_order)+1`). `list_products` orders by `sort_order ASC, id ASC`. Reordered up/down from the dashboard via `db.reorder_product()`.

### pricing_tiers columns

- `min_qty` — quantity threshold for the tier
- `discount_pct` — **legacy** percentage (kept at 0 for new rows; old rows still resolve via `services.pricing`)
- `unit_price` — **current** model (May 2026): explicit per-unit USDT price for that quantity band. Bulk pricing is now a dollar price, not a percent. See [Bulk pricing (dollar tiers)](#bulk-pricing-dollar-tiers).

### balance_adjustments columns

| Column | Purpose |
|--------|---------|
| `user_id` | Target user |
| `delta` | Change amount (+/-) |
| `balance_before` / `balance_after` | Snapshot for audit |
| `note` | Admin reason (optional) |
| `created_at` | Unix timestamp |

### Order statuses

`draft` → `awaiting_payment` → `paid` → `delivered`  
Also: `failed`, `expired`, `cancelled`

### Payment statuses

`pending` → `confirmed`  
Also: `expired`, `cancelled`

**Important:** Orders use **`payable`** (final amount after discounts), not `total`. Profile and history handlers must use `payable`.

---

## Telegram bot — customer features

### Main menu (`keyboards/main.py`)

```
        🛍 Shop                      (wide)
   💰 Wallet   |   💎 My Profile
   🆘 Support  |   🧿 History
```

Vexoran-style layout: Shop is a single wide button, then a 2×2 grid below. All callback data is unchanged — `menu:shop`, `menu:wallet`, `menu:profile`, `menu:support`, `menu:history`.

### Welcome text (`handlers/start.py`)

Crown-prefixed greeting + tagline + native `<blockquote>` menu overview + CTA. Decorated with `mix_icons` sparkles (👑 ✨ ⭐ 🎁 ⚡ 👇) that render as colored custom emoji once the Mix Emoji pack is grabbed (else plain unicode). The old "🔥 40+ top brands…" line was removed (May 2026) to keep the first screen light.

```
👑 Welcome to Akunding Store! ✨

Hey {first_name} ⭐

🎁 Premium digital products at the best prices.
⚡ Fast, secure & fully automated delivery.

┃ 🛍 Shop — Browse & buy products
┃ 💰 Wallet — Add funds & manage balance
┃ 💎 My Profile — Balance & orders
┃ 🧿 History — Your past purchases
┃ 🆘 Support — Get help

Choose an option below to continue! 👇 ✨
```

The icon constants live in `keyboards/icons.py` — single source of truth. Decorative glyphs use `keyboards/mix_icons.py` (see [Mix Emoji pack](#mix-emoji-pack-third-party-custom-emoji)).

### Bot commands

| Command | Action |
|---------|--------|
| `/start` | Register user, show main menu |
| `/start ref123456` | Register with referrer (referral % in config; UI may be disabled) |
| `/menu` | Show main menu |
| `/profile` | Customer profile screen |
| `/deposit` | Jump straight to deposit amount prompt (skips wallet balance view) |
| `/shop` | (if implemented in shop handler) Open product list |

### Callback data reference

| Callback | Handler | What it does |
|----------|---------|--------------|
| `menu:home` | `handlers/start.py` | Back to welcome + main menu |
| `menu:shop` | `handlers/shop.py` | Product catalog |
| `menu:profile` | `handlers/profile.py` | Profile (name, total spent, balance) |
| `menu:history` | `handlers/profile.py` | Last 10 orders |
| `menu:wallet` | `handlers/deposit.py` | **Wallet balance view only** |
| `menu:deposit` | `handlers/deposit.py` | Start deposit flow (ask amount) |
| `menu:support` | `handlers/support.py` | Support link |
| `dep:do:{method}` | `handlers/deposit.py` | Create top-up payment for chosen method |

### Wallet vs Deposit (IMPORTANT — fixed May 2026)

**Wallet** and **Deposit** are separate actions:

```
👛 Wallet  →  menu:wallet  →  shows balance + [Deposit] [Back to menu]
💳 Deposit →  menu:deposit →  asks amount → pick method → pay
```

Wallet screen text (`handlers/deposit.py` → `_wallet_text()`):

```
👛 Wallet

Balance: $X.XX

Use Deposit below to add funds.
```

Keyboard: `wallet_menu()` in `keyboards/main.py` — **Deposit** + **Back to menu**.

**Do not** wire the Wallet button to `menu:deposit`. That was the old bug (immediate deposit prompt).

### Shop flow

1. **Shop** → paginated product list with **brand logos** on buttons (Telegram custom emoji). **Pagination:** `PAGE_SIZE = 14` in `keyboards/shop.py`; `Page X/Y` + `Prev`/`Next` buttons appear automatically once there are >14 active products. Order follows `sort_order` (admin-reorderable).
2. Tap product → detail card (features, price, stock, **bulk pricing ranges**) → quantity → **checkout summary with payment methods already on the same screen**
3. **Pay** → wallet balance or crypto methods (Binance Pay / TRC20 / BEP20). The `pay:methods:` intermediate screen was removed in May 2026 — the summary screen IS the picker. The handler still lives for backward-compat with old chat-history buttons.
4. Poller credits payment → auto-delivers stock codes in Telegram DM, **each code numbered (`1.` `2.` `3.` …)** so the buyer can confirm quantity; chunked if many/large with numbering continuing across messages — see `services/telegram_delivery.py`. If the product has `delivery_instructions`, they appear as a `📋 Instructions:` section at the end. Delivery card uses a celebration header (`🎉 Order Delivered!`).
5. If stock runs out after payment → amount credited to wallet as refund (user notified)

**Buyer broadcasts (`services/broadcast.py`)** — fan-out to all users, rate-limited 25 msgs/sec:

| Trigger | Function | Card |
|---------|----------|------|
| New stock added | `broadcast_restock` | `🆕 N new stock added…` + callback Buy button |
| Brand-new product created | `broadcast_new_product` | `🆕 NEW PRODUCT ADDED!` + green deep-link Buy button |
| Price reduced | `broadcast_price_drop` | `📉 Price Drop!` (was/now) + green deep-link Buy button |
| Admin free-text | `broadcast_admin_notification` | `👁 Admin Notification` + DM summary to admins |

Price-drop and new-product Buy buttons are **URL deep-links** (`https://t.me/<bot>?start=prod<id>`), which Telegram renders in the theme accent color (green on dark themes). `handlers/start.py` parses the `prod<id>` payload and lands the user on the product card. Both guard against a blank `BOT_USERNAME` (would make an invalid URL and fail the whole send).

### Product button format (`keyboards/shop.py`)

Mobile Telegram clients only render ~36–40 chars of button text per line before truncating. Old code budgeted up to 64 (the API max) and the price got clipped off long names.

`MAX_BUTTON_TEXT = 36`. Format mirrors reference shops:

| Stock state | Button text |
|---|---|
| In stock | `ElevenLabs 1 Month Crea… [5] — $1.50` |
| Out of stock | `ElevenLabs 1 Months Coupo… ❌ — $1.50` |

Suffix (`[stock] — $price` or `❌ — $price`) is reserved first; only the name gets `…`-truncated. Price is always the last text to vanish on any device. Fallback path (no custom-emoji icon) shrinks the budget by `GLYPH_BUDGET = 2` to make room for the inlined glyph prefix.

### Profile (`handlers/profile.py`)

- **Profile screen text:** `💎 My Profile` header, then stacked rows — 👋 name, 🏅 tier, 💰 balance, 🛍 total spent (sum of `payable` on paid/delivered orders)
- **Loyalty tier (May 2026):** Bronze → Silver → Gold → Platinum → Diamond at $0 / $50 / $200 / $500 / $1000 lifetime spend. Shows a "$X more to reach <next tier>" nudge until Diamond. Thresholds in `_TIERS` in `handlers/profile.py`.
- **Purchase history:** `📜 Purchase history` header, last 10 orders with product name, quantity, amount, status, timestamp
- Keyboard: `profile_menu()` — Profile, Purchase history, Wallet, Back
- Decorative glyphs via `mix_icons` (sparkle, diamond, tier badges) with unicode fallback.

### Deposit flow (`handlers/deposit.py`)

1. User taps **Deposit** (from wallet screen) or runs `/deposit`
2. FSM state `DepositFlow.waiting_amount` — user replies with dollar amount; must be between `MIN_DEPOSIT_USDT` (default $1) and `MAX_DEPOSIT_USDT` (default $5 000). The max protects the partial UNIQUE index on `(coin, network, unique_amount)` from a single mega-deposit reserving a round amount for the full TTL.
3. `DepositFlow.waiting_method` — pick Binance Pay / TRC20 / BEP20
4. Shows payment instructions with **exact round amount** (e.g. `$10` not `$10.0001`)
5. Poller confirms → balance credited → Telegram notification

The deposit prompt now shows both bounds: `Minimum: $1` / `Maximum: $5000`.

Clean dollar amounts in UI via `keyboards/shop.format_coin_amount()` and `ic.money()`.

### Support

Links to `SUPPORT_USERNAME` from `.env` (opens Telegram chat).

### Bot security (`handlers/security.py`)

Customer callbacks are guarded so users cannot tamper with each other's orders:

| Helper | Blocks |
|--------|--------|
| `callback_user()` | Non-private chats; users who never ran `/start` |
| `ensure_order_owner()` | Paying or changing someone else's order |
| `ensure_payment_owner()` | Viewing or acting on someone else's deposit invoice |

Used in `handlers/shop.py`, `handlers/payment.py`, `handlers/deposit.py`.

---

## Payments — how matching works

Poller runs every **30s** (`POLL_INTERVAL_SECONDS`) via APScheduler in `bot.py` → `services/payments.py` → `poll_payments()`.

**Order of operations:** poll pending payments first, **then** expire overdue ones — so a payment that lands in the last seconds of its TTL still gets matched before expiry.

| Method | Binance API | Match by |
|--------|-------------|----------|
| **Binance Pay** | `/sapi/v1/pay/transactions` | Exact USDT amount + coin + **receiver UID must match** `BINANCE_PAY_UID` |
| **TRC20 / BEP20** | `/sapi/v1/capital/deposit/hisrec` | Exact amount + coin + network + **deposit address must match** configured address |

### Anti-false-match rules (May 2026 security pass)

These prevent old unrelated Binance transactions from crediting a new pending payment:

| Check | Purpose |
|-------|---------|
| **Transaction time window** | Tx timestamp must be ≥ payment `created_at` − 120s (`PAYMENT_TIME_GRACE_SECONDS`) |
| **Exact amount** | `find_pending_payment_by_amount()` uses tight float tolerance (`< 0.00005`) |
| **Receiver / address** | Binance Pay UID and on-chain deposit address must match the invoice |
| **Txid dedup** | Same `txid` cannot confirm twice |
| **Status gates** | `try_transition_order_status()` — only first payment settles an order; losers refund to wallet |

**Known past incident (fixed):** Order #16 was falsely matched to an old $2.50 Binance Pay tx from before the bot existed. The time-window + receiver checks above prevent this going forward.

### Amount model (current)

- **Round amounts** — e.g. customer pays exactly `$10` USDT
- **One pending payment** per `(coin, network, unique_amount)` — enforced by partial UNIQUE index in SQLite
- **No payment note** required from buyer (note still stored internally but hidden from deposit UI)
- Dedup by **transaction ID** (`txid`) — unique index on confirmed txids
- Pending payments **expire** after `PAYMENT_TTL_MINUTES` (default 15)

### Payment creation safety

- Switching payment method on same order cancels previous pending payments (`cancel_pending_payments_for_order`)
- Wallet checkout bypasses `payments` table entirely (instant debit from balance)
- **Insufficient wallet balance** does **not** cancel pending crypto invoices — user can still pay by crypto
- Top-ups have `order_id = NULL` in payments table
- Settlement pipeline: `services/delivery.py` → `confirm_and_settle()` (status gates, out-of-stock wallet refund, double-pay refund)

### Known scale limitation

At high traffic, two users depositing the **same round amount** at the same time can collide. Future options: unique sub-cent amounts (`10.0001`, `10.0002`) or note matching. Owner chose to keep simple round amounts for now.

### Payment button icons

Custom Telegram emoji pack with **full-color brand icons** (Binance, USDT, wallet):

- Setup: `scripts/setup_payment_emojis.py`
- IDs: `keyboards/payment_emoji_ids.json`
- Pack name pattern: `AkundingPayColor_by_Akunding_store_bot`
- Must use `needs_repainting: false` (otherwise icons appear white on dark theme)

Health check: `scripts/binance_health_check.py`  
Diagnostics: `scripts/diag_payments.py`  
Order forensics: `scripts/investigate_order.py <order_id>` (VPS — order + payments + Binance Pay tx)  
Stale-match audit: `scripts/audit_old_tx_matches.py` (find confirmed payments where tx predates invoice)

### Startup clock skew check

`bot.py` → `check_clock_skew()` compares local time to Binance server time at startup. Drift > 5s logs a warning (signed API calls fail silently otherwise).

---

## Product brand logos (shop buttons)

Shop list buttons show **real colorful brand logos** (ElevenLabs, Gemini, Claude, Netflix, etc.) via Telegram **custom emoji** on inline buttons.

### 43 brands

Defined in `keyboards/brands.py` → `BRAND_LABELS`:

Adobe, Amazon, Apple, Canva, CapCut, ChatGPT, Claude, **Coursera**, Cursor, Discord, Disney+, Dropbox, Duolingo, ElevenLabs, ExpressVPN, Figma, Framer, Gemini, GitHub, Grammarly, Grok, JetBrains, LinkedIn, Manus, Midjourney, Microsoft 365, Mobbin, Netflix, NordVPN, Notion, Perplexity, Picsart, PlayStation, Quillbot, Replit, Slack, Spotify, Steam, Telegram, Twitch, Xbox, YouTube, Zoom.

Auto-detect brand from product name: `product_brand()` in `keyboards/brands.py`.

Coursera (added May 2026) uses the **solid blue-C** brand mark, pinned via `BRAND_ICON_URL` to the 128px favicon (the Iconify/Simple-Icons "coursera" entries are the wordmark/letterform, not the icon).

### Logo resolution order (`keyboards/product_icons.py`)

1. Manual `icon_custom_emoji_id` on product (advanced override)
2. Product **brand** field (admin picker or auto-detect from product name)
3. Out-of-stock → red ❌ icon
4. Emoji color dot fallback (`🟢` `🔵` etc. from `product_emoji_ids.json`)
5. Default dot

### Setup scripts

| Script | Output |
|--------|--------|
| `scripts/setup_brand_logos.py` | Downloads color logos (Iconify + Simple Icons + favicons + direct URLs), **recreates the whole** Telegram emoji pack, writes `keyboards/brand_logo_ids.json`. ⚠️ Reissues every brand's `custom_emoji_id` — only use for a full rebuild. |
| `scripts/add_brand_logo.py <slug> [--replace]` | **Incremental** (May 2026): adds ONE brand to the existing pack via `addStickerToSet`, preserving all other IDs. `--replace` first deletes the brand's old sticker (used to swap Coursera wordmark → blue-C). Merges the new id into `brand_logo_ids.json` + saves the preview PNG. |
| `scripts/setup_mix_emojis.py <short_name>` | Snapshots a third-party custom-emoji pack by URL slug → `keyboards/mix_emoji_ids.json` (see [Mix Emoji pack](#mix-emoji-pack-third-party-custom-emoji)). |
| `scripts/setup_product_emojis.py` | Color dots + out-of-stock icon → `keyboards/product_emoji_ids.json` |
| `scripts/assign_product_brands.py` | Auto-set `products.brand` from product name keywords |

Run on VPS (requires bot owner Premium or Fragment username for custom emoji):

```bash
# Add one brand without disturbing the others (preferred):
cd /opt/telegram-shop && sudo -u shopbot .venv/bin/python scripts/add_brand_logo.py coursera
# Swap an existing brand's artwork:
sudo -u shopbot .venv/bin/python scripts/add_brand_logo.py coursera --replace
# Full rebuild (reissues ALL ids — rarely needed):
sudo -u shopbot .venv/bin/python scripts/setup_brand_logos.py
```

Admin logo previews served from `/brand-logos/*.png` (static mount of `assets/brand-logos/`).

### Logo picker (admin)

Jinja macro: `webapp/templates/_logo_picker.html`

- **Must receive** `brand_list` and `previews` as macro arguments (not global context) — empty grid bug if missing
- Folded by default — shows current brand + thumbnail; click **Change logo** to expand
- Search filter + **None** option to clear brand
- Auto-collapses after selection

---

## Mix Emoji pack (third-party custom emoji)

The bot can render emojis from **any public third-party custom-emoji pack** (e.g. `@OrderEmoji`'s "Mix Emoji ✨") to make messages more colorful. Used across welcome / profile / shop / delivery / wallet / support flows.

- **Helper:** `keyboards/mix_icons.py` → `tge("⭐")` returns `<tg-emoji emoji-id="…">⭐</tg-emoji>` if the pack has that emoji, else the plain unicode glyph. Safe to sprinkle anywhere — degrades gracefully when no pack is loaded. `pack_available()`, `get(char, index)`, `reload()` round it out.
- **Snapshot file:** `keyboards/mix_emoji_ids.json` — `{name, title, by_emoji, flat, count}`. Per-instance (not committed); absent on a fresh install → everything falls back to unicode.
- **One-tap setup:** Telegram admin command **`/grabemoji`** — send/forward a message containing any custom emoji from the target pack (or reply to one). `services/emoji_pack.py` resolves the owning set via `getCustomEmojiStickers` → `getStickerSet`, saves every id, and hot-reloads the cache. Bots may send any public custom-emoji id, so the pack need not be owned.
- **Scripted alternative:** `scripts/setup_mix_emojis.py <short_name>` if you have the `t.me/addemoji/<short_name>` slug.

---

## Admin dashboard (FastAPI + Jinja)

**URL:** http://72.60.191.224:8000  
**Login:** `WEB_ADMIN_PASSWORD` from `.env` (signed cookie, 7-day TTL)  
**Rate limit:** 5 failed logins per IP per 5 minutes  
**Favicon:** gradient shopping-bag SVG at `webapp/static/favicon.svg`, linked from `base.html` + `login.html` (May 2026)

### Pages and routes

| Route | Method | Features |
|-------|--------|----------|
| `/` | GET | Redirect to dashboard or login |
| `/login` | GET/POST | Password login |
| `/logout` | GET | Clear session |
| `/dashboard` | GET | Stats (users, orders, revenue, payments, stock, pending orders) + recent 20 orders **with Delivered items column** |
| `/products` | GET | Product list with stock, tiers, logo picker, reorder, bulk-pricing |
| `/products/new` | POST | Create product — **broadcasts `NEW PRODUCT ADDED!` to all users** (May 2026) |
| `/products/{id}/edit` | POST | Update product (name, price, brand, logo, delivery instructions, etc.). **Lowering the price fires a `Price Drop!` broadcast** (May 2026) |
| `/products/{id}/move/{up\|down}` | POST | Reorder product one step (May 2026) |
| `/products/{id}/toggle` | POST | Activate/deactivate (keeps product in DB) |
| `/products/{id}/delete` | POST | **Always hard-deletes** (May 2026): cancels pending payments, NULLs `order_id` on other payments (audit row preserved), wipes orders for this product, deletes product (stock cascades via FK) |
| `/products/{id}/stock` | POST | Bulk paste stock lines — broadcasts restock |
| `/products/{id}/stock/{item_id}/delete` | POST | Delete one unsold stock item |
| `/products/{id}/stock/delete-selected` | POST | Bulk delete selected unsold items |
| `/products/{id}/tier` | POST | Add/update pricing tier (**per-unit $ price**) |
| `/products/{id}/tier/delete` | POST | Remove a pricing tier (May 2026) |
| `/orders` | GET | All orders (500 max) **with Delivered items column** + **search box** (`?q=` by order #, Telegram ID, or @username — May 2026) |
| `/payments` | GET | Payment history |
| `/users` | GET | User list with balances + search (`?q=`) |
| `/users/{id}/balance` | POST | Balance adjust (add / subtract / set) + Telegram notify |
| `/coupons` | GET | Coupon list |
| `/coupons/new` | POST | Create coupon |
| `/coupons/{id}/toggle` | POST | Enable/disable coupon |

### Products page — key features

- **Logo picker:** Collapsible 43-brand grid with search (see above)
- **Manage stock:** View available stock items (ID + content), delete one or bulk delete, add new lines at bottom. Shows **Available / Sold** counts. Only **unsold** items can be deleted. List capped at **200 rows** per product in UI.
- **Edit product:** Collapsed by default to keep page light.
- **Reorder (May 2026):** ↑ Up / ↓ Down buttons per product (disabled at the ends) — sets `sort_order`, applies to both the shop and admin list.
- **Pricing tiers (May 2026):** **per-unit dollar price** per quantity band (no longer a percentage). Enter "Min qty" + "Price each ($)"; **Remove** button per tier. See [Bulk pricing (dollar tiers)](#bulk-pricing-dollar-tiers).
- **Deactivate vs Delete (May 2026):**
  - **Deactivate** (toggle button) — flips `active=0`, hides product from shop, keeps it in the dashboard list. Reversible with **Activate**.
  - **Delete** (red button) — fully removes the product, its stock items, and that product's order rows. Pending payments on those orders are cancelled (frees the unique-amount slot); confirmed/expired/cancelled payments survive with `order_id` cleared so the payment audit log stays intact. The confirm dialog spells out the consequence before submission.

### User balance control

`POST /users/{id}/balance` — form fields: `action` (`add` / `subtract` / `set`), `amount`, `note`.

- Calls `db.adjust_user_balance()` or `db.set_user_balance()`
- Logs every change to `balance_adjustments`
- Sends Telegram message to user: adjustment amount + new balance + optional note

Use for refunds, manual credits, corrections.

### Delivery instructions (per product)

Optional text that gets sent to the buyer **only after successful delivery** — e.g. redemption steps, login URLs, usage tips. The buyer never sees instructions before purchasing.

| Where | Behaviour |
|-------|-----------|
| Admin dashboard → Products → Edit | Textarea field "Delivery instructions" |
| During checkout / browsing | **Not visible** to buyer |
| After payment confirmed & codes delivered | Appended as `📋 Instructions:` section at end of the delivery message |
| Works for | Both crypto (auto-poll) and wallet payments |

Stored in `products.delivery_instructions` (TEXT column). Passed through `code_delivery_messages(instructions=...)` in `services/telegram_delivery.py`.

### Delivered items — see exact keys/accounts sent (May 2026)

When an order is **paid** or **delivered**, the dashboard shows exactly what digital content was sent to the buyer.

| Where | Column |
|-------|--------|
| `/orders` | **Delivered items** |
| `/dashboard` → Recent orders | **Delivered items** |

Click the row (e.g. **“1 delivered”**) to expand a list of keys/accounts. Data comes from `stock_items` rows that were sold and linked to that order (`sold=1`, `order_id` set at delivery via `claim_stock_items()`).

| File | Role |
|------|------|
| `db.delivered_contents_by_orders()` | Batch query: order_id → list of content strings |
| `webapp/templates/_order_delivered.html` | Jinja macro `delivered_items()` |
| `webapp/static/admin.css` | `.delivered-items`, `.delivered-code` styles |

If a very old order shows **“No stock linked”**, delivery may have happened before stock was tied to orders in the DB — new orders should show content correctly.

---

## Telegram admin commands

Owner's Telegram ID must be in `ADMIN_IDS`:

| Command | Purpose |
|---------|---------|
| `/admin` | Command list |
| `/addproduct` | Guided product creation (FSM) — **broadcasts `NEW PRODUCT ADDED!`** on completion |
| `/addstock` | Upload stock to a product — broadcasts restock |
| `/addtier` | Bulk pricing tier — **per-unit $ price** (asks min qty, then price each) |
| `/setprice` | Change a product's price; **lowering it broadcasts `Price Drop!`** to all users |
| `/addcoupon` | Create coupon |
| `/coupons` | List coupons |
| `/stats` | Sales stats |
| `/broadcast` | Message all users |
| `/notifyrestock <id> <n>` | Manually fire a restock broadcast |
| `/grabemoji` | Snapshot a third-party custom-emoji pack (send/reply with one of its emojis) |

Full product/logo/stock management is easier via **web dashboard**.

---

## Bulk pricing (dollar tiers)

**May 2026:** bulk pricing switched from a percentage discount to an **explicit per-unit USDT price** per quantity band — matches reference shops and is clearer for the owner.

- **Model:** each `pricing_tiers` row is `(min_qty, unit_price)`. Buying `>= min_qty` charges `unit_price` per unit. Legacy percentage rows (`unit_price IS NULL`) still resolve by converting from `discount_pct`.
- **Logic:** `services/pricing.py`
  - `effective_unit_price(base, qty, tiers)` → charges the band the qty falls into (the tier with the **highest `min_qty ≤ qty`**), so the price charged always equals the price displayed. Falls back to base below the first tier.
  - `format_tier_summary(base, tiers, unit_label)` → range display, e.g.
    ```
    • From 1-9: $2.00 / code
    • From 10+: $1.80 / code
    ```
    The first line is the base price up to the first tier; the last tier is open-ended (`+`). `unit_label` is HTML-escaped. Returns `""` when there are no tiers, so the block only shows when set.
  - `tier_rows_for_display(base, tiers)` → flat `{min_qty, unit_price}` for the dashboard template.
- **Entry:** dashboard "Min qty" + "Price each ($)" (rejects qty < 2 or price ≤ 0); Telegram `/addtier` asks min qty then per-unit price (warns if ≥ base price). Both upsert by `min_qty`; **Remove** deletes a tier.

---

## FSM states (`states.py`)

| Group | States | Used for |
|-------|--------|----------|
| `CheckoutFlow` | `waiting_custom_quantity` | Custom qty input during buy |
| `DepositFlow` | `waiting_amount`, `waiting_method` | Wallet top-up |
| `AdminFlow` | product add, stock add, **tier add (`add_tier_price`)**, coupon add, broadcast, **`set_price_pick_product` / `set_price_amount`** | Telegram /admin guided flows |

`add_tier_price` (renamed from `add_tier_pct` in May 2026) collects the per-unit dollar price. `/grabemoji` is a single-shot command (no FSM). Always `state.clear()` when returning to main menu.

---

## Secrets and config (`.env`)

All secrets live in **`.env`** at project root. **Never commit `.env` to git.** Never paste tokens/keys/passwords in chat.

Copy from `.env.example` for reference. Key variables:

| Variable | Purpose |
|----------|---------|
| `BOT_TOKEN` | From @BotFather |
| `BOT_USERNAME` | `Akunding_store_bot` (no @) |
| `ADMIN_IDS` | Owner Telegram user ID(s) — `config.py` accepts `6393267365` or `[6393267365]` |
| `SHOP_NAME` | Branding in bot + dashboard |
| `WELCOME_BLURB` | Optional welcome text (menu now uses quick guide in code) |
| `SUPPORT_USERNAME` | Telegram username for support (no @) |
| `CHANNEL_URL` | Empty — channel button removed |
| `REFERRAL_PERCENT` | Referral bonus on deposits (default 5%); feature exists in code |
| `DEFAULT_DELIVERY_PROMISE` | Shown on product cards |
| `BINANCE_API_KEY` / `BINANCE_API_SECRET` | Read-only; **whitelist VPS IP** `72.60.191.224` |
| `BINANCE_PAY_UID` | Enables Binance Pay button |
| `USDT_TRC20_ADDRESS` / `USDT_BEP20_ADDRESS` | Enables on-chain payment buttons |
| `PAYMENT_TTL_MINUTES` | Default 15 — pending payment expiry |
| `POLL_INTERVAL_SECONDS` | Default 30 — deposit poller interval |
| `MIN_DEPOSIT_USDT` | Minimum wallet top-up (default 1.0) |
| `MAX_DEPOSIT_USDT` | Maximum wallet top-up (default 5000.0) — protects the partial UNIQUE index from a giant pending payment hogging a round amount |
| `WEB_ADMIN_PASSWORD` | Dashboard login. **Must not be left as `change-me-now`** — startup logs a loud WARNING if so. |
| `WEB_SESSION_SECRET` | Signs admin session cookies. **Must not be left as `change-me-too`** — startup logs a loud WARNING if so (forgeable sessions). |
| `WEB_PORT` | Default 8000 |
| `WEB_HOST` | Default `0.0.0.0` on VPS |
| `WEB_SECURE_COOKIES` | Set `true` behind HTTPS |
| `WEB_LOGIN_MAX_ATTEMPTS` | Default 5 |
| `WEB_LOGIN_WINDOW_SECONDS` | Default 300 |

After any `.env` change on VPS → `systemctl restart telegram-shop`

---

## SSH access and deploy

### SSH key

| Item | Location |
|------|----------|
| Private key | `C:\Users\ahmed\.ssh\telegram_shop_deploy` |
| Server | `root@72.60.191.224` |

**Test:**
```powershell
ssh -i $env:USERPROFILE\.ssh\telegram_shop_deploy root@72.60.191.224 "echo ok"
```

### Push `.env` only

```powershell
scp -i $env:USERPROFILE\.ssh\telegram_shop_deploy `
  C:\Users\ahmed\Desktop\telegram-shop\.env `
  root@72.60.191.224:/opt/telegram-shop/.env

ssh -i $env:USERPROFILE\.ssh\telegram_shop_deploy `
  root@72.60.191.224 "systemctl restart telegram-shop"
```

### Push individual code files

**Important:** scp one file at a time to the correct directory (don't pass two remote paths in one scp command).

```powershell
$key = "$env:USERPROFILE\.ssh\telegram_shop_deploy"
$remote = "root@72.60.191.224"   # do NOT use $Host — PowerShell reserves that variable

scp -i $key C:\Users\ahmed\Desktop\telegram-shop\handlers\deposit.py "${remote}:/opt/telegram-shop/handlers/deposit.py"
scp -i $key C:\Users\ahmed\Desktop\telegram-shop\keyboards\main.py "${remote}:/opt/telegram-shop/keyboards/main.py"

ssh -i $key $remote "chown -R shopbot:shopbot /opt/telegram-shop && systemctl restart telegram-shop"
# IMPORTANT: always chown shopbot:shopbot after scp — the service runs as shopbot, not root
```

### Full redeploy

```powershell
powershell -ExecutionPolicy Bypass -File C:\Users\ahmed\Desktop\telegram-shop\deploy\deploy.ps1
```

This zips the project, uploads to VPS, runs `deploy/remote-bootstrap.sh` → `deploy/setup.sh`, restarts systemd.

**Database preserved on full redeploy:** `deploy.ps1` backs up `shop.db` + WAL/SHM to `/root/telegram-shop-backup/` before wiping `/opt/telegram-shop`, then restores after unzip — production data survives code updates.

### Health check

```powershell
ssh -i $env:USERPROFILE\.ssh\telegram_shop_deploy root@72.60.191.224 `
  "systemctl is-active telegram-shop && tail -20 /var/log/telegram-shop/app.log"
```

Dashboard: http://72.60.191.224:8000/login

---

## Project layout

```
telegram-shop/
├── run.py                    # Production entry: bot + uvicorn dashboard
├── bot.py                    # Dispatcher, payment poller, notifications, clock skew check
├── config.py                 # Settings from .env, PAYMENT_METADATA, enabled_methods()
├── db.py                     # SQLite schema, migrations, all DB helpers
├── states.py                 # FSM states for checkout/deposit/admin flows
├── shop.db                   # SQLite database (local copy; production on VPS)
├── .env                      # SECRETS — never commit
├── .env.example              # Template without secrets
│
├── handlers/
│   ├── start.py              # /start, main menu, welcome, menu:home
│   ├── shop.py               # Product list, buy flow, quantity
│   ├── payment.py            # Checkout payment methods + instructions
│   ├── deposit.py            # Wallet balance view + deposit top-up flow
│   ├── profile.py            # Profile + purchase history
│   ├── support.py            # Support link
│   ├── security.py           # Private-chat gate + order/payment ownership checks
│   └── admin.py              # Telegram /admin commands
│
├── keyboards/
│   ├── main.py               # main_menu(), profile_menu(), wallet_menu(), back_to_menu()
│   ├── shop.py               # Product buttons (brand logo custom emoji), PAGE_SIZE pagination
│   ├── payment.py            # Payment method buttons (brand emoji icons)
│   ├── icons.py              # Emoji labels + payment custom emoji IDs
│   ├── mix_icons.py          # Third-party Mix Emoji pack lookup (tge(), unicode fallback)
│   ├── brands.py             # 43-brand catalog, name auto-detect
│   ├── product_icons.py      # Resolve logo ID per product
│   ├── brand_logo_ids.json   # Telegram custom emoji IDs for brands
│   ├── mix_emoji_ids.json    # Snapshot of grabbed Mix Emoji pack (per-instance)
│   ├── product_emoji_ids.json# Color dot / out-of-stock emoji IDs
│   └── payment_emoji_ids.json# Payment method emoji IDs
│
├── services/
│   ├── binance.py            # Binance API client (signed requests)
│   ├── payments.py           # Create payments, poll + match deposits (time window, receiver/address checks)
│   ├── delivery.py           # Claim stock, deliver codes, settle order, wallet pay, confirm_and_settle
│   ├── telegram_delivery.py  # Numbered, chunked code deliveries (Telegram-safe HTML)
│   ├── broadcast.py          # Restock / new-product / price-drop / admin broadcasts
│   ├── emoji_pack.py         # Resolve + snapshot a third-party custom-emoji pack (/grabemoji)
│   ├── pricing.py            # Dollar bulk tiers, band-matched unit price, range display
│   └── coupons.py            # Coupon validation
│
├── webapp/
│   ├── app.py                # FastAPI routes, auth, static mounts, balance notify, broadcasts
│   ├── static/admin.css      # Dashboard styles
│   ├── static/favicon.svg    # Admin dashboard favicon (gradient shopping bag)
│   └── templates/            # Jinja HTML (products, users, orders, …)
│       ├── _logo_picker.html # Collapsible brand logo grid macro
│       ├── _order_delivered.html # Expandable delivered keys/accounts macro
│       ├── orders.html       # Order list + Delivered items column + search box
│       ├── dashboard.html    # Stats + recent orders + Delivered items column
│       └── products.html     # Products + stock + logo + reorder + $ bulk tiers
│
├── scripts/
│   ├── setup_brand_logos.py        # Full rebuild of brand logo emoji pack (reissues all ids)
│   ├── add_brand_logo.py           # Incremental add/replace ONE brand (addStickerToSet)
│   ├── setup_mix_emojis.py         # Snapshot a third-party emoji pack by URL slug
│   ├── setup_payment_emojis.py
│   ├── setup_product_emojis.py
│   ├── assign_product_brands.py
│   ├── binance_health_check.py
│   ├── diag_payments.py
│   ├── investigate_order.py        # Forensics for one order + Binance Pay txs
│   ├── audit_old_tx_matches.py     # Audit confirmed payments for stale tx matches
│   ├── list_inactive_products.py   # Read-only: id, active flag, order/stock counts
│   └── purge_all_products.py       # DESTRUCTIVE one-off: hard-delete every product
│
├── assets/
│   ├── brand-logos/          # PNG previews (100×100) for admin + emoji upload
│   ├── payment-icons/
│   └── product-icons/
│
├── deploy/
│   ├── setup.sh              # VPS bootstrap (shopbot user, venv, systemd)
│   ├── remote-bootstrap.sh   # Post-upload install hook
│   ├── deploy.ps1            # Full deploy from Windows
│   ├── telegram-shop.service # systemd unit (User=shopbot)
│   └── nginx.conf.example    # Optional HTTPS reverse proxy
│
├── tests/smoke_test.py       # DB + payment matching + delivery chunking tests (no Telegram)
└── AI_PROJECT_GUIDE.md       # THIS FILE
```

---

## Key DB helpers (for AI reference)

| Function | Purpose |
|----------|---------|
| `get_or_create_user()` | Register/update Telegram user |
| `adjust_user_balance()` / `set_user_balance()` | Admin balance changes + audit log |
| `list_available_stock()` | Unsold stock rows for admin UI |
| `delete_stock_item()` / `delete_available_stock()` | Remove unsold stock |
| `count_sold_stock()` / `stock_count()` | Sold vs available counts |
| `claim_stock_items()` | Atomic stock claim on delivery (sets `order_id` on sold rows) |
| `delivered_contents_by_orders()` | Admin dashboard: map order IDs → delivered content strings |
| `try_debit_balance()` / `try_transition_order_status()` | Atomic wallet pay + order status gates |
| `user_total_spent()` | Sum payable on paid/delivered orders (feeds loyalty tier) |
| `recent_orders()` | User order history with product join |
| `all_orders(limit, search)` | Admin orders list; `search` filters by order #, Telegram id, or @username |
| `reorder_product(id, "up"\|"down")` | Move a product in display order (re-densifies `sort_order`) |
| `set_pricing_tier(pid, min_qty, unit_price)` / `delete_pricing_tier()` | Dollar bulk tiers |
| `create_payment()` | Pending payment with unique amount constraint |
| `find_pending_payment_by_amount()` | Poller matching |
| `expire_old_payments()` | TTL cleanup |

---

## Owner vocabulary → what to do

| Owner says | Action |
|------------|--------|
| Change shop name | Edit `SHOP_NAME` in `.env` → push → restart |
| Add payment address | Edit `BINANCE_PAY_UID` / `USDT_*_ADDRESS` in `.env` → push → restart |
| Bot not replying | Check VPS service; no local `run.py`; logs for `TelegramConflictError` |
| Deposit not crediting | Run `binance_health_check.py` on VPS; check Binance IP whitelist + clock skew |
| Wallet asks to deposit | Wallet button must use `menu:wallet`, not `menu:deposit` — see `keyboards/main.py` |
| Add product / logo / stock | Admin dashboard → Products |
| Refund customer | Admin dashboard → Users → balance adjust (Add) with note |
| Remove bad stock | Products → Manage stock → Delete (unsold only) |
| Hide a product (might bring back later) | Products → **Deactivate** (toggle) — stays in dashboard, hidden from shop |
| Permanently remove a product | Products → **Delete** (confirm in dialog) — also wipes that product's orders; payments are preserved with `order_id` cleared |
| Logos wrong/missing | Re-run `setup_brand_logos.py` on VPS; set brand on product in dashboard |
| Add redemption steps for a product | Dashboard → Products → Edit → "Delivery instructions" textarea |
| Profile broken | Check logs; ensure handlers use `payable` not `total` on orders |
| Backup database | `scp` `shop.db` from VPS to PC |
| What keys did customer get? | Dashboard → **Orders** → expand **Delivered items** on that order |
| Payment matched wrong old tx | Fixed May 2026 — run `audit_old_tx_matches.py` on VPS to audit history |

---

## Local development (Windows)

- Python venv: `.venv\Scripts\python.exe`
- Run locally (testing only): `python run.py`
- Dashboard local: http://127.0.0.1:8000
- Smoke test: `python -m tests.smoke_test`

**Windows note:** Pinned deps may fail on Python 3.14; VPS uses Python 3.12.

**Starlette 0.41+:** `webapp/app.py` uses `render()` helper for template responses.

---

## VPS co-hosting other tools

The shop bot is **isolated** in `/opt/telegram-shop` with its own systemd service and `.env`. Other tools (n8n, OpenClaw, Hermes, etc.) can run on the same VPS **if**:

- They use **different ports** and don't stop `telegram-shop`
- They do **not** reuse the same `BOT_TOKEN` (Telegram conflict)
- KVM 1 RAM is limited (~4 GB) — running many heavy services together may OOM

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `TelegramConflictError` | Two bot instances polling | Stop local `run.py` or stop VPS service |
| Wallet opens deposit prompt | Wallet wired to `menu:deposit` | Change to `menu:wallet` in `keyboards/main.py` |
| Profile button does nothing | Python error in handler | Check logs; `payable` vs `total` on orders |
| Logo grid empty in admin | Jinja macro missing context | `logo_picker` must receive `brands` + `brand_previews` as args |
| White payment icons | `needs_repainting: true` on emoji pack | Re-run `setup_payment_emojis.py` with color pack |
| Logos mostly black | Monochrome Simple Icons | Re-run `setup_brand_logos.py` (Iconify color SVGs) |
| Grok logo black square | Source PNG is dark-on-transparent | `_invert_on_black()` in `setup_brand_logos.py` inverts to white; re-run script |
| Service crash after deploy | File ownership wrong (shopbot can't read .env) | `chown -R shopbot:shopbot /opt/telegram-shop` then restart |
| Binance API fails | Clock drift or IP not whitelisted | `timedatectl`; whitelist `72.60.191.224` |
| Dashboard 401 | Wrong password or expired session | Re-login at `/login` |
| scp "remote mkdir failure" | Wrong scp syntax (two dest paths) | One file per scp command to full remote path |
| scp resolves wrong hostname | Used `$Host` in PowerShell | Use `$remote` — `$Host` is a reserved PS variable |
| Deposit credited but wrong user | Stale Binance tx before time-window fix | Run `investigate_order.py` + `audit_old_tx_matches.py`; fixes are forward-looking |
| Delivered items empty on old order | Stock not linked to order_id at delivery time | New orders show content; old ones may say "No stock linked" |

---

## Security reminders for AI

1. Do **not** commit `.env`, `shop.db`, or API keys
2. Do **not** repeat secrets from `.env` in chat or docs
3. Binance API should stay **read-only**; IP restricted to VPS
4. Rotate bot token + Binance keys if ever exposed
5. Only create git commits when owner asks
6. Admin dashboard is HTTP on port 8000 — consider nginx + HTTPS later (`deploy/nginx.conf.example`)
7. **Callback ownership:** shop/payment/deposit handlers use `handlers/security.py` — private chat only; users cannot act on another user's orders or payments
8. **Payment poller:** never match txs outside the time window or to wrong receiver/address — stale-match bugs are high severity

---

## Useful server commands

```bash
systemctl status telegram-shop
systemctl restart telegram-shop
systemctl stop telegram-shop          # before running bot locally
journalctl -u telegram-shop -f
tail -50 /var/log/telegram-shop/app.log

# Add one brand logo without disturbing the others (preferred)
cd /opt/telegram-shop
sudo -u shopbot .venv/bin/python scripts/add_brand_logo.py <slug> [--replace]

# Regenerate ALL brand logos (reissues every emoji id — rarely needed)
sudo -u shopbot .venv/bin/python scripts/setup_brand_logos.py

# Payment health
sudo -u shopbot .venv/bin/python scripts/binance_health_check.py

# Investigate one order (payments + Binance Pay txs)
sudo -u shopbot .venv/bin/python scripts/investigate_order.py 16

# Audit for stale tx matches in confirmed payments
sudo -u shopbot .venv/bin/python scripts/audit_old_tx_matches.py

# Backup database
cp /opt/telegram-shop/shop.db /opt/telegram-shop/shop.db.bak.$(date +%F)
```

---

## Features implemented (changelog)

| Feature | Status | Key files |
|---------|--------|-----------|
| SQLite database on VPS | ✅ | `db.py`, `shop.db` |
| Binance Pay + TRC20/BEP20 auto-matching | ✅ | `services/payments.py`, `services/binance.py` |
| Round payment amounts (no fractional UI) | ✅ | `keyboards/shop.py`, payment poller |
| Full-color payment method emoji pack | ✅ | `scripts/setup_payment_emojis.py` |
| 40 brand logos on shop buttons | ✅ | `keyboards/brands.py`, `setup_brand_logos.py` |
| Admin collapsible logo picker | ✅ | `_logo_picker.html`, `webapp/app.py` |
| Stock view / add / delete in dashboard | ✅ | `db.py`, `products.html` |
| Profile + purchase history | ✅ | `handlers/profile.py` |
| Admin balance adjust + audit + notify | ✅ | `webapp/app.py`, `balance_adjustments` table |
| Wallet shows balance (not deposit) | ✅ | `menu:wallet`, `wallet_menu()` |
| Deposit as separate action | ✅ | `menu:deposit`, `/deposit` command |
| VPS deploy script + systemd | ✅ | `deploy/deploy.ps1`, `telegram-shop.service` |
| **May 2026 hardening session** | | |
| Async Telegram notify on balance adjust (no event-loop block) | ✅ | `webapp/app.py` |
| Wallet pay debits BEFORE order transition (refund on race loss) | ✅ | `services/delivery.py` |
| Column whitelist on `update_product` / `update_order` (SQLi defence) | ✅ | `db.py` (`_PRODUCT_UPDATABLE`, `_ORDER_UPDATABLE`) |
| `MAX_DEPOSIT_USDT` cap | ✅ | `config.py`, `handlers/deposit.py` |
| Startup warning if default web credentials still in place | ✅ | `webapp/app.py` (`_warn_default_credentials`) |
| Login-rate-limit dict evicts empty buckets (memory leak) | ✅ | `webapp/app.py` |
| Removed dead `require_admin` helper | ✅ | `webapp/app.py` |
| Removed dead `"brand" in r.keys()` check | ✅ | `webapp/app.py` |
| **May 2026 UI redesign session** | | |
| Vexoran-style welcome with `<blockquote>` menu overview | ✅ | `handlers/start.py` |
| Main menu reflow: wide Shop + 2×2 grid | ✅ | `keyboards/main.py` |
| Icon set refresh (🛍 💰 💎 🆘 🧿 👑 🥇) | ✅ | `keyboards/icons.py` |
| Button text capped at 36 chars; price always visible | ✅ | `keyboards/shop.py` |
| Product button format `Name… [stock] — $price` / `Name… ❌ — $price` | ✅ | `keyboards/shop.py` |
| `delete_product` always hard-deletes (cascades orders, severs payments) | ✅ | `db.py`, `products.html` |
| One-off utility scripts: `list_inactive_products.py`, `purge_all_products.py` | ✅ | `scripts/` |
| Removed "Pay Directly" intermediate; summary screen IS payment picker | ✅ | `keyboards/shop.py`, `keyboards/payment.py`, `handlers/shop.py` |
| Deposit prompt shows both min and max | ✅ | `handlers/deposit.py` |
| **May 2026 security audit (payment + bot hardening)** | | |
| Payment tx time window (reject stale Binance txs) | ✅ | `services/payments.py` (`PAYMENT_TIME_GRACE_SECONDS`) |
| Binance Pay receiver UID validation | ✅ | `services/payments.py` |
| On-chain deposit address validation | ✅ | `services/payments.py` |
| Poll before expire (last-second payments still match) | ✅ | `services/payments.py` → `poll_payments()` |
| Order/payment callback ownership + private-chat gate | ✅ | `handlers/security.py`, shop/payment/deposit handlers |
| Wallet insufficient balance keeps crypto invoice open | ✅ | `services/delivery.py` |
| Chunked Telegram code delivery for large orders | ✅ | `services/telegram_delivery.py`, `bot.py` |
| `confirm_and_settle()` status gates + double-pay refund | ✅ | `services/delivery.py` |
| Expanded smoke tests (stale tx, chunking, wallet race) | ✅ | `tests/smoke_test.py` |
| Full redeploy preserves `shop.db` + WAL | ✅ | `deploy/deploy.ps1` |
| Order/payment forensics scripts | ✅ | `scripts/investigate_order.py`, `audit_old_tx_matches.py` |
| **May 2026 admin dashboard — delivered content visibility** | | |
| Orders + Dashboard show exact keys/accounts delivered | ✅ | `db.delivered_contents_by_orders()`, `_order_delivered.html` |
| **May 2026 — delivery instructions + brand logo fixes** | | |
| Per-product delivery instructions (admin field, shown to buyer only after delivery) | ✅ | `db.py` migration, `products.html`, `webapp/app.py`, `services/telegram_delivery.py`, `bot.py`, `handlers/payment.py` |
| Grok logo fixed — inverted to white-on-black for Telegram dark theme visibility | ✅ | `scripts/setup_brand_logos.py` (`_invert_on_black()`), `keyboards/brands.py` (`BRAND_ICON_URL`) |
| Manus brand added (favicon from manus.im) | ✅ | `keyboards/brands.py` |
| Picsart brand added (Simple Icons) | ✅ | `keyboards/brands.py` |
| Brand count increased from 40 to 42 | ✅ | `keyboards/brand_logo_ids.json` |
| **May 2026 — broadcasts, visual polish & dashboard UX session** | | |
| Price-drop broadcast on price reduction (web edit + `/setprice`) | ✅ | `services/broadcast.py`, `webapp/app.py`, `handlers/admin.py`, `states.py` |
| New-product broadcast (`NEW PRODUCT ADDED!`) on creation (dashboard + `/addproduct`) | ✅ | `services/broadcast.py` (`broadcast_new_product`), `webapp/app.py`, `handlers/admin.py` |
| Green URL deep-link Buy buttons on price-drop/new-product cards (`?start=prod<id>`) | ✅ | `services/broadcast.py`, `handlers/start.py` |
| Numbered code delivery (`1.` `2.` `3.`…), continues across chunks | ✅ | `services/telegram_delivery.py` |
| Loyalty tier in profile (Bronze→Diamond by lifetime spend) | ✅ | `handlers/profile.py` |
| Mix Emoji pack support + `/grabemoji` one-tap setup + `mix_icons` helper | ✅ | `keyboards/mix_icons.py`, `services/emoji_pack.py`, `scripts/setup_mix_emojis.py`, `handlers/admin.py` |
| Visual polish (sparkles/cards) across welcome/profile/shop/wallet/support/delivery | ✅ | `handlers/*`, `bot.py` |
| Bulk pricing switched from % to per-unit $ tiers + range display | ✅ | `db.py` (`unit_price`), `services/pricing.py`, `webapp/app.py`, `products.html`, `handlers/admin.py` |
| Tier resolution charges the displayed band (highest `min_qty ≤ qty`); `unit_label` escaped | ✅ | `services/pricing.py` |
| Product reordering (↑/↓) in dashboard | ✅ | `db.py` (`sort_order`, `reorder_product`), `webapp/app.py`, `products.html` |
| Coursera brand added; logo set to solid blue-C; incremental `add_brand_logo.py` (`--replace`) | ✅ | `keyboards/brands.py`, `scripts/add_brand_logo.py` |
| Brand count increased from 42 to 43 | ✅ | `keyboards/brand_logo_ids.json` |
| Orders search by order #, Telegram id, or @username | ✅ | `db.all_orders(search=)`, `webapp/app.py`, `orders.html` |
| Admin dashboard favicon (gradient shopping-bag SVG) | ✅ | `webapp/static/favicon.svg`, `base.html`, `login.html` |
| `BOT_USERNAME` guard on deep-link broadcasts; `/grabemoji` reads reply entities | ✅ | `services/broadcast.py`, `handlers/admin.py` |

---

## Owner preferences

- Wants hands-off VPS hosting ("set it and forget it")
- Prefers AI to **push changes to VPS** rather than long tutorials
- Bot brand: **Akunding Store** / @Akunding_store_bot
- Ethical use of admin balance adjustments (refunds/credits) with audit log
- Simple round payment amounts over complex unique-amount matching

When in doubt: read `.env` locally (don't expose secrets), make the change, push to VPS, restart service, confirm bot responds to `/start`.

---

*Last updated: 2026-05-27 — production on Hostinger VPS 72.60.191.224, bot @Akunding_store_bot. Includes: SQLite, brand logo emoji packs (43 brands incl. Coursera blue-C), stock management, profile/history with loyalty tiers, balance admin, wallet/deposit split, simplified payment matching, full admin dashboard routes, May 2026 hardening + UI redesign + security audit (see changelog), admin **Delivered items** column, per-product **delivery instructions**, and the May 2026 broadcasts/polish/UX session: price-drop & new-product broadcasts (green deep-link Buy buttons), numbered code delivery, Mix Emoji pack via `/grabemoji`, dollar-based bulk pricing (band-matched charge), product reordering, orders search (order #/Telegram id/@username), and the admin dashboard favicon.*
