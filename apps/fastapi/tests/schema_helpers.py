"""
Test infra: build a throwaway Postgres schema from the SQL migrations.

Integration tests that mutate data run in their own schema (``CREATE SCHEMA``
in the dev database; the app role has no CREATEDB). Tests that need a whole
scratch database use ``pg_admin_url()`` (the dev Postgres container's
superuser). Early suites applied ``001_init/up.sql`` only;
``apply_migrations`` applies EVERY migration in alembic chain order
(``revision`` / ``down_revision`` of each ``migrations/NNN_slug/migration.py``),
so tables and columns added by later migrations exist too.

    from tests.schema_helpers import create_scratch_schema, drop_scratch_schema

    conn = await asyncpg.connect(dsn)
    await create_scratch_schema(conn, "w2s_abc123")   # search_path left on the schema
    ...
    await drop_scratch_schema(conn, "w2s_abc123")
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

FASTAPI_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = FASTAPI_ROOT / "app/db/postgres/alembic/migrations"


def _revision_info(migration_py: Path) -> Tuple[str, Optional[str]]:
    tree = ast.parse(migration_py.read_text())
    values: Dict[str, Optional[str]] = {}
    for node in tree.body:
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target, value = node.target.id, node.value
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            target, value = node.targets[0].id, node.value
        if target in ("revision", "down_revision") and value is not None:
            values[target] = ast.literal_eval(value)
    return str(values["revision"]), values.get("down_revision")


def pg_admin_url() -> Optional[str]:
    """
    Superuser URL for tests that create whole scratch databases, or None.

    ``TEST_PG_ADMIN_URL`` from the environment, else from ``apps/fastapi/.env``
    (``bootstrap.dev.sh`` writes the dev Postgres container's ``postgres`` superuser
    there)."""
    if os.environ.get("TEST_PG_ADMIN_URL"):
        return os.environ["TEST_PG_ADMIN_URL"]
    from app.core.settings.base import ENV_FILE
    from dotenv import dotenv_values

    if ENV_FILE.is_file():
        value = dotenv_values(ENV_FILE).get("TEST_PG_ADMIN_URL")
        if value:
            return value
    return None


def migration_chain() -> List[Path]:
    """Migration folders in apply order (follows down_revision links from the root)."""
    nodes: Dict[str, Tuple[Path, Optional[str]]] = {}
    for folder in sorted(MIGRATIONS.iterdir()):
        mig = folder / "migration.py"
        if folder.is_dir() and mig.is_file():
            rev, down = _revision_info(mig)
            nodes[rev] = (folder, down)
    children: Dict[Optional[str], List[str]] = {}
    for rev, (_, down) in nodes.items():
        children.setdefault(down, []).append(rev)
    order: List[Path] = []
    frontier = sorted(children.get(None, []))
    while frontier:
        rev = frontier.pop(0)
        order.append(nodes[rev][0])
        frontier.extend(sorted(children.get(rev, [])))
    return order


def up_sql(folder: Path) -> str:
    """up.sql without VACUUM lines (they cannot run inside a transaction)."""
    path = folder / "up.sql"
    if not path.is_file():
        return ""
    return "\n".join(
        line for line in path.read_text().splitlines() if not line.strip().startswith("VACUUM")
    )


async def apply_migrations(conn) -> None:
    """Apply every migration's up.sql on an asyncpg connection (search_path already set)."""
    for folder in migration_chain():
        sql = up_sql(folder)
        if sql.strip():
            await conn.execute(sql)


async def create_scratch_schema(conn, schema: str) -> None:
    """CREATE SCHEMA + all migrations; drops the schema again if anything fails."""
    await conn.execute(f'CREATE SCHEMA "{schema}"')
    try:
        await conn.execute(f'SET search_path TO "{schema}", public')
        await apply_migrations(conn)
    except Exception:
        await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        raise


async def drop_scratch_schema(conn, schema: str) -> None:
    await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
