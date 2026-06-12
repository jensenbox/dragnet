#!/bin/sh
set -e

python manage.py migrate --noinput

# Bootstrap the admin account on first run; harmless no-op error once it exists.
if [ -n "${DJANGO_SUPERUSER_USERNAME:-}" ]; then
    python manage.py createsuperuser --noinput 2>/dev/null || true
fi

exec gunicorn dragnet.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers "${GUNICORN_WORKERS:-3}" \
    --access-logfile -
