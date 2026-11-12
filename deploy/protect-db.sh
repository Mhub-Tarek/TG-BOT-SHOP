#!/usr/bin/env bash
# Database safety for deploys. shop.db lives OUTSIDE /opt/telegram-shop so
# "rm -rf /opt/telegram-shop" can never delete customer data.
#
#   bash protect-db.sh migrate          # one-time move from legacy path
#   bash protect-db.sh pre-deploy       # backup + record counts (before wipe)
#   bash protect-db.sh post-deploy      # verify counts; abort/restore if bad
#   bash protect-db.sh install          # copy self to /usr/local/bin
set -euo pipefail

DATA_DIR="${TS_DATA_DIR:-/var/lib/telegram-shop}"
LIVE_DB="${TS_DB_PATH:-$DATA_DIR/shop.db}"
LEGACY_DB="/opt/telegram-shop/shop.db"
SVC_USER="${TS_SVC_USER:-shopbot}"
DEPLOY_STATE="$DATA_DIR/.deploy-state"
BACKUP_DIR="/var/backups/telegram-shop"
MIN_USERS_ABORT=5

db_stats() {
  /usr/bin/python3 - "$1" <<'PY'
import sqlite3, sys
p = sys.argv[1]
try:
    c = sqlite3.connect(p)
    u = c.execute("select count(*) from users").fetchone()[0]
    o = c.execute("select count(*) from orders").fetchone()[0]
    p_count = c.execute("select count(*) from products").fetchone()[0]
    print(f"{u} {o} {p_count}")
except Exception:
    print("0 0 0")
PY
}

ensure_data_dir() {
  mkdir -p "$DATA_DIR"
  if id -u "$SVC_USER" >/dev/null 2>&1; then
    chown "$SVC_USER":"$SVC_USER" "$DATA_DIR"
  fi
  chmod 700 "$DATA_DIR"
}

migrate_legacy() {
  ensure_data_dir
  if [ -f "$LIVE_DB" ]; then
    echo "==> Database already at $LIVE_DB ($(db_stats "$LIVE_DB"))"
    return 0
  fi
  if [ ! -f "$LEGACY_DB" ]; then
    echo "==> No legacy database at $LEGACY_DB (fresh install?)"
    return 0
  fi
  echo "==> Migrating shop.db from $LEGACY_DB -> $LIVE_DB (online backup)..."
  /usr/bin/python3 - <<PY
import sqlite3
src = sqlite3.connect("file:${LEGACY_DB}?mode=ro", uri=True)
dst = sqlite3.connect("${LIVE_DB}")
src.backup(dst)
dst.close()
src.close()
PY
  if id -u "$SVC_USER" >/dev/null 2>&1; then
    chown "$SVC_USER":"$SVC_USER" "$LIVE_DB"
  fi
  chmod 640 "$LIVE_DB"
  echo "==> Migration complete: $(db_stats "$LIVE_DB")"
}

run_backup() {
  local script=""
  for candidate in /opt/telegram-shop/deploy/backup.py /usr/local/bin/telegram-shop-backup.py; do
    if [ -f "$candidate" ]; then
      script="$candidate"
      break
    fi
  done
  if [ -z "$script" ]; then
    echo "WARN: backup.py not found — skipping pre-deploy backup"
    return 0
  fi
  echo "==> Pre-deploy backup ($script)..."
  TS_DB_PATH="$LIVE_DB" /usr/bin/python3 "$script" || {
    echo "WARN: pre-deploy backup failed (continuing — live db is outside /opt)"
    return 0
  }
}

save_deploy_state() {
  ensure_data_dir
  migrate_legacy
  local stats
  if [ -f "$LIVE_DB" ]; then
    stats="$(db_stats "$LIVE_DB")"
  else
    stats="0 0 0"
  fi
  echo "$stats" > "$DEPLOY_STATE"
  echo "==> Pre-deploy snapshot saved: users/orders/products = $stats"
}

pre_deploy() {
  migrate_legacy
  run_backup
  save_deploy_state
}

