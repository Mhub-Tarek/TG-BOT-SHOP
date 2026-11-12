# Telegram Shop Bot — Binance USDT

A complete self-hosted Telegram shop for digital products (license keys, accounts, coupons…) with:

- **Multi-step buy flow:** product list → product detail → quantity picker → checkout summary → payment method picker → pay
- **4 payment methods:** Pay via Wallet, Binance Pay (internal UID transfer), USDT BEP20, USDT TRC20
- **Auto-detection:** every pending payment gets a unique fractional amount + alphanumeric note; a background poller reconciles against the Binance API and credits + delivers automatically
- **Coupons** with percent or fixed discount
- **Wallet** top-up / direct pay
- **Referral program** with revenue share
- **Bulk pricing tiers** (e.g. buy 10+ → 10% off)
- **Restock broadcasts** to all users when stock is added
- **Browser admin dashboard** (FastAPI) for products, stock, orders, payments, users, coupons, stats
- **Single-process runner** (`run.py`) — bot and dashboard share one event loop

## Project layout

```
telegram-shop/
├── run.py                     # ▶ entry point — bot + dashboard together
├── bot.py                     # Telegram bot only (called by run.py)
├── config.py                  # pydantic-settings
├── db.py                      # aiosqlite schema + helpers
├── states.py                  # FSM states
├── handlers/                  # Telegram handlers
│   ├── start.py shop.py payment.py deposit.py
│   ├── profile.py support.py admin.py
├── keyboards/                 # Inline keyboards
├── services/
│   ├── binance.py             # Signed Binance REST client
│   ├── payments.py            # create + poll + confirm payments
│   ├── delivery.py            # atomic stock claim
│   ├── pricing.py             # bulk-tier math
│   └── coupons.py
├── webapp/                    # FastAPI admin dashboard
│   ├── app.py
│   ├── templates/  static/
├── deploy/                    # systemd + nginx + setup.sh
├── tests/smoke_test.py
├── requirements.txt
├── .env.example
└── README.md
```

---

## 1. Local testing (this section answers "how do I test the shop locally?")

### Step 1 — Get a Telegram bot token

Open Telegram, message `@BotFather`, send `/newbot`, follow prompts. Copy the token. (Looks like `7800000000:AAH...`.)

### Step 2 — Get your Telegram user ID

Message `@userinfobot` in Telegram, copy the numeric ID.

### Step 3 — Get Binance API credentials (read-only)

1. Binance → Profile → **API Management** → Create API
2. **Enable Reading.** Disable everything else (Spot trading, Withdrawals, Universal Transfer).
3. **Restrict access to trusted IPs** — for local testing add your home IP, for VPS add the VPS public IP.
4. Copy the API key + secret.

> ⚠️ For local testing **without** a Binance account you can still launch the bot — auto-detection won't work but every other flow (browsing, wallet, coupons, admin dashboard) does.

### Step 4 — Get deposit addresses

Binance → Wallet → **Deposit** → pick USDT → choose network (TRC20 / BEP20). Copy the address. Optionally copy your Binance UID (Profile → top of dashboard, ~9-10 digit number).

### Step 5 — Install + configure

```powershell
cd c:\Users\ahmed\Desktop\telegram-shop
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
notepad .env
```

Fill at minimum:
- `BOT_TOKEN`, `BOT_USERNAME`, `ADMIN_IDS`
- `BINANCE_API_KEY`, `BINANCE_API_SECRET` *(or leave blank for offline test)*
- `BINANCE_PAY_UID` *(optional — hides the button if blank)*
- `USDT_TRC20_ADDRESS` and/or `USDT_BEP20_ADDRESS`
- `WEB_ADMIN_PASSWORD`, `WEB_SESSION_SECRET` *(use a 32+ char random string)*

### Step 6 — Run

```powershell
python run.py
```

You'll see:
```
INFO bot :: Bot starting as @YourShopBot
INFO run :: Admin dashboard starting on http://0.0.0.0:8000
```

### Step 7 — Test the buyer flow

1. Telegram → open your bot → `/start`
2. **Add a product (admin commands work in Telegram too):**
   - `/admin` to see all admin commands
   - `/addproduct` → guided creation (name → emoji → price → unit → features → description)
   - `/addstock` → pick product ID → paste one stock item per line
