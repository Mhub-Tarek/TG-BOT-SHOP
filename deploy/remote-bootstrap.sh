#!/usr/bin/env bash
# Run once on the VPS as root after code is in /opt/telegram-shop
set -euo pipefail

APP_DIR=/opt/telegram-shop
SVC_USER=shopbot
cd "$APP_DIR"

if [ ! -f .env ]; then
  echo "ERROR: .env missing — copy it from your PC first."
  exit 1
fi

# The admin dashboard handles real money, so it must sit behind the nginx
# reverse proxy + TLS, NOT be exposed directly on a public interface. Bind it
# to loopback and mark the session cookie Secure (HTTPS-only). (review H2)
ensure_kv () {  # ensure_kv KEY VALUE  — set or replace KEY=VALUE in .env
  local key="$1" val="$2"
  if grep -q "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${val}|" .env
  else
    echo "${key}=${val}" >> .env
  fi
}
ensure_kv WEB_HOST 127.0.0.1
ensure_kv WEB_SECURE_COOKIES true
ensure_kv SHOP_DB_PATH /var/lib/telegram-shop/shop.db

bash deploy/setup.sh

# Lock down .env: it holds the bot token, Binance keys, admin password and
# session secret. Owned by the service user, readable only by it, and the
# transient upload copy in /root is shredded. (review H7)
if id -u "$SVC_USER" >/dev/null 2>&1; then
  chown "$SVC_USER":"$SVC_USER" "$APP_DIR/.env"
fi
chmod 600 "$APP_DIR/.env"
if [ -f /root/telegram-shop.env ]; then
  shred -u /root/telegram-shop.env 2>/dev/null || rm -f /root/telegram-shop.env
fi

# Firewall: only expose HTTP/HTTPS (nginx). The dashboard's :8000 must never be
# reachable directly from the internet. (review H2)
if command -v ufw >/dev/null 2>&1; then
  ufw allow OpenSSH >/dev/null 2>&1 || true
  ufw allow 'Nginx Full' >/dev/null 2>&1 || ufw allow 80,443/tcp >/dev/null 2>&1 || true
  ufw deny 8000/tcp >/dev/null 2>&1 || true
  yes | ufw enable >/dev/null 2>&1 || true
fi

systemctl restart telegram-shop || systemctl start telegram-shop
systemctl restart telegram-shop-refund || systemctl start telegram-shop-refund || true
sleep 2
systemctl status telegram-shop --no-pager || true
systemctl status telegram-shop-refund --no-pager || true

# Install/refresh automated backups (deploy-proof: script + cron live outside
# /opt, so they survive the next deploy wipe). Idempotent. (backup safety net)
if [ -f deploy/install-backups.sh ]; then
  bash deploy/install-backups.sh || echo "WARN: backup install failed (non-fatal)"
fi
echo
echo "=== Deploy done ==="
echo "Dashboard binds 127.0.0.1:8000 — reach it through nginx over HTTPS:"
echo "  1) cp $APP_DIR/deploy/nginx.conf.example /etc/nginx/sites-available/telegram-shop-admin"
echo "  2) edit server_name, symlink into sites-enabled, then: nginx -t && systemctl reload nginx"
echo "     (the 443 block ships commented, so this passes before a cert exists)"
echo "  3) certbot --nginx -d <your-admin-host>   # issues the cert + injects the 443 ssl block"
echo "  4) move the hardening headers from the commented 443 template into certbot's"
echo "     443 block, then: nginx -t && systemctl reload nginx"
echo "Refund calculator binds 127.0.0.1:8010 — expose it with:"
echo "  cp $APP_DIR/deploy/nginx-refund-ip.conf.example /etc/nginx/sites-available/telegram-shop-refund"
echo "  ln -s ../sites-available/telegram-shop-refund /etc/nginx/sites-enabled/"
echo "  nginx -t && systemctl reload nginx"
echo "  URL: http://72.60.191.224/refund"
echo "Logs: journalctl -u telegram-shop -f"
