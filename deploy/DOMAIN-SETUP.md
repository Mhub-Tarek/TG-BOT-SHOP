# Domain setup (Spaceship + VPS)

## akunding.shop (current)

**VPS IP:** `72.60.191.224`

### Spaceship DNS (do this now)

1. Spaceship → **Domain Manager** → **akunding.shop**
2. **Nameservers & DNS** → **Advanced DNS**
3. Under **Custom records**, add **two A records**:

| Type | Host | Value / Points to |
|------|------|-------------------|
| A | `admin` | `72.60.191.224` |
| A | `refund` | `72.60.191.224` |

4. Save each record. If Spaceship says the host is on **Shared Hosting**, remove that subdomain from Hosting first (see Spaceship docs).

5. Wait 5–30 minutes. The VPS auto-installs HTTPS when both names resolve.

**Your URLs after activation:**

- Admin: `https://admin.akunding.shop/login`
- Refund: `https://refund.akunding.shop/`

---

## Generic steps

1. Buy the domain.
2. Open **DNS** for that domain.
3. Add two **A** records:

| Type | Host | Value |
|------|------|--------|
| A | `admin` | `72.60.191.224` |
| A | `refund` | `72.60.191.224` |

4. Wait until both resolve (often 5–30 minutes).

## What is already on the VPS

- `telegram-shop` → admin on `127.0.0.1:8000`
- `telegram-shop-refund` → refund on `127.0.0.1:8010`
- `deploy/enable-domain.sh` — turns on HTTPS for `admin.YOURDOMAIN` and `refund.YOURDOMAIN`

## One command after DNS works

SSH to the server:

```bash
ssh -i ~/.ssh/telegram_shop_deploy root@72.60.191.224
bash /opt/telegram-shop/deploy/enable-domain.sh YOURDOMAIN.COM your@email.com
```

Use a real email (Let's Encrypt expiry notices).

## Result

| App | URL |
|-----|-----|
| Admin | `https://admin.YOURDOMAIN.COM/login` |
| Refund | `https://refund.YOURDOMAIN.COM/` |

## Optional: stop using the raw IP

```bash
bash /opt/telegram-shop/deploy/enable-domain-ip-redirect.sh YOURDOMAIN.COM
```

## From Windows (update script only)

```powershell
$key = "$env:USERPROFILE\.ssh\telegram_shop_deploy"
$root = "C:\Users\ahmed\Desktop\telegram-shop"
scp -i $key "$root\deploy\enable-domain.sh" "${root}\deploy\enable-domain-ip-redirect.sh" "${root}\deploy\nginx-domain.conf.template" root@72.60.191.224:/opt/telegram-shop/deploy/
ssh -i $key root@72.60.191.224 "chmod +x /opt/telegram-shop/deploy/enable-domain.sh /opt/telegram-shop/deploy/enable-domain-ip-redirect.sh"
```
