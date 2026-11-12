#!/usr/bin/env bash
# Wire admin + refund apps to HTTPS subdomains after DNS is pointed at this VPS.
#
# Usage (on the VPS as root):
#   bash /opt/telegram-shop/deploy/enable-domain.sh YOURDOMAIN.COM you@email.com
#
# Spaceship DNS (before running this):
#   A  admin   -> VPS public IP
#   A  refund  -> VPS public IP
set -euo pipefail

DOMAIN="${1:-}"
EMAIL="${2:-}"
NO_EMAIL="${3:-}"
APP_DIR="${APP_DIR:-/opt/telegram-shop}"
NGINX_SITE="telegram-shop-web"
ADMIN_HOST="admin.${DOMAIN}"
REFUND_HOST="refund.${DOMAIN}"

if [[ -z "${DOMAIN}" ]]; then
  echo "Usage: $0 YOURDOMAIN.COM you@email.com"
  echo "       $0 YOURDOMAIN.COM ignored --no-email"
  exit 1
fi
if [[ -z "${EMAIL}" && "${NO_EMAIL}" != "--no-email" ]]; then
  echo "Usage: $0 YOURDOMAIN.COM you@email.com"
  exit 1
fi

if [[ ! -f "${APP_DIR}/deploy/nginx-domain.conf.template" ]]; then
  echo "Missing ${APP_DIR}/deploy/nginx-domain.conf.template"
  exit 1
fi

VPS_IP="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || true)"
if [[ -z "${VPS_IP}" ]]; then
  VPS_IP="$(hostname -I | awk '{print $1}')"
fi

echo "VPS public IP: ${VPS_IP}"
echo "Expect DNS: ${ADMIN_HOST} and ${REFUND_HOST} -> ${VPS_IP}"

resolve_host() {
  local host="$1"
  local ip
  ip="$(getent ahostsv4 "${host}" 2>/dev/null | awk '{print $1; exit}')"
  if [[ -n "${ip}" ]]; then
    echo "${ip}"
    return
  fi
  if command -v dig >/dev/null 2>&1; then
    dig +short "${host}" A @8.8.8.8 @1.1.1.1 2>/dev/null | head -1
    return
  fi
  host -t A "${host}" 8.8.8.8 2>/dev/null | awk '/has address/ {print $4; exit}'
}

for host in "${ADMIN_HOST}" "${REFUND_HOST}"; do
  resolved="$(resolve_host "${host}")"
  if [[ -z "${resolved}" ]]; then
    echo "ERROR: ${host} does not resolve yet. Add A records in Spaceship, wait a few minutes, retry."
    exit 1
  fi
  if [[ "${resolved}" != "${VPS_IP}" ]]; then
    echo "ERROR: ${host} -> ${resolved} (expected ${VPS_IP}). Fix DNS before continuing."
    exit 1
  fi
  echo "OK DNS: ${host} -> ${resolved}"
done

echo "[1/4] Packages..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq certbot python3-certbot-nginx nginx

echo "[2/4] nginx vhosts..."
mkdir -p /var/www/html
CONF="/etc/nginx/sites-available/${NGINX_SITE}"
sed "s/__DOMAIN__/${DOMAIN}/g" "${APP_DIR}/deploy/nginx-domain.conf.template" > "${CONF}"

if [[ -L /etc/nginx/sites-enabled/telegram-shop-admin ]]; then
  cp -a /etc/nginx/sites-available/telegram-shop-admin \
    "/etc/nginx/sites-available/telegram-shop-admin.bak-$(date +%Y%m%d%H%M%S)" 2>/dev/null || true
  rm -f /etc/nginx/sites-enabled/telegram-shop-admin
fi

ln -sf "../sites-available/${NGINX_SITE}" "/etc/nginx/sites-enabled/${NGINX_SITE}"
rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true

nginx -t
systemctl reload nginx

echo "[3/4] TLS certificates (Let's Encrypt)..."
CERTBOT_ARGS=(
  --nginx
  -d "${ADMIN_HOST}"
  -d "${REFUND_HOST}"
  --non-interactive
  --agree-tos
  --redirect
)
if [[ "${NO_EMAIL}" == "--no-email" ]]; then
  CERTBOT_ARGS+=(--register-unsafely-without-email)
else
  CERTBOT_ARGS+=(-m "${EMAIL}")
fi
certbot "${CERTBOT_ARGS[@]}"

echo "[4/5] Firewall + services..."
if command -v ufw >/dev/null 2>&1; then
  ufw allow 80/tcp >/dev/null 2>&1 || true
  ufw allow 443/tcp >/dev/null 2>&1 || true
  ufw deny 8000/tcp >/dev/null 2>&1 || true
  ufw deny 8010/tcp >/dev/null 2>&1 || true
fi

if [[ -f "${APP_DIR}/.env" ]]; then
  if grep -q '^WEB_SECURE_COOKIES=' "${APP_DIR}/.env"; then
    sed -i 's/^WEB_SECURE_COOKIES=.*/WEB_SECURE_COOKIES=true/' "${APP_DIR}/.env"
  else
    echo 'WEB_SECURE_COOKIES=true' >> "${APP_DIR}/.env"
  fi
  chown shopbot:shopbot "${APP_DIR}/.env"
fi

systemctl restart telegram-shop telegram-shop-refund
systemctl is-active telegram-shop telegram-shop-refund nginx

printf '%s\n' "${DOMAIN}" > /etc/telegram-shop-domain
echo "${EMAIL}" >> /etc/telegram-shop-domain

echo
echo "Done."
echo "  Admin:  https://${ADMIN_HOST}/login"
echo "  Refund: https://${REFUND_HOST}/"
echo
echo "Old IP URLs still work until you run (optional):"
echo "  bash ${APP_DIR}/deploy/enable-domain-ip-redirect.sh ${DOMAIN}"
