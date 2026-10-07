#!/usr/bin/env bash
# Cross-browser smoke suite: builds the app image, runs it with demo data on a
# private Docker network next to a separate-origin "partner site" that embeds
# the chat widget, then runs browser_tests/ with pytest-playwright in the
# official Playwright image against Chromium, Firefox and WebKit, each at
# desktop (1280x800) and on emulated iPhone and Pixel devices.
#
#   browser_tests/run.sh                 # everything
#   browser_tests/run.sh -k widget -x    # extra args go to pytest
#   BROWSERS="chromium" browser_tests/run.sh
#
# Everything this script creates (containers, network, the app image, the temp
# site directory) is removed on exit; the pulled Playwright image is kept.
# Screenshots, a JUnit report and the app log land in browser_tests/artifacts/.
#
# Topology (network $NET):
#   travel-os              Django, DEBUG off, demo data seeded       :8000
#   www.partner-site.test  the test page embedding widget.js          :8080
#   runner                 Playwright + pytest
#
# Static files: whitenoise is not installed and production serves /static/
# from nginx, so the app runs under `manage.py runserver --insecure
# --noreload`, which serves /static/ with DEBUG off. That keeps the stack to
# one app container; the browser behaviour under test (templates, CSS, JS,
# CORS, cookies) is the same as behind nginx + gunicorn.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"

PW_IMAGE="${PW_IMAGE:-mcr.microsoft.com/playwright/python:v1.63.0-noble}"
BROWSERS="${BROWSERS:-chromium firefox webkit}"

RUN_ID="stabt-$(date +%s)-$$"
NET="$RUN_ID-net"
APP="$RUN_ID-app"
SITE="$RUN_ID-site"
RUNNER="$RUN_ID-runner"
APP_IMAGE="scared-travel-ai-browser-tests:$RUN_ID"

# Not "app": .app is an HSTS-preloaded TLD, so browsers would force https://app.
APP_HOST="travel-os"
APP_URL="http://$APP_HOST:8000"
SITE_HOST="www.partner-site.test"
SITE_PORT=8080
SITE_URL="http://$SITE_HOST:$SITE_PORT"
# The registered domain is the bare host: the widget API also accepts www.
# and subdomains, but a port in the domain must match the origin exactly.
SITE_DOMAIN="partner-site.test:$SITE_PORT"
WEBSITE_SOURCE="scared-main"   # seed_demo's "Scared Travel" website

ARTIFACTS="$HERE/artifacts"
SITE_DIR="$(mktemp -d)"
IMAGE_BUILT=0
START=$SECONDS

log() { printf '\n==> %s\n' "$*"; }

