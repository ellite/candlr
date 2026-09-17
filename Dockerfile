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

# ── Stage 2: Runtime (Python + Node + supervisord) ────────────────────────────
FROM python:3.12-slim

ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}

RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    curl \
    gosu \
    supervisor \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# ── Backend ───────────────────────────────────────────────────────────────────
WORKDIR /app/backend

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

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
# This runs in the target-platform stage so native optional packages match the
# image architecture (not the BUILDPLATFORM used by the fast Astro build).
RUN npm ci --omit=dev \
    && npm cache clean --force

# ── Entrypoint & supervisor config ────────────────────────────────────────────
COPY entrypoint.sh /entrypoint.sh
COPY supervisord.conf /etc/supervisor/conf.d/candlr.conf
RUN chmod +x /entrypoint.sh

VOLUME ["/app/backend/data"]

EXPOSE 4258

# Exercise the public frontend-to-backend path and ensure the reminder worker
# is alive as well. Keep the syntax compatible with pre-25 Docker engines.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:4258/api/health >/dev/null \
      && supervisorctl --serverurl unix:///tmp/supervisor.sock status reminders | grep -q RUNNING \
      || exit 1

ENTRYPOINT ["/entrypoint.sh"]