3. As a "buyer" (use a second Telegram account if you can — your own user ID is also fine since admin and buyer roles are independent):
   - `/start` → Shop → pick product → Buy Now → quantity → Pay Directly → Pay via Wallet
   - Wallet balance is $0 so it'll tell you to deposit. Top up via `/deposit` or just buy via "USDT TRC20" / "Binance Pay" to see the unique-amount + note payment screen.
4. **Check the auto-poller** (without real money):
   - Pick "USDT TRC20"
   - You'll see "Send exactly: 10.0017 USDT" + a note like `Y3KP9F`
   - Open the admin dashboard → Payments → you'll see the pending row with that unique amount and note. Status flips to `confirmed` once a matching deposit arrives in your real Binance wallet. The bot then auto-DMs the buyer with the codes.

### Step 8 — Open the admin dashboard

Open http://localhost:8000 in your browser → log in with `WEB_ADMIN_PASSWORD`. Tabs:

| Tab | What you do here |
| --- | --- |
| **Dashboard** | At-a-glance: users, delivered orders, revenue, confirmed payments, pending orders, unsold stock + the 20 most recent orders. |
| **Products** | Create, edit (name/emoji/price/features/description), toggle active, delete. For each: inline forms to upload more stock items and add bulk-pricing tiers. |
| **Orders** | Every order, who bought, qty, subtotal, coupon used, payable, method, status. Filterable visually by status color. |
| **Payments** | Every payment row: method, network, unique amount, note, TXID, status. Useful for reconciling a payment with your Binance history. |
| **Users** | All users with Telegram ID, username, balance, signup time. |
| **Coupons** | Create / enable / disable coupons. Two kinds: `percent` (0-90) or `fixed` (USDT). Optional max-uses cap. |

### Common things to try while testing

- Edit a product in the dashboard while you have the Telegram product card open → tap Refresh in the bot → new price/stock shows up.
- Create a coupon `TEST10` (percent 10) → in the bot, go through a buy flow → "Apply Coupon" → enter `TEST10` → final payable drops 10%.
- Add a pricing tier (buy 10+ → 10% off) and try buying 10 — unit price drops automatically.
- Push a "restock" broadcast: in the bot type `/notifyrestock <product_id> 50` — every user gets a "🆕 50 new stock added" message with a one-tap Buy button.

### Smoke test (no Telegram needed)

```powershell
python -m tests.smoke_test
```

Hits the DB layer end-to-end (user, product, tier, stock, deposit, atomic claim, balance refund, TXID uniqueness). Must print "All smoke tests passed."

---

## 2. How to improve / extend

A few high-leverage things you can add once the basics work:

1. **Pre-product images.** Telegram lets you attach a photo to a message. Store `image_file_id` on products and use `bot.send_photo(...)` for the detail view.
2. **Multi-language UI.** All user-facing strings live in `handlers/*.py`. Move them to a `locales/<lang>.json` file, key the user's language from `from_user.language_code`, and look up.
3. **Refund button.** Add a `/refund <order_id>` admin command that re-credits the user's wallet and re-shelves the stock items (set `sold=0, order_id=NULL`).
4. **Order export.** Add a "/orders.csv" route to the admin dashboard.
5. **2FA on admin dashboard.** Replace the password login with TOTP (use `pyotp`). High-value once you handle real money.
6. **Webhook Binance Pay merchant.** If you get Binance Pay merchant approval, swap the unique-amount matcher for the proper `/binancepay/openapi/v3/order` API + webhook — same code shape, cleaner UX (one-tap pay in Binance).
7. **Postgres + multi-instance.** For real scale, swap SQLite for Postgres (asyncpg). Only `db.py` changes; everything else is unaffected because all DB access goes through that module.
8. **Telegram Stars / Telegram Payments.** Optional sidecar method for users who don't want crypto.
9. **Better restock-broadcast throttling.** Wrap the loop in `asyncio.sleep(0.05)` between sends to stay under Telegram's flood limit on large user bases.

---

## 3. VPS deployment (24/7)

### Provider

Any Ubuntu 22.04 / 24.04 VPS works. Hetzner CX11 (~$4/mo), DigitalOcean basic droplet (~$5/mo), Contabo VPS ($6/mo), or a 1 GB Vultr instance — all fine. You need: 1 vCPU, 1 GB RAM, 20 GB disk, public IPv4.

### A. Quick path (one script)

