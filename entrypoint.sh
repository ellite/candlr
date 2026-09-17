#!/bin/sh
set -e

PUID=${PUID:-1000}
PGID=${PGID:-1000}
BACKEND_PORT=${BACKEND_PORT:-8000}
export BACKEND_PORT

# --non-unique also supports common NAS setups where the requested host IDs
# already exist in the base image under another name.
if ! getent group candlr >/dev/null 2>&1; then
    groupadd --non-unique --gid "$PGID" candlr
fi
if ! id candlr >/dev/null 2>&1; then
    useradd --non-unique --uid "$PUID" --gid candlr --no-create-home --shell /bin/false candlr
fi

chown -R "$PUID:$PGID" /app/backend/data

chmod o+w /dev/stdout /dev/stderr

echo "Running database migrations..."
cd /app/backend
gosu candlr alembic upgrade head

echo "Starting Candlr (frontend :4258, backend 127.0.0.1:${BACKEND_PORT})..."
exec gosu candlr /usr/bin/supervisord -n -c /etc/supervisor/supervisord.conf
