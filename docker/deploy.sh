#!/usr/bin/env bash
# Pull the latest commit and roll the stack onto it.
#
# Runs on the VPS. Invoked by .github/workflows/deploy.yml over SSH on every
# push to master, and safe to run by hand:  ./docker/deploy.sh
#
# If the new build fails its health check the previous commit is restored and
# rebuilt, so a bad push takes the site down for the length of one build rather
# than until someone notices.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/scared-travel-ai}"
BRANCH="${DEPLOY_BRANCH:-master}"
HEALTH_RETRIES="${HEALTH_RETRIES:-30}"
HEALTH_DELAY="${HEALTH_DELAY:-5}"

cd "$APP_DIR"

log() { printf '\n==> %s\n' "$*"; }

# Bring the stack up and wait for the web container to report healthy.
# Returns non-zero if it never does.
roll() {
    docker compose build web
    docker compose up -d --remove-orphans

    local cid state health i crashes=0
    for ((i = 1; i <= HEALTH_RETRIES; i++)); do
        cid="$(docker compose ps -q web)"
        if [ -n "$cid" ]; then
            read -r state health <<<"$(docker inspect \
                -f '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
                "$cid" 2>/dev/null || echo "unknown none")"

            case "$health" in
                healthy) return 0 ;;
                unhealthy)
                    echo "!! web container reported unhealthy"
                    return 1
                    ;;
            esac

            # A container that dies on boot (bad migration, broken settings)
            # never reports a health status at all — `restart: unless-stopped`
            # just cycles it. Catch that instead of waiting out the timeout.
            if [ "$state" = "exited" ] || [ "$state" = "restarting" ]; then
                crashes=$((crashes + 1))
                if [ "$crashes" -ge 3 ]; then
                    echo "!! web container keeps exiting on boot (state=$state)"
                    return 1
                fi
            fi
        fi
        sleep "$HEALTH_DELAY"
    done

    echo "!! web container never became healthy after $((HEALTH_RETRIES * HEALTH_DELAY))s"
    return 1
}

PREVIOUS="$(git rev-parse HEAD)"
log "Current commit: $PREVIOUS"

log "Fetching origin/$BRANCH"
git fetch --prune origin "$BRANCH"
TARGET="$(git rev-parse "origin/$BRANCH")"

if [ "$PREVIOUS" = "$TARGET" ]; then
    log "Already on $TARGET — rebuilding anyway to pick up any image changes"
fi

# Tracked files only; .env, db.sqlite3 and media/ are gitignored and untouched.
git reset --hard "$TARGET"
log "Deploying $TARGET"
git --no-pager log -1 --pretty='  %h  %s  (%an)'

if roll; then
    log "Healthy. Deployed $(git rev-parse --short HEAD)"
    docker image prune -f >/dev/null 2>&1 || true
    exit 0
fi

log "Health check failed — rolling back to $PREVIOUS"
git reset --hard "$PREVIOUS"

if roll; then
    log "Rolled back to $(git rev-parse --short HEAD). The pushed commit was NOT deployed."
else
    log "Rollback also failed. The stack needs manual attention."
fi

docker compose logs --tail 60 web || true
exit 1
