"""
Alembic environment — SQL-first migrations.

Each migration lives in ``migrations/NNN_slug/{up.sql,down.sql,migration.py}``
and ``alembic.ini`` lists every folder in ``version_locations``. Alembic only
tracks which revision is applied; the SQL files are the source of truth and
``target_metadata`` exists only so ``alembic check`` can compare.
"""

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import Connection

# Add project root (apps/fastapi) to path
project_root = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(project_root))

from app.core.settings import settings  # noqa: E402
from app.db.postgres.connection import Base  # noqa: E402

# Models may not be generated yet, or may reference a stale schema.
try:
    from app.db.postgres.models import generated_models  # noqa: F401,E402
except Exception:  # noqa: BLE001
    pass

config = context.config

# Escape "%" for configparser interpolation: a URL-encoded password
# (e.g. %40 for "@") otherwise fails with "invalid interpolation syntax".
config.set_main_option("sqlalchemy.url", settings.get_sync_database_url().replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL to stdout)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# Scratch-schema runs (generate_models.sh, schema-isolated tests) put the
# alembic_version table in their own schema; otherwise public.alembic_version,
# visible through search_path, would make alembic think the scratch schema is
# already at head.
VERSION_TABLE_SCHEMA = os.environ.get("ALEMBIC_VERSION_TABLE_SCHEMA") or None


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        version_table_schema=VERSION_TABLE_SCHEMA,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode with a sync psycopg engine."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = settings.get_sync_database_url()
    connectable = engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        do_run_migrations(connection)
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
