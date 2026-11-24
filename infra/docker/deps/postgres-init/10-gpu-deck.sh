#!/bin/sh
# Create (or update) the GPU Deck app role and database in the dev Postgres container.
#
# Runs once from /docker-entrypoint-initdb.d on an empty volume, and again on every
# `bootstrap.dev.sh deps up` (docker compose exec), so it must stay idempotent. It also
# re-applies the password from infra/docker/.env, so changing GPUDECK_DEPS_PG_PASSWORD there
# and running `deps up` is enough.
#
# Env (from docker-compose.deps.yml): APP_DB_USER, APP_DB_PASSWORD, APP_DB_NAME.
set -eu
export PGOPTIONS="-c client_min_messages=warning"

: "${APP_DB_USER:?}" "${APP_DB_PASSWORD:?}" "${APP_DB_NAME:?}"

psql -v ON_ERROR_STOP=1 -U postgres -d postgres -q \
  -v app_user="$APP_DB_USER" -v app_password="$APP_DB_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN', :'app_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user') \gexec
SELECT format('ALTER ROLE %I WITH LOGIN PASSWORD %L', :'app_user', :'app_password') \gexec
SQL

psql -v ON_ERROR_STOP=1 -U postgres -d postgres -q \
  -v app_user="$APP_DB_USER" -v app_db="$APP_DB_NAME" <<'SQL'
SELECT format('CREATE DATABASE %I OWNER %I', :'app_db', :'app_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'app_db') \gexec
SQL

# Extensions the migrations use, in the app DB and in template1 (scratch DBs created by
# integration tests inherit them).
for db in "$APP_DB_NAME" template1; do
  psql -v ON_ERROR_STOP=1 -U postgres -d "$db" -q <<'SQL'
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
SQL
done

psql -v ON_ERROR_STOP=1 -U postgres -d "$APP_DB_NAME" -q -v app_user="$APP_DB_USER" <<'SQL'
SELECT format('ALTER SCHEMA public OWNER TO %I', :'app_user') \gexec
SQL

echo "gpu-deck: role ${APP_DB_USER} and database ${APP_DB_NAME} ready"
