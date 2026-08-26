#!/usr/bin/env bash
# Post-deployment verification. Read-only — it changes nothing.
#
#   ./docker/verify.sh
#
# Env overrides (mostly for testing off-VPS):
#   HTTP_PORT=8080 HTTPS_PORT=8443   non-standard ports
#   INSECURE=1                       accept a self-signed / staging cert
#   SKIP_PUBLIC=1                    skip the DNS + public-URL checks

APP_DIR="${APP_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
HTTP_PORT="${HTTP_PORT:-80}"
HTTPS_PORT="${HTTPS_PORT:-443}"

cd "$APP_DIR" || { echo "Cannot cd to $APP_DIR"; exit 1; }

if [ -t 1 ]; then G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; B=$'\033[1m'; N=$'\033[0m'
else G=; R=; Y=; B=; N=; fi

PASS=0; FAIL=0; WARN=0
ok()   { printf "  ${G}PASS${N}  %s\n" "$*"; PASS=$((PASS + 1)); }
bad()  { printf "  ${R}FAIL${N}  %s\n" "$*"; FAIL=$((FAIL + 1)); }
warn() { printf "  ${Y}WARN${N}  %s\n" "$*"; WARN=$((WARN + 1)); }
head_() { printf "\n${B}%s${N}\n" "$*"; }

dc() { docker compose "$@"; }
# Run a python snippet inside the web container against live settings.
djset() {
    dc exec -T web python -c "
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
django.setup()
from django.conf import settings
$1" 2>/dev/null | tr -d '\r'
}

CURL=(curl -sS --max-time 15)
[ "${INSECURE:-0}" = "1" ] && CURL+=(-k)

# ---------------------------------------------------------------- config ----
head_ "1. Configuration"

if [ -f .env ]; then
    ok ".env present"
    set -a; . ./.env 2>/dev/null; set +a
else
    bad ".env missing — copy .env.example and fill it in"
fi

DOMAIN="${DOMAIN:-}"
[ -n "$DOMAIN" ] && ok "DOMAIN = $DOMAIN" || bad "DOMAIN not set in .env"

case "${DEBUG:-}" in
    False|false|0) ok "DEBUG is off" ;;
    *) bad "DEBUG=${DEBUG:-unset} — must be False in production" ;;
esac

case "${SECRET_KEY:-}" in
    ""|change-me-in-production|*insecure*) bad "SECRET_KEY is unset or still the placeholder" ;;
    *) [ "${#SECRET_KEY}" -ge 50 ] && ok "SECRET_KEY looks real (${#SECRET_KEY} chars)" \
                                   || warn "SECRET_KEY is only ${#SECRET_KEY} chars — Django wants 50+" ;;
esac

case "${DATABASE_URL:-}" in
    *data/db.sqlite3) ok "DATABASE_URL points at the mounted volume" ;;
    postgres*)        ok "DATABASE_URL points at Postgres" ;;
    *) warn "DATABASE_URL=${DATABASE_URL:-unset} — outside the volume, data is lost on rebuild" ;;
esac

case "${USE_X_FORWARDED_PROTO:-}" in
    True|true|1) ok "USE_X_FORWARDED_PROTO on (correct behind nginx)" ;;
    *) bad "USE_X_FORWARDED_PROTO must be True behind nginx, or you get a redirect loop" ;;
esac

# ------------------------------------------------------------ containers ----
head_ "2. Containers"

for svc in web nginx certbot; do
    cid="$(dc ps -q "$svc" 2>/dev/null)"
    if [ -z "$cid" ]; then
        bad "$svc is not running"
        continue
    fi
    state="$(docker inspect -f '{{.State.Status}}' "$cid" 2>/dev/null)"
    health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$cid" 2>/dev/null)"
    if [ "$state" != "running" ]; then
        bad "$svc is $state"
    elif [ "$health" = "unhealthy" ]; then
        bad "$svc is running but unhealthy"
    elif [ "$health" = "healthy" ]; then
        ok "$svc running and healthy"
    else
        ok "$svc running"
    fi
done

restarts="$(docker inspect -f '{{.RestartCount}}' "$(dc ps -q web 2>/dev/null)" 2>/dev/null || echo 0)"
[ "${restarts:-0}" -gt 3 ] && warn "web has restarted $restarts times — check: docker compose logs web" \
                           || ok "web is not crash-looping"

# ---------------------------------------------------------------- django ----
head_ "3. Django"

# Every check below shells into the web container. If it is down they would all
# silently produce empty output, which must not read as a pass.
if [ -z "$(dc ps -q web 2>/dev/null)" ] || \
   [ "$(docker inspect -f '{{.State.Status}}' "$(dc ps -q web)" 2>/dev/null)" != "running" ]; then
    bad "web container is not running — skipping the Django checks"
else

if dc exec -T web python manage.py migrate --check >/dev/null 2>&1; then
    ok "all migrations applied"
else
    bad "unapplied migrations — run: docker compose exec web python manage.py migrate"
fi

if dc exec -T web python manage.py check --deploy --fail-level ERROR >/dev/null 2>&1; then
    ok "Django deployment checks pass (no ERRORs)"
else
    bad "Django check --deploy reported ERRORs — run it to see them:
        docker compose exec web python manage.py check --deploy"
fi

users="$(djset "from django.contrib.auth import get_user_model; print(get_user_model().objects.filter(is_superuser=True).count())")"
[ "${users:-0}" -gt 0 ] && ok "$users superuser(s) exist" \
                        || warn "no superuser — run: docker compose exec web python manage.py createsuperuser"

