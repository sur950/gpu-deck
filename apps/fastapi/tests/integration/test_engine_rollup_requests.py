"""
Live requests per minute / error rate from the 1-minute engine rollup (FULL-TEST #21).

Under a 24-concurrent load on vLLM the instance's live ``requests_per_minute`` stayed 0
and ``error_rate_pct`` null: ``requests_total`` comes from the node proxy, which never
saw the traffic, while vLLM's own ``request_success_total`` climbed. The rollup now
counts the larger of the proxy counter delta and the engine's success + error deltas.
Runs ``MetricRepository.rollup_minutes`` on a scratch schema.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest
from app.core.settings import settings
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from tests.schema_helpers import create_scratch_schema, drop_scratch_schema

pytestmark = [pytest.mark.integration, pytest.mark.db]


def _plain_dsn() -> str:
    return settings.get_database_url().replace("postgresql+asyncpg://", "postgresql://", 1)


async def test_rollup_counts_requests_the_engine_served():
    from app.db.postgres.repositories.metric_repository import MetricRepository

    schema = f"rolltest_{uuid.uuid4().hex[:8]}"
    conn = await asyncpg.connect(_plain_dsn())
    engine = None
    try:
        await create_scratch_schema(conn, schema)
        await conn.execute("SELECT create_daily_partitions(1, 1)")
        node, dep = uuid.uuid4(), uuid.uuid4()
        direct, proxied, silent = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        minute = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=2)
        rows = []
        for i in range(31):  # one sample every 2 s, from the minute before
            ts = minute - timedelta(seconds=2) + timedelta(seconds=2 * i)
            # vLLM hit directly on the node: proxy counter flat, engine success counter +2/sample
            rows.append((ts, direct, 7, 100 + 2 * i, 0))
            # through the proxy: 3 requests per sample, every third sample adds an error
            rows.append((ts, proxied, 3 * i, 2 * i + (i // 3), i - (i // 3)))
            # an engine with no counters at all
            rows.append((ts, silent, None, None, None))
        await conn.executemany(
            "INSERT INTO metric_samples_engine (ts, node_id, instance_id, deployment_id, "
            "requests_total, requests_success_total, errors_total, active_sessions, health) "
            "VALUES ($1, $6, $2, $7, $3, $4, $5, 24, 'healthy')",
            [r + (node, dep) for r in rows],
        )
        engine = create_async_engine(
            settings.get_database_url(),
            poolclass=NullPool,
            connect_args={"server_settings": {"search_path": f"{schema},public"}},
        )
        async with AsyncSession(engine) as session:
            repo = MetricRepository(session)
            await repo.rollup_minutes(minute, minute + timedelta(minutes=1), 2.0)
            await session.commit()
            latest = await repo.last_minute_engine_rollups([direct, proxied, silent])
        assert latest[direct]["requests_delta"] == 60  # 30 samples x 2 (engine counter)
        assert latest[direct]["error_rate_pct"] == 0.0
        assert latest[proxied]["requests_delta"] == 90  # proxy counter
        assert latest[proxied]["errors_delta"] == 20
        assert latest[proxied]["error_rate_pct"] == pytest.approx(20 / 90 * 100, rel=1e-4)
        assert not latest[silent]["requests_delta"]
        assert latest[silent]["error_rate_pct"] is None

        from app.services.metrics.live_cache import instance_live

        live = instance_live({"ts": "x"}, latest[direct])
        assert live["requests_per_minute"] == 60.0 and live["error_rate_pct"] == 0.0
    finally:
        if engine is not None:
            await engine.dispose()
        await drop_scratch_schema(conn, schema)
        await conn.close()
