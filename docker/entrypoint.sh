#!/bin/sh
set -e

# Boot tasks (migrate, collectstatic, demo seed) belong to the web container
# alone. Every other container built from this image (the scheduler) sets
# SKIP_BOOT_TASKS=1, so two processes never migrate the SQLite file at once.
if [ "${SKIP_BOOT_TASKS:-0}" = "1" ] || [ "${SKIP_BOOT_TASKS:-}" = "true" ]; then
    echo "==> SKIP_BOOT_TASKS set: not migrating or collecting static files"
else
    echo "==> Applying migrations"
    python manage.py migrate --noinput

    echo "==> Collecting static files"
    python manage.py collectstatic --noinput --clear

    # Optional one-shot demo data: set SEED_DEMO=true on first boot only.
    if [ "${SEED_DEMO:-false}" = "true" ]; then
        echo "==> Seeding demo data"
        python manage.py seed_demo
    fi
fi

echo "==> Starting: $*"
exec "$@"
