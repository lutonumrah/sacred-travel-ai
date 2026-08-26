#!/usr/bin/env bash
# One-time TLS bootstrap. nginx refuses to start without a certificate file,
# and certbot's webroot challenge needs nginx already running — so we plant a
# self-signed placeholder, start nginx, swap in the real certificate, reload.
#
# Run once from the project root:  ./docker/init-letsencrypt.sh
set -euo pipefail

cd "$(dirname "$0")/.."

[ -f .env ] || { echo "!! .env not found. Copy .env.example to .env first."; exit 1; }
set -a; . ./.env; set +a

: "${DOMAIN:?set DOMAIN in .env}"
: "${CERTBOT_EMAIL:?set CERTBOT_EMAIL in .env}"

DOMAINS=(-d "${DOMAIN}" -d "www.${DOMAIN}")
STAGING_ARG=""
if [ "${CERTBOT_STAGING:-false}" = "true" ]; then
    STAGING_ARG="--staging"
    echo "==> Using Let's Encrypt STAGING (certificates will not be trusted)"
fi

compose() { docker compose "$@"; }

if compose run --rm --entrypoint "test -d /etc/letsencrypt/live/${DOMAIN}" certbot 2>/dev/null; then
    echo "==> Certificate for ${DOMAIN} already exists. Nothing to bootstrap."
    exit 0
fi

echo "==> Creating a placeholder certificate for ${DOMAIN}"
compose run --rm --entrypoint "\
  sh -c 'mkdir -p /etc/letsencrypt/live/${DOMAIN} && \
    openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
      -keyout /etc/letsencrypt/live/${DOMAIN}/privkey.pem \
      -out    /etc/letsencrypt/live/${DOMAIN}/fullchain.pem \
      -subj /CN=localhost'" certbot

echo "==> Starting nginx"
compose up -d nginx

echo "==> Removing the placeholder"
compose run --rm --entrypoint "rm -rf /etc/letsencrypt/live/${DOMAIN} /etc/letsencrypt/archive/${DOMAIN} /etc/letsencrypt/renewal/${DOMAIN}.conf" certbot

echo "==> Requesting the real certificate"
compose run --rm --entrypoint "\
  certbot certonly --webroot -w /var/www/certbot \
    ${STAGING_ARG} \
    --email ${CERTBOT_EMAIL} \
    ${DOMAINS[*]} \
    --rsa-key-size 4096 \
    --agree-tos \
    --no-eff-email \
    --non-interactive" certbot

echo "==> Reloading nginx"
compose exec nginx nginx -s reload

echo "==> Done. TLS is live for https://${DOMAIN}"