cleanup() {
  local status=$?
  set +e
  if docker container inspect "$APP" >/dev/null 2>&1; then
    mkdir -p "$ARTIFACTS"
    docker logs "$APP" >"$ARTIFACTS/app.log" 2>&1
  fi
  log "Cleaning up"
  docker rm -f "$RUNNER" "$SITE" "$APP" >/dev/null 2>&1
  docker network rm "$NET" >/dev/null 2>&1
  if [ "$IMAGE_BUILT" = 1 ]; then docker rmi -f "$APP_IMAGE" >/dev/null 2>&1; fi
  rm -rf "$SITE_DIR"
  log "Finished in $((SECONDS - START))s (exit $status)"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

log "Building the app image ($APP_IMAGE)"
docker build --quiet -t "$APP_IMAGE" "$ROOT" >/dev/null
IMAGE_BUILT=1

log "Creating network $NET"
docker network create "$NET" >/dev/null

log "Starting the app (migrate + collectstatic + seed_demo run in the entrypoint)"
docker run -d --name "$APP" --network "$NET" --network-alias "$APP_HOST" \
  -e DEBUG=False \
  -e SECRET_KEY="browser-tests-$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')" \
  -e ALLOWED_HOSTS="$APP_HOST,localhost,127.0.0.1" \
  -e CSRF_TRUSTED_ORIGINS="$APP_URL" \
  -e SITE_URL="$APP_URL" \
  -e NUM_PROXIES=0 \
  -e DATABASE_URL="sqlite:///data/db.sqlite3" \
  -e SEED_DEMO=true \
  -e AI_ENABLED=false \
  -e THROTTLE_WIDGET_CHAT=100000/minute \
  -e THROTTLE_WIDGET_POLL=100000/minute \
  -e THROTTLE_WIDGET_BOOK=100000/minute \
  -e THROTTLE_PUBLIC_PAY=100000/minute \
  -e THROTTLE_PUBLIC_INTAKE=100000/minute \
  -e BACKUP_ENABLED=False \
  "$APP_IMAGE" \
  python manage.py runserver 0.0.0.0:8000 --insecure --noreload >/dev/null

log "Waiting for the app to become healthy"
for _ in $(seq 1 90); do
  if docker exec "$APP" curl -fsS http://127.0.0.1:8000/api/v1/health/ >/dev/null 2>&1; then
    break
  fi
  if [ "$(docker inspect -f '{{.State.Running}}' "$APP")" != "true" ]; then
    docker logs "$APP" | tail -50
    echo "The app container exited." >&2
    exit 1
  fi
  sleep 1
done
docker exec "$APP" curl -fsS http://127.0.0.1:8000/api/v1/health/ >/dev/null

log "Pointing the seeded website at $SITE_DOMAIN and reading its widget key"
KEY_OUTPUT="$(docker exec "$APP" python manage.py shell -c "
from websites.models import Website
site = Website.objects.get(source_identifier='$WEBSITE_SOURCE')
site.domain = '$SITE_DOMAIN'
site.widget_enabled = True
site.save(update_fields=['domain', 'widget_enabled', 'updated_at'])
key = site.api_keys.filter(is_active=True).order_by('-created_at').first()
print('WIDGET_KEY=' + key.public_key)
print('WIDGET_TITLE=' + (site.brand_name or site.name))
print('WIDGET_COLOR=' + (site.primary_color or '#0F766E'))
")"
WIDGET_KEY="$(printf '%s\n' "$KEY_OUTPUT" | sed -n 's/^WIDGET_KEY=//p')"
WIDGET_TITLE="$(printf '%s\n' "$KEY_OUTPUT" | sed -n 's/^WIDGET_TITLE=//p')"
WIDGET_COLOR="$(printf '%s\n' "$KEY_OUTPUT" | sed -n 's/^WIDGET_COLOR=//p')"
if [ -z "$WIDGET_KEY" ]; then
  echo "Could not read the widget key:" >&2
  printf '%s\n' "$KEY_OUTPUT" >&2
  exit 1
fi
echo "key $WIDGET_KEY for \"$WIDGET_TITLE\""

log "Starting the partner site at $SITE_URL"
cat >"$SITE_DIR/index.html" <<HTML
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Partner Site — browser tests</title>
  <style>
    body { margin: 0; font-family: Georgia, serif; background: #fffaf0; color: #222; }
    header { background: #7c2d12; color: #fff; padding: 1rem 1.25rem; }
    main { max-width: 720px; margin: 0 auto; padding: 1.25rem; }
  </style>
  <script src="$APP_URL/static/js/widget.js"
          data-scared-key="$WIDGET_KEY"
          data-scared-api="$APP_URL"
          data-scared-color="$WIDGET_COLOR"
          data-scared-title="$WIDGET_TITLE" defer></script>
</head>
<body>
  <header><strong>Partner Site</strong> · a separate origin embedding the chat widget</header>
  <main>
    <h1>Plan your next holiday</h1>
    <p>This page is served from $SITE_URL; the widget talks to $APP_URL.</p>
  </main>
</body>
</html>
HTML
chmod 755 "$SITE_DIR"
chmod 644 "$SITE_DIR/index.html"
docker run -d --name "$SITE" --network "$NET" --network-alias "$SITE_HOST" \
  -v "$SITE_DIR:/site:ro" --entrypoint python "$APP_IMAGE" \
  -m http.server "$SITE_PORT" --bind 0.0.0.0 --directory /site >/dev/null

BROWSER_ARGS=()
for browser in $BROWSERS; do BROWSER_ARGS+=(--browser "$browser"); done

log "Running the suite in $PW_IMAGE ($BROWSERS)"
set +e
docker run --name "$RUNNER" --network "$NET" --ipc=host --init \
  -e APP_URL="$APP_URL" -e SITE_URL="$SITE_URL" \
  -e WIDGET_KEY="$WIDGET_KEY" -e WIDGET_TITLE="$WIDGET_TITLE" \
  -e PYTHONDONTWRITEBYTECODE=1 -e PIP_DISABLE_PIP_VERSION_CHECK=1 -e PIP_ROOT_USER_ACTION=ignore \
  -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
  -v "$HERE:/work" -w /work \
  "$PW_IMAGE" \
  bash -c '
    rm -rf artifacts/*.png artifacts/junit.xml
    mkdir -p artifacts
    # Wait for the partner site before the first test.
    for _ in $(seq 1 30); do python - <<"PY" && break
import os, sys, urllib.request
try:
    urllib.request.urlopen(os.environ["SITE_URL"] + "/", timeout=2)
except Exception:
    sys.exit(1)
PY
      sleep 1
    done
    pip install --quiet -r requirements.txt || exit 2
    pytest "$@" --junitxml=artifacts/junit.xml
    status=$?
    chown -R "$HOST_UID:$HOST_GID" artifacts
    exit $status
  ' runner "${BROWSER_ARGS[@]}" "$@"
STATUS=$?
set -e
exit "$STATUS"
