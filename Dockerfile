# ── Stage 1: Build frontend ───────────────────────────────────────────────────
FROM --platform=$BUILDPLATFORM node:22-alpine AS frontend-builder
WORKDIR /app/frontend

COPY frontend/package*.json ./
RUN npm ci

# Copy only inputs required by the frontend build. In particular, never copy
# a local frontend/.env.* file into a build where Vite could embed it.
COPY frontend/astro.config.mjs frontend/tsconfig.json ./
COPY frontend/public ./public
COPY frontend/src ./src
RUN npm run build

# ── Stage 2: Production frontend dependencies ─────────────────────────────────
# Runs on the target platform (no --platform flag) so native optional packages
# match the image architecture and libc, unlike the BUILDPLATFORM stage above.
# This is also where the `node` binary for the runtime image comes from.
FROM node:22-slim AS frontend-deps
WORKDIR /app/frontend

COPY frontend/package*.json ./
RUN npm ci --omit=dev \
    && npm cache clean --force

# ── Stage 3: Runtime (Python + Node + supervisord) ────────────────────────────
FROM python:3.12-slim

ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}

# Node is copied from the official image as a single binary instead of being
# installed from NodeSource. The NodeSource package depends on Debian's own
# Python 3.13, and installing it needs curl and gnupg; none of that is used at
# runtime, yet every package left in the image shows up in vulnerability scans.
# npm and corepack are not needed to run the server and are left out.
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    gosu \
    && rm -rf /var/lib/apt/lists/*
COPY --from=frontend-deps /usr/local/bin/node /usr/local/bin/node

# ── Backend ───────────────────────────────────────────────────────────────────
WORKDIR /app/backend

# supervisor comes from pip, not apt: the Debian package drags in Debian's own
# Python 3.13 and libexpat, which would run nothing but supervisord.
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt supervisor==4.3.0

# Keep the runtime image limited to application and migration files. Tests,
# local databases, and any unrelated files under backend/ are not included.
COPY backend/alembic.ini ./
COPY backend/alembic ./alembic
COPY backend/app ./app
COPY backend/scripts ./scripts

# ── Frontend ──────────────────────────────────────────────────────────────────
WORKDIR /app/frontend

COPY --from=frontend-builder /app/frontend/dist ./dist
COPY frontend/package*.json ./
COPY --from=frontend-deps /app/frontend/node_modules ./node_modules

# ── Entrypoint & supervisor config ────────────────────────────────────────────
COPY entrypoint.sh /entrypoint.sh
COPY supervisord.conf /etc/supervisor/conf.d/candlr.conf
RUN chmod +x /entrypoint.sh

VOLUME ["/app/backend/data"]

EXPOSE 4258

# Exercise the public frontend-to-backend path and ensure the reminder worker
# is alive as well. Keep the syntax compatible with pre-25 Docker engines.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4258/api/health', timeout=4)" \
      && supervisorctl --serverurl unix:///tmp/supervisor.sock status reminders | grep -q RUNNING \
      || exit 1

ENTRYPOINT ["/entrypoint.sh"]
