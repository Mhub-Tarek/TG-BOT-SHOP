# Deploy telegram-shop to Hostinger VPS via SSH key (no password in chat).
param(
    [string]$Server = "72.60.191.224",
    [string]$User = "root",
    [string]$Key = "$env:USERPROFILE\.ssh\telegram_shop_deploy"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Zip = Join-Path (Split-Path -Parent $ProjectRoot) "telegram-shop-deploy.zip"
$Ssh = @("-i", $Key, "-o", "StrictHostKeyChecking=accept-new", "-o", "BatchMode=yes")

Write-Host "==> Testing SSH to ${User}@${Server}..."
& ssh @Ssh "${User}@${Server}" "echo SSH OK && uname -a"
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "SSH failed. Add the deploy key in Hostinger Browser Terminal first (see README in chat)."
    exit 1
}

Write-Host "==> Building zip..."
if (Test-Path $Zip) { Remove-Item $Zip }
$items = @(
    "run.py","run_refund_app.py","bot.py","config.py","db.py","states.py","i18n.py","requirements.txt",
    ".env.example","handlers","keyboards","services","webapp","refund_app","deploy",
    "tests","scripts","assets\brand-logos","assets\brand-logos-curated",
    "AI_PROJECT_GUIDE.md"
) | ForEach-Object { Join-Path $ProjectRoot $_ }
Compress-Archive -Path $items -DestinationPath $Zip -Force

Write-Host "==> Uploading zip + .env..."
& scp @Ssh $Zip "${User}@${Server}:/root/telegram-shop-deploy.zip"
& scp @Ssh (Join-Path $ProjectRoot ".env") "${User}@${Server}:/root/telegram-shop.env"

Write-Host "==> Installing on server..."
$remote = @'
set -e
# .env carries live secrets — restrict it the moment it lands. (review H7)
chmod 600 /root/telegram-shop.env 2>/dev/null || true
apt-get update -qq
apt-get install -y -qq unzip

# ── DATABASE SAFETY ─────────────────────────────────────────────────────────
# shop.db lives at /var/lib/telegram-shop/shop.db — NEVER inside /opt.
# Deploy only updates code; rm -rf /opt/telegram-shop cannot touch customer data.
PROTECT=/usr/local/bin/telegram-shop-protect-db.sh
if [ -f /opt/telegram-shop/deploy/protect-db.sh ]; then
  bash /opt/telegram-shop/deploy/protect-db.sh pre-deploy
elif [ -x "$PROTECT" ]; then
  bash "$PROTECT" pre-deploy
else
  DATA_DIR=/var/lib/telegram-shop
  LIVE_DB="$DATA_DIR/shop.db"
  mkdir -p "$DATA_DIR"
  if [ ! -f "$LIVE_DB" ] && [ -f /opt/telegram-shop/shop.db ]; then
    echo "==> Migrating shop.db to $LIVE_DB..."
    python3 -c "import sqlite3;s=sqlite3.connect('file:/opt/telegram-shop/shop.db?mode=ro',uri=True);d=sqlite3.connect('$LIVE_DB');s.backup(d);d.close();s.close()"
    chown shopbot:shopbot "$LIVE_DB" 2>/dev/null || true
  fi
fi

systemctl stop telegram-shop 2>/dev/null || true
systemctl stop telegram-shop-refund 2>/dev/null || true

# Preserve SERVER-GENERATED logo data across the wipe (not the database).
rm -rf /root/telegram-shop-preserve
mkdir -p /root/telegram-shop-preserve/keyboards
[ -d /opt/telegram-shop/assets/brand-logos ] && cp -a /opt/telegram-shop/assets/brand-logos /root/telegram-shop-preserve/ 2>/dev/null || true
cp -a /opt/telegram-shop/keyboards/*_ids.json /root/telegram-shop-preserve/keyboards/ 2>/dev/null || true

# Wipe CODE ONLY — database is outside /opt
rm -rf /opt/telegram-shop
mkdir -p /opt/telegram-shop
if ! unzip -qo /root/telegram-shop-deploy.zip -d /opt/telegram-shop; then
  if find /opt/telegram-shop -maxdepth 4 -type f -name run.py | grep -q .; then
    echo "WARN: unzip reported warnings after extracting deploy archive; continuing"
  else
    echo "ERROR: deploy archive extraction failed before run.py was available"
    exit 1
  fi
fi
# flatten if zip wrapped paths
if [ -f /opt/telegram-shop/run.py ]; then true; else
  shopt -s nullglob
  for d in /opt/telegram-shop/*/; do
    if [ -f "${d}run.py" ]; then mv "${d}"* /opt/telegram-shop/; rmdir "$d" 2>/dev/null || true; fi
  done
fi
if [ ! -f /root/telegram-shop.env ]; then
  echo "ERROR: /root/telegram-shop.env missing after upload"
  exit 1
fi
cp /root/telegram-shop.env /opt/telegram-shop/.env
chmod 600 /opt/telegram-shop/.env

# Verify database survived (abort + restore if not)
bash /opt/telegram-shop/deploy/protect-db.sh post-deploy
bash /opt/telegram-shop/deploy/protect-db.sh install

# Restore preserved server-generated logo data (server copies win over the zip seed)
mkdir -p /opt/telegram-shop/assets/brand-logos
[ -d /root/telegram-shop-preserve/brand-logos ] && cp -a /root/telegram-shop-preserve/brand-logos/* /opt/telegram-shop/assets/brand-logos/ 2>/dev/null || true
cp -a /root/telegram-shop-preserve/keyboards/*_ids.json /opt/telegram-shop/keyboards/ 2>/dev/null || true
python3 - <<'PY'
import json
from pathlib import Path

bad_ids = {"5229064374403998351"}
path = Path("/opt/telegram-shop/keyboards/mix_emoji_ids.json")
if path.exists():
    data = json.loads(path.read_text(encoding="utf-8"))
    by_emoji = data.get("by_emoji") or {}
    for emoji, ids in list(by_emoji.items()):
        by_emoji[emoji] = [str(cid) for cid in ids if str(cid) not in bad_ids]
    data["flat"] = [
        row for row in (data.get("flat") or [])
        if str(row.get("id")) not in bad_ids
    ]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
PY
rm -rf /root/telegram-shop-preserve
chmod +x /opt/telegram-shop/deploy/remote-bootstrap.sh
bash /opt/telegram-shop/deploy/remote-bootstrap.sh
'@
$RemoteScript = Join-Path ([System.IO.Path]::GetTempPath()) "telegram-shop-install-$PID.sh"
try {
    $Utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($RemoteScript, ($remote -replace "`r", ""), $Utf8NoBom)
    & scp @Ssh $RemoteScript "${User}@${Server}:/root/telegram-shop-install.sh"
    if ($LASTEXITCODE -ne 0) {
        throw "Remote install script upload failed."
    }
    & ssh @Ssh "${User}@${Server}" "sed -i 's/\r$//' /root/telegram-shop-install.sh && bash /root/telegram-shop-install.sh"
    if ($LASTEXITCODE -ne 0) {
        throw "Remote install failed."
    }
} finally {
    Remove-Item $RemoteScript -Force -ErrorAction SilentlyContinue
}
Write-Host "==> Done."
