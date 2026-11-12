#!/usr/bin/env bash
# Poll until admin/refund DNS points at this VPS, then run enable-domain.sh.
# Usage:
#   nohup bash wait-and-enable-domain.sh akunding.shop you@email.com >> /var/log/telegram-shop/domain-enable.log 2>&1 &
set -euo pipefail

DOMAIN="${1:-}"
EMAIL="${2:-}"
APP_DIR="${APP_DIR:-/opt/telegram-shop}"
LOG="${LOG:-/var/log/telegram-shop/domain-enable.log}"
ADMIN_HOST="admin.${DOMAIN}"
REFUND_HOST="refund.${DOMAIN}"
INTERVAL="${INTERVAL:-60}"

if [[ -z "${DOMAIN}" ]]; then
  echo "Usage: $0 YOURDOMAIN.COM you@email.com"
  exit 1
fi

mkdir -p "$(dirname "${LOG}")"
VPS_IP="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')"

log() { echo "$(date -Is) $*" | tee -a "${LOG}"; }

log "Waiting for DNS: ${ADMIN_HOST} and ${REFUND_HOST} -> ${VPS_IP}"

while true; do
  ok=1
  for host in "${ADMIN_HOST}" "${REFUND_HOST}"; do
    resolved="$(dig +short "${host}" A @8.8.8.8 2>/dev/null | head -1)"
    if [[ -z "${resolved}" ]]; then
      resolved="$(getent ahostsv4 "${host}" 2>/dev/null | awk '{print $1; exit}')"
    fi
    if [[ "${resolved}" != "${VPS_IP}" ]]; then
      log "  ${host} -> ${resolved:-<not set>} (need ${VPS_IP})"
      ok=0
    fi
  done
  if [[ "${ok}" -eq 1 ]]; then
    log "DNS ready. Running enable-domain.sh ..."
    if [[ -n "${EMAIL}" ]]; then
      bash "${APP_DIR}/deploy/enable-domain.sh" "${DOMAIN}" "${EMAIL}"
    else
      bash "${APP_DIR}/deploy/enable-domain.sh" "${DOMAIN}" "ssl@${DOMAIN}" --no-email
    fi
    log "Finished."
    exit 0
  fi
  sleep "${INTERVAL}"
done
