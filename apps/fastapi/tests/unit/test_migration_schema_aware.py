"""
Every type / enum existence check in a migration's up.sql must be schema-aware.

``pg_type WHERE typname = 'x'`` without ``typnamespace`` finds ``public``'s
type when the migrations run in a scratch schema, so the scratch schema's
column binds to ``public.x`` (and breaks once ``public`` leaves the search_path).
"""

import re
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[2] / "app/db/postgres/alembic/migrations"


def test_pg_type_checks_filter_on_the_current_schema():
    offenders = []
    for up in sorted(MIGRATIONS.glob("*/up.sql")):
        sql = up.read_text()
        for m in re.finditer(r"FROM\s+pg_type\s+WHERE(?P<cond>.*?)\)\s*THEN", sql, re.S | re.I):
            if "typnamespace = current_schema()::regnamespace" not in " ".join(m["cond"].split()):
                offenders.append(f"{up.parent.name}: {' '.join(m.group(0).split())[:120]}")
    assert not offenders, offenders
