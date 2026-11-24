"""llamacpp router seed

Revision ID: 600_llamacpp_router_seed
Revises: '400_layout_unlisted_policy'
Create Date: 2026-11-17 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "600_llamacpp_router_seed"
down_revision: Union[str, None] = "400_layout_unlisted_policy"
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
