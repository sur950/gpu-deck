"""
Every migration folder must be listed in alembic.ini's version_locations;
alembic only discovers revisions there, so an unlisted folder never runs.
Also checks the raw-cursor execution convention and numbering ranges.
"""

import configparser
import re
from pathlib import Path

import pytest

FASTAPI_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = FASTAPI_ROOT / "app/db/postgres/alembic/migrations"


def _migration_folders():
    return sorted(d for d in MIGRATIONS.iterdir() if d.is_dir() and (d / "migration.py").is_file())


@pytest.mark.unit
def test_every_migration_folder_is_in_version_locations():
    ini = configparser.ConfigParser()
    ini.read(FASTAPI_ROOT / "alembic.ini")
    listed = set(ini["alembic"]["version_locations"].split())
    folders = {str(d.relative_to(FASTAPI_ROOT)) for d in _migration_folders()}
    assert folders - listed == set()


@pytest.mark.unit
def test_migrations_execute_sql_via_raw_cursor():
    for folder in _migration_folders():
        source = (folder / "migration.py").read_text()
        assert "connection.cursor().execute(" in source, folder.name
        assert "op.execute(sql" not in source, folder.name


@pytest.mark.unit
def test_migration_folders_are_numbered():
    for folder in _migration_folders():
        assert re.match(r"^\d{3}_[a-z0-9_]+$", folder.name), folder.name
