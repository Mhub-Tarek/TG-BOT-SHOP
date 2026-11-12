#!/usr/bin/env bash
# Serve a landing page on https://YOURDOMAIN/ (apex + www).
# Requires A records: @ and www -> VPS IP (Spaceship Advanced DNS).
set -euo pipefail

DOMAIN="${1:-akunding.shop}"
SKIP_DNS="${SKIP_DNS:-0}"
APP_DIR="${APP_DIR:-/opt/telegram-shop}"
WEB_ROOT="/var/www/${DOMAIN}"
NGINX_SITE="telegram-shop-apex"
VPS_IP="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')"

resolve() {
  dig +short "$1" A @8.8.8.8 2>/dev/null | head -1
}

if [[ "${SKIP_DNS}" != "1" ]]; then
  for host in "${DOMAIN}" "www.${DOMAIN}"; do
    ip="$(resolve "${host}")"
    if [[ "${ip}" != "${VPS_IP}" ]]; then
      echo "ERROR: ${host} -> ${ip:-<missing>} (need ${VPS_IP})"
      echo "Add A records in Spaceship: Host @ and www -> ${VPS_IP}"
      exit 1
    fi
    echo "OK DNS: ${host} -> ${ip}"
  done
else
  echo "SKIP_DNS=1 — installing files/nginx only (run again after DNS propagates for HTTPS)"
fi

echo "[1/4] Static site..."
mkdir -p "${WEB_ROOT}"
cp -a "${APP_DIR}/deploy/www/." "${WEB_ROOT}/"
chown -R www-data:www-data "${WEB_ROOT}"

echo "[2/4] nginx..."
cat > "/etc/nginx/sites-available/${NGINX_SITE}" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN} www.${DOMAIN};

    root ${WEB_ROOT};
    index index.html;

    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }

    location / {
        try_files \$uri \$uri/ =404;
    }
}
EOF

ln -sf "../sites-available/${NGINX_SITE}" "/etc/nginx/sites-enabled/${NGINX_SITE}"
nginx -t
systemctl reload nginx

echo "[3/4] TLS..."
if [[ "${SKIP_DNS}" != "1" ]]; then
  certbot --nginx \
    -d "${DOMAIN}" \
    -d "www.${DOMAIN}" \
    --non-interactive \
    --agree-tos \
    --register-unsafely-without-email \
    --redirect
fi

nginx -t
systemctl reload nginx

echo
echo "Done:"
echo "  https://${DOMAIN}/"
echo "  https://www.${DOMAIN}/"
