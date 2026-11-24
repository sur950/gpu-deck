"""Deployment instance live data includes the last-minute engine rollup (requests/min)."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from app.services.deployments import service as dep_service


@pytest.mark.asyncio
async def test_instance_lives_passes_rollup_requests_per_minute():
    iid = uuid.uuid4()
    sample = {"ts": "2026-11-18T10:00:00+00:00", "active_sessions": 2}
    repo = AsyncMock()
    repo.last_minute_engine_rollups.return_value = {iid: {"requests_delta": 42}}
    with patch(
        "app.db.postgres.repositories.metric_repository.MetricRepository",
        return_value=repo,
    ), patch("app.services.deployments.live.samples", AsyncMock(return_value=[sample])), patch(
        "app.services.metrics.live_cache.is_fresh", return_value=True
    ):
        lives = await dep_service._instance_lives(AsyncMock(), [iid])
    assert lives[iid].requests_per_minute == 42


@pytest.mark.asyncio
async def test_instance_lives_survives_rollup_failure():
    iid = uuid.uuid4()
    sample = {"ts": "2026-11-18T10:00:00+00:00"}
    repo = AsyncMock()
    repo.last_minute_engine_rollups.side_effect = RuntimeError("db down")
    with patch(
        "app.db.postgres.repositories.metric_repository.MetricRepository",
        return_value=repo,
    ), patch("app.services.deployments.live.samples", AsyncMock(return_value=[sample])), patch(
        "app.services.metrics.live_cache.is_fresh", return_value=True
    ):
        lives = await dep_service._instance_lives(AsyncMock(), [iid])
    assert lives[iid].requests_per_minute is None