restore_from_latest_backup() {
  local src="${BACKUP_DIR}/shop.db.latest.gz"
  [ -e "$src" ] || { echo "ERROR: no backup at $src"; return 1; }
  echo "==> Restoring $LIVE_DB from $(readlink -f "$src")..."
  systemctl stop telegram-shop 2>/dev/null || true
  systemctl stop telegram-shop-refund 2>/dev/null || true
  local tmp
  tmp="$(mktemp /tmp/shop.restore.XXXXXX.db)"
  gunzip -c "$(readlink -f "$src")" > "$tmp"
  local check
  check="$(/usr/bin/python3 - "$tmp" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
print(c.execute("PRAGMA integrity_check").fetchone()[0])
PY
)"
  [ "$check" = "ok" ] || { echo "ERROR: backup integrity_check failed"; rm -f "$tmp"; return 1; }
  mv "$tmp" "$LIVE_DB"
  rm -f "${LIVE_DB}-wal" "${LIVE_DB}-shm"
  if id -u "$SVC_USER" >/dev/null 2>&1; then
    chown "$SVC_USER":"$SVC_USER" "$LIVE_DB"
  fi
  chmod 640 "$LIVE_DB"
}

post_deploy() {
  ensure_data_dir
  if [ ! -f "$LIVE_DB" ]; then
    echo "ERROR: $LIVE_DB missing after deploy — attempting restore from latest backup"
    restore_from_latest_backup || {
      echo "FATAL: no database and restore failed — deploy aborted"
      exit 1
    }
  fi

  read -r post_u post_o post_p <<< "$(db_stats "$LIVE_DB")"
  read -r pre_u pre_o pre_p <<< "$(cat "$DEPLOY_STATE" 2>/dev/null || echo '0 0 0')"

  echo "==> Post-deploy db: users=$post_u orders=$post_o products=$post_p (was: $pre_u/$pre_o/$pre_p)"

  if [ "$pre_u" -ge "$MIN_USERS_ABORT" ] && [ "$post_u" -lt "$pre_u" ]; then
    echo "FATAL: user count dropped ($pre_u -> $post_u) — restoring backup and aborting deploy"
    restore_from_latest_backup
    exit 1
  fi

  if [ "$pre_u" -ge "$MIN_USERS_ABORT" ] && [ "$post_u" -lt "$MIN_USERS_ABORT" ]; then
    echo "FATAL: database looks empty ($post_u users) — restoring backup and aborting deploy"
    restore_from_latest_backup
    exit 1
  fi

  if [ "$post_u" -lt "$MIN_USERS_ABORT" ] && [ -e "${BACKUP_DIR}/shop.db.latest.gz" ]; then
    local tmpdb
    tmpdb="$(mktemp /tmp/shop.check.XXXXXX.db)"
    gunzip -c "$(readlink -f "${BACKUP_DIR}/shop.db.latest.gz")" > "$tmpdb"
    read -r bak_u bak_o bak_p <<< "$(db_stats "$tmpdb")"
    rm -f "$tmpdb"
    if [ "$bak_u" -ge "$MIN_USERS_ABORT" ] && [ "$post_u" -lt "$bak_u" ]; then
      echo "FATAL: live db ($post_u users) worse than latest backup ($bak_u users) — restoring"
      restore_from_latest_backup
      exit 1
    fi
  fi

  echo "==> Database safety check PASSED"
}

install_script() {
  install -m 0755 "$(dirname "$0")/protect-db.sh" /usr/local/bin/telegram-shop-protect-db.sh 2>/dev/null || \
    install -m 0755 /opt/telegram-shop/deploy/protect-db.sh /usr/local/bin/telegram-shop-protect-db.sh
  echo "==> Installed /usr/local/bin/telegram-shop-protect-db.sh"
}

cmd="${1:-}"
case "$cmd" in
  migrate) migrate_legacy ;;
  pre-deploy) pre_deploy ;;
  post-deploy) post_deploy ;;
  install) install_script ;;
  *)
    echo "Usage: $0 {migrate|pre-deploy|post-deploy|install}"
    exit 1
    ;;
esac
