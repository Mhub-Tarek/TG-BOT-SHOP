#!/usr/bin/env bash
# One-shot VPS bootstrap. Tested on Ubuntu 22.04 / 24.04.
# Run as root:
#   bash deploy/setup.sh
set -euo pipefail

APP_DIR=/opt/telegram-shop
LOG_DIR=/var/log/telegram-shop
DATA_DIR=/var/lib/telegram-shop
SVC_USER=shopbot

echo "[1/7] System packages..."
apt-get update
apt-get install -y python3 python3-venv python3-pip git nginx

echo "[2/7] App user..."
id -u "$SVC_USER" >/dev/null 2>&1 || useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$SVC_USER"

echo "[3/7] Directories..."
mkdir -p "$APP_DIR" "$LOG_DIR" "$DATA_DIR"
chown -R "$SVC_USER":"$SVC_USER" "$APP_DIR" "$LOG_DIR" "$DATA_DIR"
chmod 700 "$DATA_DIR"

echo "[4/7] Python venv + deps (run as $SVC_USER)..."
if [ ! -d "$APP_DIR/.venv" ]; then
  sudo -u "$SVC_USER" python3 -m venv "$APP_DIR/.venv"
fi
sudo -u "$SVC_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip
sudo -u "$SVC_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "[5/7] systemd unit..."
cp "$APP_DIR/deploy/telegram-shop.service" /etc/systemd/system/
cp "$APP_DIR/deploy/telegram-shop-refund.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable telegram-shop.service
systemctl enable telegram-shop-refund.service

echo "[6/7] Reminder — nginx vhost..."
echo "  cp $APP_DIR/deploy/nginx.conf.example /etc/nginx/sites-available/telegram-shop-admin"
echo "  cp $APP_DIR/deploy/nginx-refund-ip.conf.example /etc/nginx/sites-available/telegram-shop-refund"
echo "  ln -s ../sites-available/telegram-shop-admin /etc/nginx/sites-enabled/"
echo "  edit server_name, then: nginx -t && systemctl reload nginx"
echo "  certbot --nginx -d shop-admin.yourdomain.com"

echo "[7/7] Start the service:"
echo "  sudo systemctl start telegram-shop"
echo "  sudo systemctl start telegram-shop-refund"
echo "  sudo journalctl -u telegram-shop -f      # follow logs"
echo
echo "Don't forget: cp .env.example .env  &&  nano .env"
