#!/usr/bin/env bash
# Throwaway Postgres 17 + Redis 7.4 for the backend checks (GitHub CI and scripts/ci_local.sh).
#
# Same containers as local development (infra/docker/docker-compose.deps.yml: the app role, the
# extensions in template1, the Redis ACL user limited to gpu-deck:* keys), but under its own
# compose project, network, volumes and ports, so it never touches the dev stack. GitHub
# `services:` containers cannot be used: they take no command, and the Redis ACL comes from the
# command line (tests/integration/test_health_endpoints.py checks that unprefixed keys fail).
#
#   scripts/ci_deps.sh up     start, wait healthy, write apps/fastapi/.env for them
#   scripts/ci_deps.sh down   stop and delete the containers, volumes and state
#
# Environment (all optional):
#   CI_DEPS_PROJECT     compose project name           (default gpu-deck-ci)
#   CI_DEPS_PG_PORT     Postgres host port, 127.0.0.1  (default 55433)
#   CI_DEPS_REDIS_PORT  Redis host port, 127.0.0.1     (default 56380)
#   CI_DEPS_DIR         state directory (env file, compose override)
#
# apps/fastapi/.env is only written when it does not exist or was written by this script, so a
# developer's real .env is never replaced.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT="${CI_DEPS_PROJECT:-gpu-deck-ci}"
PG_PORT="${CI_DEPS_PG_PORT:-55433}"
REDIS_PORT="${CI_DEPS_REDIS_PORT:-56380}"
STATE="${CI_DEPS_DIR:-${RUNNER_TEMP:-${TMPDIR:-/tmp}}/$PROJECT-deps}"
STATE="${STATE%/}"
COMPOSE_FILE="$ROOT/infra/docker/docker-compose.deps.yml"
APP_ENV_FILE="$ROOT/apps/fastapi/.env"
MARKER="# written by scripts/ci_deps.sh"

dc() {
  docker compose -p "$PROJECT" -f "$COMPOSE_FILE" -f "$STATE/override.yml" \
    --env-file "$STATE/deps.env" "$@"
}

set_env() {  # set_env FILE KEY VALUE (values never contain '|')
  if grep -qE "^$2=" "$1"; then
    sed -i.bak -E "s|^$2=.*|$2=$3|" "$1" && rm -f "$1.bak"
  else
    printf '%s=%s\n' "$2" "$3" >> "$1"
  fi
}

up() {
  docker compose version >/dev/null 2>&1 || { echo "error: docker compose is required" >&2; exit 1; }
  mkdir -p "$STATE"
  if [[ ! -f "$STATE/deps.env" ]]; then
    umask 077
    cat > "$STATE/deps.env" <<EOF
GPUDECK_DEPS_PG_PORT=$PG_PORT
GPUDECK_DEPS_PG_SUPERUSER_PASSWORD=$(openssl rand -hex 24)
GPUDECK_DEPS_PG_USER=gpu_deck
GPUDECK_DEPS_PG_PASSWORD=$(openssl rand -hex 24)
GPUDECK_DEPS_PG_DB=gpu_deck_test
GPUDECK_DEPS_REDIS_PORT=$REDIS_PORT
GPUDECK_DEPS_REDIS_USER=gpu-deck
GPUDECK_DEPS_REDIS_PASSWORD=$(openssl rand -hex 24)
EOF
    umask 022
  fi
  # Own network name (the dev file pins `gpu-deck-deps`), no restart policy.
  cat > "$STATE/override.yml" <<EOF
networks:
  default:
    name: $PROJECT-deps
services:
  postgres:
    restart: "no"
  redis:
    restart: "no"
EOF
  # shellcheck disable=SC1091
  source "$STATE/deps.env"

  echo "==> Postgres 127.0.0.1:$PG_PORT and Redis 127.0.0.1:$REDIS_PORT (compose project $PROJECT)"
  dc up -d --wait --wait-timeout 180 postgres redis
  # Idempotent; also runs from docker-entrypoint-initdb.d on the first start.
  dc exec -T postgres /docker-entrypoint-initdb.d/10-gpu-deck.sh

  if [[ -f "$APP_ENV_FILE" ]] && ! grep -qxF "$MARKER" "$APP_ENV_FILE"; then
    echo "error: $APP_ENV_FILE exists and was not written by this script; not replacing it" >&2
    exit 1
  fi
  { echo "$MARKER"; cat "$ROOT/apps/fastapi/.env.example"; } > "$APP_ENV_FILE"
  local pg_user="$GPUDECK_DEPS_PG_USER" pg_pass="$GPUDECK_DEPS_PG_PASSWORD"
  local pg_db="$GPUDECK_DEPS_PG_DB" su_pass="$GPUDECK_DEPS_PG_SUPERUSER_PASSWORD"
  local r_user="$GPUDECK_DEPS_REDIS_USER" r_pass="$GPUDECK_DEPS_REDIS_PASSWORD"
  set_env "$APP_ENV_FILE" POSTGRES_HOST 127.0.0.1
  set_env "$APP_ENV_FILE" POSTGRES_PORT "$PG_PORT"
  set_env "$APP_ENV_FILE" POSTGRES_USER "$pg_user"
  set_env "$APP_ENV_FILE" POSTGRES_PASSWORD "$pg_pass"
  set_env "$APP_ENV_FILE" POSTGRES_DB "$pg_db"
  set_env "$APP_ENV_FILE" DATABASE_URL "postgresql://$pg_user:$pg_pass@127.0.0.1:$PG_PORT/$pg_db"
  set_env "$APP_ENV_FILE" TEST_PG_ADMIN_URL "postgresql://postgres:$su_pass@127.0.0.1:$PG_PORT/postgres"
  set_env "$APP_ENV_FILE" REDIS_HOST 127.0.0.1
  set_env "$APP_ENV_FILE" REDIS_PORT "$REDIS_PORT"
  set_env "$APP_ENV_FILE" REDIS_DB 0
  set_env "$APP_ENV_FILE" REDIS_USERNAME "$r_user"
  set_env "$APP_ENV_FILE" REDIS_PASSWORD "$r_pass"
  set_env "$APP_ENV_FILE" REDIS_URL "redis://$r_user:$r_pass@127.0.0.1:$REDIS_PORT/0"
  set_env "$APP_ENV_FILE" REDIS_KEY_PREFIX "gpu-deck:"
  set_env "$APP_ENV_FILE" SECRET_KEY "$(openssl rand -hex 32)"
  set_env "$APP_ENV_FILE" VAULT_KEYS "$(openssl rand -base64 32 | tr '+/' '-_')"
  set_env "$APP_ENV_FILE" ADMIN_PASSWORD "Ci-$(openssl rand -hex 12)"
  chmod 600 "$APP_ENV_FILE"
  echo "==> wrote apps/fastapi/.env (connection values for the CI containers)"
}

down() {
  if [[ -f "$STATE/deps.env" && -f "$STATE/override.yml" ]]; then
    dc down -v --remove-orphans
  fi
  rm -rf "$STATE"
  if [[ -f "$APP_ENV_FILE" ]] && grep -qxF "$MARKER" "$APP_ENV_FILE"; then
    rm -f "$APP_ENV_FILE"
  fi
}

case "${1:-}" in
  up) up ;;
  down) down ;;
  *) echo "usage: scripts/ci_deps.sh up|down" >&2; exit 2 ;;
esac
