#!/bin/sh
set -e

echo "==> Applying migrations"
python manage.py migrate --noinput

echo "==> Collecting static files"
python manage.py collectstatic --noinput --clear

# Optional one-shot demo data: set SEED_DEMO=true on first boot only.
if [ "${SEED_DEMO:-false}" = "true" ]; then
    echo "==> Seeding demo data"
    python manage.py seed_demo
fi

echo "==> Starting: $*"
exec "$@"
