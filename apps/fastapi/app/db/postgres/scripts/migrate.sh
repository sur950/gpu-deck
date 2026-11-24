#!/bin/bash

# ==========================================
# Database Migration Script using Alembic
# Applies every pending NNN_slug/up.sql in order (alembic upgrade head).
# ==========================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${GREEN}=========================================${NC}"
echo -e "${GREEN}Database Migration with Alembic${NC}"
echo -e "${GREEN}=========================================${NC}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
cd "$PROJECT_ROOT"

if [ ! -f "$PROJECT_ROOT/.env" ]; then
    echo -e "${RED}Error: .env file not found at $PROJECT_ROOT/.env${NC}"
    echo -e "${YELLOW}Run ../../infra/scripts/bootstrap.dev.sh first (it starts the Postgres/Redis containers and writes .env)${NC}"
    exit 1
fi

if [ ! -f "$PROJECT_ROOT/venv/bin/alembic" ]; then
    echo -e "${RED}Error: Alembic not found in virtual environment${NC}"
    echo -e "${YELLOW}Create it: uv venv --python 3.12 venv && uv pip install --python venv/bin/python -r requirements.txt${NC}"
    exit 1
fi

echo -e "${BLUE}Current migration state:${NC}"
venv/bin/alembic current

echo -e "${YELLOW}Running migrations...${NC}"
venv/bin/alembic upgrade head

echo -e "${GREEN}=========================================${NC}"
echo -e "${GREEN}✓ Migrations applied successfully!${NC}"
echo -e "${GREEN}=========================================${NC}"
echo -e "${BLUE}New migration state:${NC}"
venv/bin/alembic current
