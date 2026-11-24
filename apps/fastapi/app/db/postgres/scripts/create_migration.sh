#!/bin/bash

# ==========================================
# Create SQL-First Migration
# Creates NNN_slug/{up.sql,down.sql,migration.py} and updates alembic.ini
#
# Usage: ./create_migration.sh "description" [NNN]
#   NNN is optional; use the next free number (see docs/components/fastapi-migrations.md).
# ==========================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
ALEMBIC_MIGRATIONS_DIR="$PROJECT_ROOT/app/db/postgres/alembic/migrations"

cd "$PROJECT_ROOT"

if [ -z "$1" ]; then
    echo -e "${RED}Error: Migration description required${NC}"
    echo -e "${YELLOW}Usage: ./create_migration.sh \"description\" [NNN]${NC}"
    echo -e "${YELLOW}Example: ./create_migration.sh \"add node labels\" 203${NC}"
    exit 1
fi

DESCRIPTION=$1
SNAKE_CASE=$(echo "$DESCRIPTION" | tr '[:upper:]' '[:lower:]' | tr ' ' '_' | tr '-' '_')

# Previous migration = highest numbered folder (global chain)
PREV_MIGRATION=$(find "$ALEMBIC_MIGRATIONS_DIR" -mindepth 1 -maxdepth 1 -type d ! -name "__pycache__" | sort | tail -1)
if [ -z "$PREV_MIGRATION" ]; then
    DOWN_REVISION="None"
    LAST_NUM=0
else
    PREV_NAME=$(basename "$PREV_MIGRATION")
    DOWN_REVISION="'$PREV_NAME'"
    LAST_NUM=$(echo "$PREV_NAME" | cut -d'_' -f1)
fi

if [ -n "$2" ]; then
    NEXT_NUM=$(printf "%03d" $((10#$2)))
else
    NEXT_NUM=$(printf "%03d" $((10#$LAST_NUM + 1)))
fi

if [ "$((10#$NEXT_NUM))" -le "$((10#$LAST_NUM))" ]; then
    echo -e "${RED}Error: ${NEXT_NUM} is not greater than the last migration (${LAST_NUM})${NC}"
    exit 1
fi

MIGRATION_ID="${NEXT_NUM}_${SNAKE_CASE}"
MIGRATION_FOLDER="$ALEMBIC_MIGRATIONS_DIR/$MIGRATION_ID"
UP_SQL_FILE="$MIGRATION_FOLDER/up.sql"
DOWN_SQL_FILE="$MIGRATION_FOLDER/down.sql"
PY_FILE="$MIGRATION_FOLDER/migration.py"

TIMESTAMP=$(date -u +"%Y-%m-%d %H:%M:%S.000000")
DESCRIPTION_CAPITALIZED="$(echo "${DESCRIPTION:0:1}" | tr '[:lower:]' '[:upper:]')${DESCRIPTION:1}"

mkdir -p "$MIGRATION_FOLDER"
echo -e "${GREEN}✓ Created migration folder: ${MIGRATION_ID}/${NC}"

cat > "$UP_SQL_FILE" << EOF
-- ==========================================
-- ${DESCRIPTION_CAPITALIZED}
-- Version: ${NEXT_NUM}
-- Description: TODO: Add detailed description
-- ==========================================

-- TODO: Add your SQL here (idempotent where possible, COMMENT ON new objects)
-- Avoid VACUUM statements (they are filtered automatically).

EOF
echo -e "${GREEN}✓ Created up.sql${NC}"

cat > "$DOWN_SQL_FILE" << EOF
-- ==========================================
-- Rollback for ${MIGRATION_ID}
-- ${DESCRIPTION_CAPITALIZED}
-- ==========================================

-- TODO: Reverse the changes made in up.sql, in the opposite order.

EOF
echo -e "${GREEN}✓ Created down.sql${NC}"

cat > "$PY_FILE" << EOF
"""${DESCRIPTION}

Revision ID: ${MIGRATION_ID}
Revises: ${DOWN_REVISION}
Create Date: ${TIMESTAMP}

"""

from typing import Sequence, Union

from alembic import op

revision: str = "${MIGRATION_ID}"
down_revision: Union[str, None] = ${DOWN_REVISION}
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _run_sql_file(name: str, strip_vacuum: bool = False) -> None:
    from pathlib import Path

    sql_file = Path(__file__).parent / name
    with open(sql_file, "r", encoding="utf-8") as f:
        sql_content = f.read()

    if strip_vacuum:
        # VACUUM cannot run inside a transaction block.
        lines = sql_content.split("\\n")
        sql_content = "\\n".join(line for line in lines if not line.strip().startswith("VACUUM"))

    if not sql_content.strip():
        return

    # Execute on the raw DBAPI cursor to bypass SQLAlchemy's bind-parameter
    # parser, which misreads PostgreSQL 'value'::type casts.
    bind = op.get_bind()
    bind.connection.cursor().execute(sql_content)


def upgrade() -> None:
    """Apply the migration from up.sql"""
    _run_sql_file("up.sql", strip_vacuum=True)


def downgrade() -> None:
    """Reverse the migration using down.sql"""
    _run_sql_file("down.sql")
EOF
echo -e "${GREEN}✓ Created migration.py${NC}"

# Update alembic.ini with all migration subdirectories
ALEMBIC_INI="$PROJECT_ROOT/alembic.ini"
MIGRATION_SUBDIRS=$(find "$ALEMBIC_MIGRATIONS_DIR" -mindepth 1 -maxdepth 1 -type d ! -name "__pycache__" -exec basename {} \; | sort | sed 's|^|app/db/postgres/alembic/migrations/|' | tr '\n' ' ' | sed 's/ $//')

if [ -f "$ALEMBIC_INI" ]; then
    sed -i.bak -E "s|^version_locations = .*|version_locations = $MIGRATION_SUBDIRS|" "$ALEMBIC_INI"
    rm -f "$ALEMBIC_INI.bak"
    echo -e "${GREEN}✓ Updated alembic.ini version_locations${NC}"
fi

echo -e ""
echo -e "${BLUE}Next steps:${NC}"
echo -e "  1. Edit ${YELLOW}$UP_SQL_FILE${NC} and ${YELLOW}$DOWN_SQL_FILE${NC}"
echo -e "  2. Apply:      ${YELLOW}./app/db/postgres/scripts/migrate.sh${NC}"
echo -e "  3. Regenerate: ${YELLOW}./app/db/postgres/scripts/generate_models.sh${NC}"
echo -e "  4. Test:       ${YELLOW}venv/bin/alembic downgrade -1 && venv/bin/alembic upgrade head${NC}"
