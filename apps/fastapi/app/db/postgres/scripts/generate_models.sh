#!/bin/bash

# ==========================================
# Generate SQLAlchemy Models (sqlacodegen) -> app/db/postgres/models/generated_models.py
#
# generated_models.py is COMMITTED (the apps import it at runtime; images and
# fresh clones must not depend on a local database). It is always generated
# from a throwaway scratch SCHEMA (in the configured database) migrated from
# zero with `alembic upgrade head`, so the output depends only on the
# migrations — never on the state of the dev schema (no daily *_pYYYYMMDD
# partitions, no half-applied branches, no tables of unmerged work).
#
#   ./app/db/postgres/scripts/generate_models.sh           # regenerate in place
#   ./app/db/postgres/scripts/generate_models.sh --check   # CI: exit 1 when the committed
#                                                          # file differs from the migrations
#
# Needs: the database from .env / DATABASE_URL and the CREATE privilege on it
# (the scratch schema is created and dropped here; extensions already in
# `public` are reused).
# ==========================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

MODE="write"
if [ "${1:-}" = "--check" ]; then
    MODE="check"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
MODELS_DIR="$(dirname "$SCRIPT_DIR")/models"
TARGET="$MODELS_DIR/generated_models.py"
cd "$PROJECT_ROOT"

PY="venv/bin/python"
[ -x "$PY" ] || PY="python"
BIN="$(dirname "$(command -v "$PY" 2>/dev/null || echo venv/bin/python)")"
[ -x "venv/bin/python" ] && BIN="venv/bin"

if [ ! -f .env ] && [ -z "${DATABASE_URL:-}" ] && [ -z "${POSTGRES_HOST:-}" ]; then
    echo -e "${RED}Error: no .env and no DATABASE_URL / POSTGRES_* in the environment${NC}"
    exit 1
fi

# Server URL exactly as the app sees it (DATABASE_URL wins over POSTGRES_*).
BASE_URL=$("$PY" -c "from app.core.settings import settings; print(settings.get_sync_database_url())" 2>/dev/null | tail -1)
SCRATCH="gpu_deck_models_$$_$RANDOM"
# search_path = scratch, public: CREATE TABLE/TYPE/FUNCTION land in the scratch
# schema, CREATE EXTENSION IF NOT EXISTS finds the ones already in public, and
# sqlacodegen reflects current_schema() (= scratch) with no schema qualifier.
SCRATCH_URL=$("$PY" -c "import sys; from sqlalchemy.engine import make_url; u = make_url(sys.argv[1]); print(u.update_query_dict({'options': f'-csearch_path={sys.argv[2]},public'}).render_as_string(hide_password=False))" "$BASE_URL" "$SCRATCH")
OUT_DIR="$(mktemp -d)"

db_sql() {
    # One statement on the configured database (autocommit).
    "$PY" - "$BASE_URL" "$1" <<'PYEOF'
import sys
import psycopg
from sqlalchemy.engine import make_url

url = make_url(sys.argv[1])
with psycopg.connect(
    host=url.host, port=url.port or 5432, user=url.username, password=url.password,
    dbname=url.database, autocommit=True,
) as conn:
    conn.execute(sys.argv[2])
PYEOF
}

cleanup() {
    db_sql "DROP SCHEMA IF EXISTS \"$SCRATCH\" CASCADE" >/dev/null 2>&1 || true
    rm -rf "$OUT_DIR"
}
trap cleanup EXIT

echo -e "${YELLOW}Creating scratch schema $SCRATCH and applying all migrations...${NC}"
db_sql "CREATE SCHEMA \"$SCRATCH\""
DATABASE_URL="$SCRATCH_URL" ALEMBIC_VERSION_TABLE_SCHEMA="$SCRATCH" "$BIN/alembic" upgrade head 2>&1 | grep -v "^INFO\|Logging configured" || true

# Every ordinary/partitioned table except partition children (the per-day *_pYYYYMMDD partitions never become ORM classes).
TABLES=$("$PY" - "$BASE_URL" "$SCRATCH" <<'PYEOF'
import sys
import psycopg
from sqlalchemy.engine import make_url

url = make_url(sys.argv[1])
with psycopg.connect(
    host=url.host, port=url.port or 5432, user=url.username, password=url.password,
    dbname=url.database,
) as conn:
    rows = conn.execute(
        """
        SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relkind IN ('r', 'p') AND NOT c.relispartition
          AND c.relname <> 'alembic_version'
        ORDER BY c.relname
        """,
        (sys.argv[2],),
    ).fetchall()
print(",".join(r[0] for r in rows))
PYEOF
)

echo -e "${YELLOW}Generating models ($(echo "$TABLES" | tr ',' '\n' | wc -l | tr -d ' ') tables)...${NC}"
# nonativeenums: sqlacodegen>=4 otherwise turns Postgres ENUM columns into
# Python enum classes, and str() of those yields "Status.ACTIVE" rather than
# "active" — the code compares and formats these columns as plain strings.
"$BIN/sqlacodegen" "$SCRATCH_URL" \
    --outfile "$OUT_DIR/generated_models.py" \
    --generator declarative \
    --options nonativeenums \
    --tables "$TABLES"
"$BIN/black" "$OUT_DIR/generated_models.py" -l 100 -q
"$BIN/isort" --profile black -l 100 -q "$OUT_DIR/generated_models.py"

if [ "$MODE" = "check" ]; then
    if ! diff -q "$OUT_DIR/generated_models.py" "$TARGET" >/dev/null 2>&1; then
        diff -u "$TARGET" "$OUT_DIR/generated_models.py" | head -40 || true
        echo -e "${RED}✗ generated_models.py is stale — run app/db/postgres/scripts/generate_models.sh and commit it${NC}"
        exit 1
    fi
    echo -e "${GREEN}✓ generated_models.py matches the migrations${NC}"
    exit 0
fi

mkdir -p "$MODELS_DIR"
cp "$OUT_DIR/generated_models.py" "$TARGET"
echo -e "${GREEN}✓ Models generated: $TARGET${NC}"
