# syntax=docker/dockerfile:1.7
# GPU Deck control-plane image (admin API, serving API, agent hub, Celery worker/beat, scheduler).
#
# Build context: repository root. The backend reads the shared contracts at runtime
# (libs/contracts/agent-protocol/*.json for hub frame validation, libs/contracts/rules/*.schema.json
# for rule params), resolved relative to the source tree, so the image keeps the repository layout:
#
#   /srv/gpu-deck/apps/fastapi   application (WORKDIR, PYTHONPATH)
#   /srv/gpu-deck/libs/contracts contracts (read-only)
#   /app/logs, /app/certs        writable at runtime (tmpfs in compose/Helm; LOG_FILE_PATH, PKI_CA_DIR)
#
#   docker build -f infra/docker/api/Dockerfile -t gpu-deck-api:local .
#
# Non-root (uid 1000), read-only-root friendly. Pick the process with the command (see
# infra/docker/docker-compose.yml); the default runs the admin API on 8180.

# Debian 13 (trixie): its postgresql-client is 17, the server major the stack runs.
ARG PYTHON_IMAGE=python:3.12.16-slim-trixie

# ---------- wheels ----------
FROM ${PYTHON_IMAGE} AS builder
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends gcc python3-dev libpq-dev \
    && rm -rf /var/lib/apt/lists/*
COPY apps/fastapi/requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip wheel --no-cache-dir --wheel-dir /build/wheels -r requirements.txt

# ---------- runtime ----------
FROM ${PYTHON_IMAGE} AS runtime
LABEL org.opencontainers.image.title="gpu-deck-api" \
      org.opencontainers.image.description="GPU Deck control plane (admin API, serving API, hub, jobs)" \
      org.opencontainers.image.vendor="Suresh Konakanchi" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/sur950/gpu-deck"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/srv/gpu-deck/apps/fastapi \
    APP_MODULE=app.main:app \
    APP_PORT=8180 \
    WORKERS=4 \
    LOG_FILE_PATH=/app/logs/app.log \
    PKI_CA_DIR=/app/certs \
    TMPDIR=/tmp

# `upgrade` takes the Debian security fixes published since the base image was built.
RUN apt-get update && apt-get -y upgrade \
    && apt-get install -y --no-install-recommends postgresql-client libpq5 curl procps \
    && rm -rf /var/lib/apt/lists/*

# Install from the wheelhouse only, through requirements.txt so extras such as
# uvicorn[standard] (websockets for the agent hub) are installed too.
RUN --mount=type=bind,from=builder,source=/build,target=/build \
    pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --no-index --find-links /build/wheels -r /build/requirements.txt

RUN useradd -m -u 1000 appuser && mkdir -p /app/logs /app/certs /app/agent-cache && \
    chown appuser:appuser /app/logs /app/certs /app/agent-cache && chmod 0700 /app/certs

# Code and contracts stay root-owned (read-only for the app user).
COPY libs/contracts /srv/gpu-deck/libs/contracts
COPY apps/fastapi /srv/gpu-deck/apps/fastapi
WORKDIR /srv/gpu-deck/apps/fastapi

USER appuser
EXPOSE 8180 8181 8443

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${APP_PORT}/health/live" || curl -fsS "http://127.0.0.1:${APP_PORT}/api/v1/health/live" || exit 1

CMD ["sh", "-c", "exec gunicorn ${APP_MODULE} --workers ${WORKERS} --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:${APP_PORT} --access-logfile - --error-logfile - --log-level info"]
