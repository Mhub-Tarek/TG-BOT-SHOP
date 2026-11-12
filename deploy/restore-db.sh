#!/usr/bin/env bash
# Restore shop.db from a backup made by backup.py. Safe, verified, reversible.
#
#   bash restore-db.sh                       # restore newest backup (shop.db.latest.gz)
#   bash restore-db.sh /var/backups/telegram-shop/shop.db.20260602-005600.gz
#
# It stops the bot, integrity-checks the chosen backup, keeps a safety copy of
# the CURRENT db, swaps the backup in, fixes ownership, and restarts the bot.
set -euo pipefail

APP_DIR=/opt/telegram-shop
BACKUP_DIR=/var/backups/telegram-shop
DB="${SHOP_DB_PATH:-${TS_DB_PATH:-/var/lib/telegram-shop/shop.db}}"
SVC=telegram-shop
SVC_USER=shopbot
SRC="${1:-$BACKUP_DIR/shop.db.latest.gz}"

[ -e "$SRC" ] || { echo "ERROR: backup not found: $SRC"; echo "Available:"; ls -1 "$BACKUP_DIR"/shop.db.*.gz 2>/dev/null | tail -n 20; exit 1; }
# Resolve symlink (latest) to its real target for the log.
SRC_REAL="$(readlink -f "$SRC")"
echo "==> Restoring from: $SRC_REAL"
echo "==> Target database: $DB"

mkdir -p "$(dirname "$DB")"

TMP="$(mktemp /tmp/shop.restore.XXXXXX.db)"
gunzip -c "$SRC_REAL" > "$TMP"

echo "==> Verifying integrity of the backup..."
RES="$(/usr/bin/python3 - "$TMP" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
print(c.execute("PRAGMA integrity_check").fetchone()[0])
print("products=", c.execute("select count(*) from products").fetchone()[0])
print("users=", c.execute("select count(*) from users").fetchone()[0])
print("orders=", c.execute("select count(*) from orders").fetchone()[0])
PY
)"
echo "$RES"
echo "$RES" | head -1 | grep -qx ok || { echo "ERROR: backup failed integrity check — aborting."; rm -f "$TMP"; exit 2; }

echo "==> Stopping $SVC..."
systemctl stop "$SVC" || true
systemctl stop telegram-shop-refund || true

if [ -f "$DB" ]; then
  SAFETY="$BACKUP_DIR/shop.db.pre-restore-$(date -u +%Y%m%d-%H%M%S)"
  cp "$DB" "$SAFETY"; echo "==> Saved current db to $SAFETY"
fi
rm -f "$DB-wal" "$DB-shm"
mv "$TMP" "$DB"
chown "$SVC_USER":"$SVC_USER" "$DB" 2>/dev/null || true
chmod 640 "$DB"

echo "==> Starting $SVC..."
systemctl start "$SVC"
systemctl start telegram-shop-refund 2>/dev/null || true
sleep 4
systemctl is-active "$SVC"
echo "==> Restore complete."
