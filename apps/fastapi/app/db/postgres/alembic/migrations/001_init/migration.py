"""GPU Deck init — every table for all 23 modules

Revision ID: 001_init
Revises: None
Create Date: 2026-11-16 00:00:00.000000

The SQL lives in up.sql / down.sql next to this file. This wrapper only runs
them; do not put schema statements here.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "001_init"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _run_sql_file(name: str, strip_vacuum: bool = False) -> None:
    from pathlib import Path

    sql_file = Path(__file__).parent / name
    with open(sql_file, "r", encoding="utf-8") as f:
        sql_content = f.read()

    if strip_vacuum:
        # VACUUM cannot run inside a transaction block.
        lines = sql_content.split("\n")
        sql_content = "\n".join(line for line in lines if not line.strip().startswith("VACUUM"))

    if not sql_content.strip():
        return

    # Execute directly on the raw DBAPI connection to bypass SQLAlchemy's
    # parameter-binding parser, which misinterprets PostgreSQL cast syntax
    # (e.g. 'value'::type) as named bind parameters.
    bind = op.get_bind()
    bind.connection.cursor().execute(sql_content)


def upgrade() -> None:
    _run_sql_file("up.sql", strip_vacuum=True)


def downgrade() -> None:
    _run_sql_file("down.sql")
