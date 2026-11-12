# Railway Deployment Guide

Complete guide for deploying the Telegram Shop bot to Railway.app.

## Prerequisites

Before you start, gather these credentials:

1. **Telegram Bot Token** — Get from [@BotFather](https://t.me/BotFather):
   - Send `/newbot`
   - Follow prompts to create your bot
   - Copy the token (format: `7800000000:AAH...`)
   - Get bot username (without @)

2. **Your Telegram User ID** — Get from [@userinfobot](https://t.me/userinfobot):
   - Message the bot
   - Copy your numeric ID

3. **Binance API Credentials** (read-only):
   - Log into Binance → Profile → API Management → Create API
   - **Enable "Reading" only** — disable Spot trading, Withdrawals, Universal Transfer
   - Copy API Key and Secret
   - Note: You'll need to whitelist Railway's IP later (see below)

4. **Binance Payment Addresses**:
   - Your Binance Pay UID (Settings → Account → UID)
   - Your USDT BEP20 deposit address (Wallet → Spot → USDT → Deposit → BEP20)
   - Your USDT TRC20 deposit address (Wallet → Spot → USDT → Deposit → TRC20)

5. **Admin Dashboard Password** — Generate a strong password:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

6. **Session Secret** — Generate another random string:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

---

## Step 1: Create Railway Project

1. Go to [railway.app](https://railway.app) and sign in (GitHub account works)
2. Click **New Project** → **Deploy from GitHub repo**
3. Connect your GitHub account if needed
4. Select or import this repository
5. Railway will detect the Python app automatically

---

## Step 2: Add Volume for Database

The bot uses SQLite, which needs persistent storage:

1. In your Railway project, click your service
2. Go to **Settings** tab
3. Scroll to **Volumes** section
4. Click **+ New Volume**
5. Set mount path: `/data`
6. Click **Add**

---

## Step 3: Configure Environment Variables

Go to the **Variables** tab and add all of these:

### Telegram Configuration
```
BOT_TOKEN=your_bot_token_from_botfather
BOT_USERNAME=your_bot_username_without_at_sign
ADMIN_IDS=your_telegram_user_id
```

### Branding (customize these)
```
SHOP_NAME=Your Shop Name
WELCOME_BLURB=Welcome to our store — premium digital products at the best prices. Fast, secure, and fully automated delivery.
SUPPORT_USERNAME=your_support_telegram_username
CHANNEL_URL=https://t.me/yourchannel
DEFAULT_DELIVERY_PROMISE=💚 Instant Code Delivery\n🛡 Full Year Warranty
```

### Binance API
```
BINANCE_API_KEY=your_binance_api_key
BINANCE_API_SECRET=your_binance_api_secret
```

### Payment Methods (configure the ones you want to use)
```
BINANCE_PAY_UID=your_binance_uid
USDT_BEP20_ADDRESS=your_bep20_deposit_address
USDT_TRC20_ADDRESS=your_trc20_deposit_address
```

### Payment Settings
```
PAYMENT_TTL_MINUTES=15
POLL_INTERVAL_SECONDS=30
MIN_DEPOSIT_USDT=1.0
MAX_DEPOSIT_USDT=5000.0
```

### Web Dashboard (CRITICAL — change these defaults)
```
WEB_HOST=0.0.0.0
WEB_PORT=$PORT
WEB_ADMIN_PASSWORD=your_generated_strong_password
WEB_SESSION_SECRET=your_generated_session_secret
WEB_SECURE_COOKIES=true
WEB_LOGIN_MAX_ATTEMPTS=5
WEB_LOGIN_WINDOW_SECONDS=300
WEB_FORWARDED_ALLOW_IPS=*
```

### Database Path (points to the volume)
```
SHOP_DB_PATH=/data/shop.db
```

### Referral Program
```
REFERRAL_PERCENT=5.0
```

---

## Step 4: Handle Binance IP Whitelist

Railway's outbound IPs are shared by default. You have two options:

### Option A: Use Static Outbound IP (Recommended)

1. In Railway project settings, look for **Networking** or **Static Outbound IP**
2. Enable static IP (may require Pro plan — check Railway pricing)
3. Copy the static IP address
4. Go to Binance → Profile → API Management
5. Edit your API key → IP Access Restrictions
6. Add Railway's static IP to whitelist
7. Save

### Option B: No IP Whitelist (Less Secure)

1. Go to Binance → Profile → API Management
2. Edit your API key
3. Set IP Access Restriction to **Unrestricted** (or remove all IPs)
4. Save
5. **Note**: This is less secure but works for read-only keys. Consider enabling 2FA on your Binance account for additional security.

---

## Step 5: Deploy

1. Railway will automatically deploy after you save environment variables
2. Check the **Deployments** tab for build progress
3. Watch logs for any errors
4. Once deployed, Railway will assign a public URL to your service

---

## Step 6: Access Your Admin Dashboard

1. Go to your Railway service page
2. Click **Settings** → find **Public Networking** section
3. Copy the generated domain (format: `your-service.up.railway.app`)
4. Open in browser: `https://your-service.up.railway.app`
5. Login with the `WEB_ADMIN_PASSWORD` you set
6. You should see the admin dashboard

---

## Step 7: Test Your Bot

1. Open Telegram and search for your bot (the username you set)
2. Send `/start`
3. Bot should respond with your welcome message
4. Try browsing the shop (it will be empty initially)
5. Go to admin dashboard → **Products** → **Add Product** to create your first product

---

## Database Backups

Railway volumes are persistent but not automatically backed up. Consider:

1. **Manual backups**: Download `/data/shop.db` periodically via Railway CLI or add a backup script
2. **Scheduled exports**: Create a Railway cron service that dumps the DB to cloud storage (S3, Dropbox, etc.)
3. **Railway volume snapshots**: Check if your Railway plan includes volume snapshots

To download your database manually:
```bash
# Install Railway CLI
npm install -g @railway/cli

# Login
railway login

# Link to your project
railway link

# Download database
railway run cat /data/shop.db > backup-$(date +%Y%m%d).db
```

---

## Troubleshooting

### Bot doesn't respond
- Check Railway logs for errors
- Verify `BOT_TOKEN` is correct
- Ensure bot is not running elsewhere (can't have two instances with same token)

### Payment auto-detection not working
- Verify Binance API credentials are correct
- Check IP whitelist configuration
- Look for API errors in Railway logs
- Test with `scripts/binance_health_check.py`

### Dashboard shows 500 error
- Check `WEB_ADMIN_PASSWORD` and `WEB_SESSION_SECRET` are set
- Verify database path `/data/shop.db` is accessible
- Check Railway logs for Python errors

### Database resets after deployment
- Ensure volume is mounted at `/data`
- Verify `SHOP_DB_PATH=/data/shop.db` environment variable
- Check that the volume is properly attached to the service

### "Workspace still starting" errors
- This is normal on first deploy or after sleep
- Railway services can sleep on free tier — they wake on first request
- Upgrade to Hobby plan ($5/mo) for always-on services

---

## Cost Estimate

Railway pricing (as of 2024):

- **Hobby Plan**: $5/month base + usage
  - $0.000231/GB-hour for memory
  - $0.000463/vCPU-hour for compute
  - 100GB outbound bandwidth included

Estimated total for this bot: **$7-12/month** depending on traffic.

Free trial available with $5 credit.

---

## Updating Your Bot

When you push changes to your GitHub repo:

1. Railway auto-deploys from your default branch
2. Database persists across deployments (it's on the volume)
3. Check **Deployments** tab for status
4. Rollback available if something breaks

---

## Security Checklist

- ✅ Strong `WEB_ADMIN_PASSWORD` (not the default)
- ✅ Random `WEB_SESSION_SECRET` (32+ characters)
- ✅ `WEB_SECURE_COOKIES=true` (HTTPS only)
- ✅ Binance API key is **read-only** (no trading/withdrawal permissions)
- ✅ Binance API IP whitelist enabled (if using static IP)
- ✅ `ADMIN_IDS` set to your Telegram user ID only
- ✅ Regular database backups configured
- ✅ Railway notifications enabled for deployment failures

---

## Advanced: Running Refund App (Optional)

If you want to deploy the refund calculator separately:

1. Create a second Railway service from the same repo
2. Set start command to: `python run_refund_app.py`
3. Add same environment variables (it uses the same database)
4. Set `REFUND_WEB_PORT=$PORT` and `REFUND_WEB_HOST=0.0.0.0`
5. Use a shared volume (or accept separate databases)

However, you can also skip this — the refund app is optional.

---

## Getting Help

- Railway docs: https://docs.railway.app
- Railway Discord: https://discord.gg/railway
- Check `README.md` in this repo for bot-specific questions
- Review `AI_PROJECT_GUIDE.md` for codebase structure

---

## Quick Reference: Environment Variables

Copy this template to Railway's Variables tab:

```env
# Telegram
BOT_TOKEN=
BOT_USERNAME=
ADMIN_IDS=

# Branding
SHOP_NAME=
WELCOME_BLURB=
SUPPORT_USERNAME=
CHANNEL_URL=
DEFAULT_DELIVERY_PROMISE=

# Binance
BINANCE_API_KEY=
BINANCE_API_SECRET=
BINANCE_PAY_UID=
USDT_BEP20_ADDRESS=
USDT_TRC20_ADDRESS=

# Payments
PAYMENT_TTL_MINUTES=15
POLL_INTERVAL_SECONDS=30
MIN_DEPOSIT_USDT=1.0
MAX_DEPOSIT_USDT=5000.0

# Web Dashboard
WEB_HOST=0.0.0.0
WEB_PORT=$PORT
WEB_ADMIN_PASSWORD=
WEB_SESSION_SECRET=
WEB_SECURE_COOKIES=true
WEB_LOGIN_MAX_ATTEMPTS=5
WEB_LOGIN_WINDOW_SECONDS=300
WEB_FORWARDED_ALLOW_IPS=*

# Database
SHOP_DB_PATH=/data/shop.db

# Referral
REFERRAL_PERCENT=5.0
```

Fill in the empty values and you're ready to deploy!
