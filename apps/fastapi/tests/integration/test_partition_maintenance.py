"""
Partition maintenance stays inside the current schema (migration 002).

``drop_old_partitions()`` from 001_init matched partitions by bare table name
across every schema and dropped them by an unqualified name, so the retention
job running in one schema (a scratch schema, a second install in the same
database) listed and could drop another schema's partitions. Two scratch
schemas get the same old partition; retention in one must leave the other
(and ``public``) alone, and ``create_daily_partitions()`` must create the
current schema's partitions even when ``public`` already has them.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest
from app.core.settings import settings
from tests.schema_helpers import create_scratch_schema, drop_scratch_schema

pytestmark = [
    pytest.mark.integration,
    pytest.mark.db,
    pytest.mark.asyncio(loop_scope="session"),
]

OLD = "usage_events_p20200101"


def _plain_dsn() -> str:
    return settings.get_database_url().replace("postgresql+asyncpg://", "postgresql://", 1)


async def _old_partition(conn, schema: str) -> None:
    await conn.execute(
        f'CREATE TABLE "{schema}".{OLD} PARTITION OF "{schema}".usage_events '
        "FOR VALUES FROM ('2020-01-01 00:00:00+00') TO ('2020-01-02 00:00:00+00')"
    )


async def _exists(conn, schema: str, name: str) -> bool:
    return await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", f'"{schema}".{name}')


async def test_retention_and_creation_only_touch_the_current_schema() -> None:
    tag = uuid.uuid4().hex[:8]
    one, two = f"parttest_a_{tag}", f"parttest_b_{tag}"
    conn = await asyncpg.connect(_plain_dsn())
    try:
        await create_scratch_schema(conn, one)
        await create_scratch_schema(conn, two)
        await _old_partition(conn, one)
        await _old_partition(conn, two)
        public_before = await conn.fetchval(
            "SELECT count(*) FROM pg_inherits i JOIN pg_class p ON p.oid = i.inhparent "
            "WHERE p.relname = 'usage_events' AND p.relnamespace = 'public'::regnamespace"
        )

        await conn.execute(f'SET search_path TO "{one}", public')
        assert await conn.fetchval("SELECT drop_old_partitions('usage_events', 30)") == 1
        assert not await _exists(conn, one, OLD)
        assert await _exists(conn, two, OLD)
        # a second run finds nothing (the other schema's partition is not listed)
        assert await conn.fetchval("SELECT drop_old_partitions('usage_events', 30)") == 0

        public_after = await conn.fetchval(
            "SELECT count(*) FROM pg_inherits i JOIN pg_class p ON p.oid = i.inhparent "
            "WHERE p.relname = 'usage_events' AND p.relnamespace = 'public'::regnamespace"
        )
        assert public_after == public_before

        # creation resolves names in the current schema, not through the search_path
        today = await conn.fetchval(
            "SELECT 'usage_events_p' || to_char((now() AT TIME ZONE 'UTC')::date, 'YYYYMMDD')"
        )
        await conn.execute(f'DROP TABLE "{one}".{today}')
        assert await conn.fetchval("SELECT create_daily_partitions(0, 0)") >= 1
        assert await _exists(conn, one, today)
    finally:
        await conn.execute("RESET search_path")
        await drop_scratch_schema(conn, one)
        await drop_scratch_schema(conn, two)
        await conn.close()
