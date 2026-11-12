#!/usr/bin/env bash
# After HTTPS subdomains work, redirect http://VPS_IP/... to the new hosts.
set -euo pipefail

DOMAIN="${1:-}"
if [[ -z "${DOMAIN}" ]]; then
  echo "Usage: $0 YOURDOMAIN.COM"
  exit 1
fi

VPS_IP="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')"
CONF="/etc/nginx/sites-available/telegram-shop-ip-redirect"

cat > "${CONF}" <<EOF
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name ${VPS_IP} _;

    location /refund {
        return 301 https://refund.${DOMAIN}\$request_uri;
    }

    location / {
        return 301 https://admin.${DOMAIN}\$request_uri;
    }
}
EOF

ln -sf "../sites-available/telegram-shop-ip-redirect" /etc/nginx/sites-enabled/telegram-shop-ip-redirect
nginx -t
systemctl reload nginx
echo "IP http://${VPS_IP}/ now redirects to HTTPS subdomains."
