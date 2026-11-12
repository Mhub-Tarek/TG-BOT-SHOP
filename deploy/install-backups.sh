#!/usr/bin/env bash
# Install/refresh the automated backup system on the VPS. Idempotent — safe to
# re-run on every deploy. Installs backup.py to a deploy-proof location outside
# /opt (so it survives the deploy wipe), creates the backup dir, installs the
# cron job, and runs one backup immediately to verify.
set -euo pipefail

APP_DIR=/opt/telegram-shop
BIN=/usr/local/bin/telegram-shop-backup.py
BACKUP_DIR=/var/backups/telegram-shop
CRON=/etc/cron.d/telegram-shop-backup
LOG=/var/log/telegram-shop-backup.log

echo "==> Installing backup script to $BIN"
install -m 0755 "$APP_DIR/deploy/backup.py" "$BIN"
if [ -f "$APP_DIR/deploy/protect-db.sh" ]; then
  install -m 0755 "$APP_DIR/deploy/protect-db.sh" /usr/local/bin/telegram-shop-protect-db.sh
fi

echo "==> Creating backup dir $BACKUP_DIR (root-only, holds .env secrets)"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

echo "==> Installing cron job every 30 min -> $CRON"
cat > "$CRON" <<EOF
# telegram-shop automated backups (managed by deploy/install-backups.sh)
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
*/30 * * * * root TS_DB_PATH=/var/lib/telegram-shop/shop.db /usr/bin/python3 $BIN >> $LOG 2>&1
EOF
chmod 644 "$CRON"
touch "$LOG"; chmod 600 "$LOG"

# cron.d ignores files with a "." in the name on some systems; ours has none.
systemctl reload cron 2>/dev/null || systemctl restart cron 2>/dev/null || true

echo "==> Running one backup now to verify..."
TS_DB_PATH=/var/lib/telegram-shop/shop.db /usr/bin/python3 "$BIN" >> "$LOG" 2>&1
echo "==> Latest log lines:"; tail -n 6 "$LOG"
echo "==> Backups present:"; ls -lah "$BACKUP_DIR" | tail -n 10
echo "==> Done. Schedule: every 30 min. Keep all for 3 days, 1/day for 30 days."