```bash
ssh root@YOUR_VPS_IP
# 1) copy this folder up (from your laptop):
#    rsync -av --exclude='.venv' --exclude='shop.db*' --exclude='__pycache__' \
#          ./ root@YOUR_VPS_IP:/opt/telegram-shop/
# OR
git clone <your-repo-url> /opt/telegram-shop

cd /opt/telegram-shop
bash deploy/setup.sh           # installs Python, venv, deps, creates user, installs systemd unit
cp .env.example .env
nano .env                      # fill in EVERYTHING — token, IDs, Binance keys, addresses, web password
chown shopbot:shopbot .env

systemctl start telegram-shop
journalctl -u telegram-shop -f # live logs — verify it says "Bot starting as @yourbot"
```

### B. Whitelist your VPS IP in Binance

Binance API restrictions → edit your API key → enable "Restrict access to trusted IPs" → paste the VPS public IP. Without this, the API will reject the bot's signed requests.

### C. Expose the admin dashboard (HTTPS)

The dashboard listens on `127.0.0.1:8000` (set `WEB_HOST=127.0.0.1` in `.env` on a VPS — don't expose it directly). Reverse-proxy with nginx + Let's Encrypt:

```bash
# DNS: point shop-admin.yourdomain.com to your VPS IP first.

cp /opt/telegram-shop/deploy/nginx.conf.example /etc/nginx/sites-available/telegram-shop-admin
nano /etc/nginx/sites-available/telegram-shop-admin       # set server_name shop-admin.yourdomain.com
ln -s /etc/nginx/sites-available/telegram-shop-admin /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

apt install certbot python3-certbot-nginx -y
certbot --nginx -d shop-admin.yourdomain.com              # auto-installs cert + auto-renews
```

Now https://shop-admin.yourdomain.com lets you manage the shop from any browser.

### D. Firewall

```bash
ufw allow ssh
ufw allow http
ufw allow https
ufw --force enable
```

Port 8000 is NOT opened — only nginx (80/443) is public, and it proxies to localhost.

### E. Backups

```bash
# Daily backup of the SQLite DB to /var/backups/
echo '0 4 * * * shopbot sqlite3 /opt/telegram-shop/shop.db ".backup /var/backups/shop-$(date +\%F).db"' | sudo tee /etc/cron.d/telegram-shop-backup
mkdir -p /var/backups && chown shopbot /var/backups
```

For off-site backups: rclone to S3/B2 or scp/rsync to another VPS.

### F. Updating the bot

```bash
cd /opt/telegram-shop
git pull                                        # or rsync new files up
sudo -u shopbot .venv/bin/pip install -r requirements.txt
systemctl restart telegram-shop
journalctl -u telegram-shop -n 50               # verify clean restart
```

### G. Common operational commands

| Command | What it does |
| --- | --- |
| `systemctl status telegram-shop` | Is it running? |
| `systemctl restart telegram-shop` | Restart after config change |
| `systemctl stop telegram-shop` | Stop |
| `journalctl -u telegram-shop -f` | Tail live logs |
| `journalctl -u telegram-shop --since "1 hour ago"` | Recent logs |
| `sqlite3 /opt/telegram-shop/shop.db "SELECT COUNT(*) FROM orders WHERE status='delivered'"` | Spot-check from CLI |

### H. Docker alternative (optional)

If you'd rather run in Docker, a minimal `Dockerfile`:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["python", "run.py"]
```

Then `docker build -t telegram-shop .` and run with `docker run -d --name shop --restart always --env-file .env -p 127.0.0.1:8000:8000 -v $PWD/shop.db:/app/shop.db telegram-shop`.

---

## 4. Race-safety notes (the parts that matter when real money is on the line)

- **`BEGIN IMMEDIATE` + atomic UPDATE** for stock claim; full rollback if partial claim fails → caller refunds wallet.
- **`UPDATE … WHERE balance >= ?`** for wallet debit (no time-of-check/time-of-use bug).
- **Partial UNIQUE index** on `payments.txid` prevents double-crediting a TXID.
- **`UPDATE … WHERE status = 'pending'`** ensures a payment can only transition once.
- **Unique-amount reservation** is scoped per `(coin, network, pending)` so two simultaneous payments on the same coin+network can never collide; expired / confirmed payments release their slot.
- **HTML escape** on every user-supplied string interpolated into messages.

---

## License

MIT.
# TG-BOT-SHOP
# TG-BOT-SHOP
# TG-BOT-SHOP
# TG-BOT-SHOP
# TG-BOT-SHOP
