#!/bin/sh
# Entrypoint shim for the GPU Deck API image under Docker Compose.
#
# The dev .env points host processes at loopback addresses (127.0.0.1 / localhost). Inside a
# container that is the container itself, so rewrite any such host that is still set (compose
# already points Postgres/Redis at the deps containers) to the Docker host alias (OrbStack and Docker Desktop provide host.docker.internal; on Linux compose maps it to
# host-gateway). Only connection settings are touched; then exec the real command.
set -eu

alias_host="${GPUDECK_HOST_ALIAS:-host.docker.internal}"

# Compose's env_file parser keeps an inline comment as the value when the value itself is empty
# (`SESSION_COOKIE_SECURE=   # empty = ...` becomes "# empty = ..."). Treat any value that starts
# with "# " as empty, which is what the shell and python-dotenv do with such a .env line.
for var in $(env | sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=# .*/\1/p'); do
  export "$var="
done

rewrite_url() {
  printf '%s' "$1" | sed -E \
    -e "s#@(127\.0\.0\.1|localhost|\[::1\])([:/]|\$)#@${alias_host}\2#" \
    -e "s#://(127\.0\.0\.1|localhost|\[::1\])([:/]|\$)#://${alias_host}\2#"
}

for var in DATABASE_URL REDIS_URL CELERY_BROKER_URL CELERY_RESULT_BACKEND OTEL_EXPORTER_OTLP_ENDPOINT; do
  eval "val=\${$var:-}"
  if [ -n "$val" ]; then
    export "$var=$(rewrite_url "$val")"
  fi
done

for var in POSTGRES_HOST REDIS_HOST; do
  eval "val=\${$var:-}"
  case "$val" in
    127.0.0.1 | localhost | ::1) export "$var=$alias_host" ;;
  esac
done

exec "$@"
