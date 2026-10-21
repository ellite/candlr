#!/bin/sh
set -e

PUID=${PUID:-1000}
PGID=${PGID:-1000}
BACKEND_PORT=${BACKEND_PORT:-8000}
export BACKEND_PORT

chown -R "$PUID:$PGID" /app/backend/data

chmod o+w /dev/stdout /dev/stderr

echo "Running database migrations..."
cd /app/backend
su-exec "$PUID:$PGID" alembic upgrade head

echo "Starting Candlr (frontend :4258, backend 127.0.0.1:${BACKEND_PORT})..."
exec su-exec "$PUID:$PGID" supervisord -n -c /etc/supervisor/conf.d/candlr.conf