sfiles="$(dc exec -T web sh -c 'find /app/staticfiles -type f 2>/dev/null | wc -l' | tr -d '\r ')"
[ "${sfiles:-0}" -gt 50 ] && ok "$sfiles static files collected" \
                          || bad "only ${sfiles:-0} static files — collectstatic did not run"

dbsize="$(dc exec -T web sh -c 'du -h /app/data/db.sqlite3 2>/dev/null | cut -f1' | tr -d '\r ')"
[ -n "$dbsize" ] && ok "database present on the volume ($dbsize)" \
                 || warn "no db.sqlite3 on the volume — is DATABASE_URL using data/ ?"

fi

# ------------------------------------------------------------ nginx / TLS ----
head_ "4. nginx and TLS (local)"

BASE="https://${DOMAIN}:${HTTPS_PORT}"
RESOLVE=(--resolve "${DOMAIN}:${HTTPS_PORT}:127.0.0.1")
RESOLVE_HTTP=(--resolve "${DOMAIN}:${HTTP_PORT}:127.0.0.1")

redir="$("${CURL[@]}" "${RESOLVE_HTTP[@]}" -o /dev/null -w '%{http_code} %{redirect_url}' \
         "http://${DOMAIN}:${HTTP_PORT}/" 2>/dev/null)"
case "$redir" in
    30[18]*https://*) ok "HTTP redirects to HTTPS ($redir)" ;;
    *) bad "HTTP did not redirect to HTTPS (got: ${redir:-no response})" ;;
esac

code="$("${CURL[@]}" "${RESOLVE[@]}" -o /dev/null -w '%{http_code}' "$BASE/api/v1/health/" 2>/dev/null)"
[ "$code" = "200" ] && ok "health endpoint returns 200 over HTTPS" \
                    || bad "health endpoint returned ${code:-no response}"

body="$("${CURL[@]}" "${RESOLVE[@]}" "$BASE/api/v1/health/" 2>/dev/null)"
case "$body" in
    *'"database":"ok"'*) ok "health reports the database is reachable" ;;
    *) bad "health did not report a healthy database: ${body:-empty}" ;;
esac

code="$("${CURL[@]}" "${RESOLVE[@]}" -o /dev/null -w '%{http_code}' "$BASE/static/admin/css/base.css" 2>/dev/null)"
[ "$code" = "200" ] && ok "nginx serves /static/" || bad "/static/ returned ${code:-no response}"

code="$("${CURL[@]}" "${RESOLVE[@]}" -o /dev/null -w '%{http_code}' "$BASE/auth/login/" 2>/dev/null)"
[ "$code" = "200" ] && ok "login page renders" || bad "login page returned ${code:-no response}"

hsts="$("${CURL[@]}" "${RESOLVE[@]}" -sI "$BASE/auth/login/" 2>/dev/null | grep -ci strict-transport-security || true)"
[ "${hsts:-0}" -gt 0 ] && ok "HSTS header present" || warn "no HSTS header"

if [ "${INSECURE:-0}" != "1" ]; then
    if curl -sS --max-time 15 "${RESOLVE[@]}" -o /dev/null "$BASE/api/v1/health/" 2>/dev/null; then
        ok "TLS certificate is valid and matches $DOMAIN"
    else
        bad "TLS certificate is not trusted for $DOMAIN (self-signed, staging, or wrong name)"
    fi
fi

certinfo="$(dc run --rm --entrypoint "certbot certificates" certbot 2>/dev/null \
            | grep -E "Domains:|Expiry Date:" | sed 's/^ *//')"
if [ -n "$certinfo" ]; then
    while IFS= read -r line; do ok "$line"; done <<<"$certinfo"
else
    warn "certbot reports no certificates — has ./docker/init-letsencrypt.sh been run?"
fi

# ------------------------------------------------------------ public URL ----
if [ "${SKIP_PUBLIC:-0}" != "1" ]; then
    head_ "5. Public reachability"

    resolved="$(getent hosts "$DOMAIN" 2>/dev/null | awk '{print $1}' | head -1)"
    if [ -n "$resolved" ]; then
        ok "$DOMAIN resolves to $resolved"
        if command -v hostname >/dev/null && hostname -I 2>/dev/null | grep -qw "$resolved"; then
            ok "that IP belongs to this machine"
        else
            warn "$resolved is not an address of this machine — fine if behind a proxy, otherwise check DNS"
        fi
    else
        bad "$DOMAIN does not resolve — DNS not propagated yet"
    fi

    code="$("${CURL[@]}" -o /dev/null -w '%{http_code}' "https://${DOMAIN}/api/v1/health/" 2>/dev/null)"
    [ "$code" = "200" ] && ok "https://${DOMAIN}/ is reachable from here" \
                        || bad "https://${DOMAIN}/ returned ${code:-no response} — check firewall (ufw + hPanel)"
fi

# ----------------------------------------------------------------- disk ----
head_ "6. Host"

avail="$(df -P . | awk 'NR==2 {print $4}')"
pct="$(df -P . | awk 'NR==2 {gsub(/%/,"",$5); print $5}')"
[ "${pct:-0}" -lt 85 ] && ok "disk ${pct}% used ($((avail / 1024)) MB free)" \
                       || warn "disk ${pct}% used — prune images: docker image prune -a"

# --------------------------------------------------------------- summary ----
printf "\n${B}%s${N}\n" "Summary"
printf "  ${G}%d passed${N}   ${Y}%d warnings${N}   ${R}%d failed${N}\n" "$PASS" "$WARN" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    printf "\n  Something is wrong. Start with:  docker compose logs --tail 50 web\n\n"
    exit 1
fi
printf "\n  Everything checks out.\n\n"
