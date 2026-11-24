# syntax=docker/dockerfile:1.7
# GPU Deck web UI (Next.js standalone output), non-root and read-only-root friendly.
#
# Build context: repository root (the app imports libs/contracts/rules/*.schema.json)
#   docker build -f infra/docker/web/Dockerfile -t gpu-deck-web:local .
#
# One image for any hostname: the browser bundle calls the relative /api/v1, and the gateway
# (apps/web/server/gateway.mjs, the container's entry point) serves the UI and forwards /api/*
# to the admin API named at runtime by GPUDECK_API_INTERNAL_URL (e.g. http://admin-api:8180),
# including cookies, SSE and WebSocket upgrades. NEXT_PUBLIC_* branding values are still inlined
# at build time (build args). At runtime the container only needs write access to /tmp and
# /app/apps/web/.next/cache (mount tmpfs there when running with --read-only).

ARG NODE_IMAGE=node:24.21.0-alpine

# ---------- dependencies ----------
# apps/web is a member of the root pnpm workspace (one lockfile at the repo root).
FROM ${NODE_IMAGE} AS deps
WORKDIR /src
ENV PNPM_HOME=/pnpm \
    COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
    NEXT_TELEMETRY_DISABLED=1
RUN corepack enable && corepack prepare pnpm@10.28.2 --activate
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml .npmrc ./
COPY apps/web/package.json apps/web/.npmrc apps/web/
RUN --mount=type=cache,id=pnpm-store,target=/pnpm/store \
    pnpm config set store-dir /pnpm/store && \
    pnpm install --frozen-lockfile --filter web...

# ---------- build ----------
FROM ${NODE_IMAGE} AS build
WORKDIR /src/apps/web
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
    NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=production
RUN corepack enable && corepack prepare pnpm@10.28.2 --activate
# Names match apps/web/.env.example (the app reads exactly these).
# Empty = same origin (/api/v1 through the gateway). Set it only to make the browser call an
# admin API on another origin.
ARG NEXT_PUBLIC_API_URL=
ARG NEXT_PUBLIC_SESSION_COOKIE=gpudeck_session
ARG NEXT_PUBLIC_CSRF_COOKIE=gpudeck_csrf
ARG NEXT_PUBLIC_APP_NAME="GPU Deck"
ARG NEXT_PUBLIC_APP_BYLINE="GPU control plane"
ARG NEXT_PUBLIC_BRAND_COLOR="#B3122E"
ARG NEXT_PUBLIC_LOGO_URL=""
ARG NEXT_PUBLIC_FAVICON_URL="/favicon.ico"
ARG NEXT_PUBLIC_DEFAULT_COLOR_MODE=dark
ARG NEXT_PUBLIC_LOG_POLL_INTERVAL_MS=15000
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL} \
    NEXT_PUBLIC_SESSION_COOKIE=${NEXT_PUBLIC_SESSION_COOKIE} \
    NEXT_PUBLIC_CSRF_COOKIE=${NEXT_PUBLIC_CSRF_COOKIE} \
    NEXT_PUBLIC_APP_NAME=${NEXT_PUBLIC_APP_NAME} \
    NEXT_PUBLIC_APP_BYLINE=${NEXT_PUBLIC_APP_BYLINE} \
    NEXT_PUBLIC_BRAND_COLOR=${NEXT_PUBLIC_BRAND_COLOR} \
    NEXT_PUBLIC_LOGO_URL=${NEXT_PUBLIC_LOGO_URL} \
    NEXT_PUBLIC_FAVICON_URL=${NEXT_PUBLIC_FAVICON_URL} \
    NEXT_PUBLIC_DEFAULT_COLOR_MODE=${NEXT_PUBLIC_DEFAULT_COLOR_MODE} \
    NEXT_PUBLIC_LOG_POLL_INTERVAL_MS=${NEXT_PUBLIC_LOG_POLL_INTERVAL_MS} \
    NEXT_PUBLIC_API_MOCKS=0
# The workspace files make Next/Turbopack use the repository root as project root, as it does
# locally, so the ../../libs/contracts imports resolve. apps/web/node_modules holds symlinks into
# /src/node_modules/.pnpm.
COPY package.json pnpm-workspace.yaml /src/
COPY libs/contracts /src/libs/contracts
COPY --from=deps /src/node_modules /src/node_modules
COPY --from=deps /src/apps/web/node_modules ./node_modules
COPY apps/web ./
RUN pnpm build

# ---------- runtime ----------
FROM ${NODE_IMAGE} AS runtime
LABEL org.opencontainers.image.title="gpu-deck-web" \
      org.opencontainers.image.description="GPU Deck web UI" \
      org.opencontainers.image.vendor="Suresh Konakanchi" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/sur950/gpu-deck"
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3180 \
    HOSTNAME=0.0.0.0 \
    GPUDECK_WEB_INTERNAL_PORT=3189 \
    GPUDECK_API_INTERNAL_URL=
RUN addgroup -S -g 1001 web && adduser -S -u 1001 -G web -H -s /sbin/nologin web && \
    rm -rf /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/corepack \
           /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack /opt/yarn* \
           /usr/local/bin/yarn /usr/local/bin/yarnpkg
# Standalone output is rooted at the workspace root, so the server lives in apps/web.
COPY --from=build --chown=0:0 /src/apps/web/.next/standalone ./
COPY --from=build --chown=0:0 /src/apps/web/public ./apps/web/public
COPY --from=build --chown=0:0 /src/apps/web/.next/static ./apps/web/.next/static
COPY --from=build --chown=0:0 /src/apps/web/server/gateway.mjs ./apps/web/server/gateway.mjs
# Only the cache dir is writable (tmpfs in compose/Helm); the app files stay root-owned.
RUN mkdir -p /app/apps/web/.next/cache && chown 1001:1001 /app/apps/web/.next/cache
WORKDIR /app/apps/web
USER 1001:1001
EXPOSE 3180
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD wget -q --spider "http://127.0.0.1:${PORT}/login" || exit 1
CMD ["node", "server/gateway.mjs"]
