"""
Node connectivity vs. drain, offline alerts and node removal (MON-TEST findings).

- A drained node that disconnects is ``offline`` (shows detached, raises ``node.offline``);
  the drain stays in ``drain_requested_at`` / ``drained_at`` and comes back on reconnect
  (hello and heartbeat recovery paths).
- The dashboard's ``nodes_online`` counts connected drained / draining nodes.
- ``node.offline`` is raised once per disconnect, after ``NODE_OFFLINE_AFTER_SECONDS``.
- Removing a node soft-deletes its devices (dashboard counts exclude them), releases their
  reservations and removes its observed (external) deployments.

Runs on a scratch schema with its own engine (no global swapped).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import asyncpg
import pytest
from app.core.settings import settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from tests.schema_helpers import create_scratch_schema, drop_scratch_schema

pytestmark = [pytest.mark.integration, pytest.mark.db]

GIB = 1024**3


def _plain_dsn() -> str:
    return settings.get_database_url().replace("postgresql+asyncpg://", "postgresql://", 1)


@pytest.fixture
async def db():
    schema = f"nodeconn_{uuid.uuid4().hex[:8]}"
    conn = await asyncpg.connect(_plain_dsn())
    await create_scratch_schema(conn, schema)
    engine = create_async_engine(
        settings.get_database_url(),
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": f"{schema},public"}},
    )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    cluster = uuid.uuid4()
    async with maker() as s:
        await s.execute(text("INSERT INTO clusters (id, name) VALUES (:id, 'c')"), {"id": cluster})
        await s.commit()
    try:
        yield maker, cluster
    finally:
        await engine.dispose()
        await drop_scratch_schema(conn, schema)
        await conn.close()


async def _node(maker, cluster, name: str, status: str = "online") -> uuid.UUID:
    node = uuid.uuid4()
    async with maker() as s:
        await s.execute(
            text(
                "INSERT INTO nodes (id, cluster_id, name, os, arch, status, last_heartbeat_at,"
                " connected_at) VALUES (:id, :c, :n, 'linux', 'x86_64', :st, now(), now())"
            ),
            {"id": node, "c": cluster, "n": name, "st": status},
        )
        await s.commit()
    return node


async def _row(maker, node):
    async with maker() as s:
        return (
            (
                await s.execute(
                    text(
                        "SELECT status::text AS status, drain_requested_at, drained_at,"
                        " disconnected_at, deleted_at FROM nodes WHERE id = :id"
                    ),
                    {"id": node},
                )
            )
            .mappings()
            .one()
        )


async def test_drained_node_disconnects_offline_and_reconnects_drained(db):
    from app.db.postgres.repositories.metric_repository import MetricRepository
    from app.db.postgres.repositories.node_repository import NodeRepository

    maker, cluster = db
    node = await _node(maker, cluster, "h200")
    other = await _node(maker, cluster, "spare")
    now = datetime.now(timezone.utc)
    async with maker() as s:
        await NodeRepository(s).set_status(
            node, "drained", "monitor only", drain_requested_at=now, drained_at=now
        )
        await s.commit()
    async with maker() as s:
        counts = await MetricRepository(s).overview_counts()
    assert counts["nodes_online"] == 2  # a connected drained node is online

    async with maker() as s:
        await NodeRepository(s).mark_disconnected(node, "socket closed")
        await s.commit()
    row = await _row(maker, node)
    assert row["status"] == "offline" and row["disconnected_at"] is not None
    assert row["drain_requested_at"] is not None and row["drained_at"] is not None
    async with maker() as s:
        assert (await MetricRepository(s).overview_counts())["nodes_online"] == 1

    async with maker() as s:  # hello: back to drained, not online
        await NodeRepository(s).mark_connected(node)
        await s.commit()
    assert (await _row(maker, node))["status"] == "drained"

    async with maker() as s:  # heartbeat recovery path (stale sweep flipped it offline)
        await NodeRepository(s).set_status(node, "offline", "no heartbeat")
        assert await NodeRepository(s).touch_heartbeat(node)
        await s.commit()
    assert (await _row(maker, node))["status"] == "drained"

    async with maker() as s:  # a draining node that is not drained yet
        await NodeRepository(s).set_status(other, "draining", None, drain_requested_at=now)
        await NodeRepository(s).mark_disconnected(other, "socket closed")
        await NodeRepository(s).mark_connected(other)
        await s.commit()
    assert (await _row(maker, other))["status"] == "draining"

    # A plain online node that reconnects is online.
    plain = await _node(maker, cluster, "plain")
    async with maker() as s:
        await NodeRepository(s).mark_disconnected(plain, "socket closed")
        await NodeRepository(s).mark_connected(plain)
        await s.commit()
    assert (await _row(maker, plain))["status"] == "online"


async def test_stale_sweep_covers_drained_nodes(db):
    from app.db.postgres.repositories.node_repository import NodeRepository

    maker, cluster = db
    node = await _node(maker, cluster, "quiet", status="drained")
    async with maker() as s:
        await s.execute(
            text("UPDATE nodes SET last_heartbeat_at = now() - interval '10 minutes' WHERE id=:id"),
            {"id": node},
        )
        await s.commit()
    async with maker() as s:
        stale = await NodeRepository(s).find_stale(60)
    assert node in {n.id for n in stale}


async def test_offline_alert_once_per_disconnect(db, monkeypatch):
    from app.db.postgres.repositories.node_repository import NodeRepository
    from app.services.alerts import alerts
    from app.services.nodes import maintenance

    maker, cluster = db
    node = await _node(maker, cluster, "gone")
    quick = await _node(maker, cluster, "restarting")
    now = datetime.now(timezone.utc)
    async with maker() as s:
        await NodeRepository(s).set_status(
            node, "drained", None, drain_requested_at=now, drained_at=now
        )
        await NodeRepository(s).mark_disconnected(node, "socket closed")
        await NodeRepository(s).mark_disconnected(quick, "socket closed")
        await s.execute(
            text("UPDATE nodes SET disconnected_at = now() - interval '5 minutes' WHERE id=:id"),
            {"id": node},
        )
        await s.commit()
    emitted = []

    async def fake_emit(event_type, severity, title, message=None, **kw):
        emitted.append((event_type, kw["resource_id"], kw["payload"]["drained"], kw["dedupe_key"]))
        return uuid.uuid4()

    monkeypatch.setattr(maintenance, "get_session_maker", lambda: maker)
    monkeypatch.setattr(alerts, "emit", fake_emit)
    monkeypatch.setattr(settings, "NODE_OFFLINE_AFTER_SECONDS", 60)
    assert await maintenance.alert_offline_nodes() == 1
    assert emitted == [("node.offline", node, True, f"node.offline:{node}")]
    assert await maintenance.alert_offline_nodes() == 0  # once per disconnect

    async with maker() as s:  # reconnect, then a new disconnect: alerted again
        await NodeRepository(s).mark_connected(node)
        await NodeRepository(s).mark_disconnected(node, "socket closed")
        await s.execute(
            text("UPDATE nodes SET disconnected_at = now() - interval '2 minutes' WHERE id=:id"),
            {"id": node},
        )
        await s.commit()
    assert await maintenance.alert_offline_nodes() == 1
    assert len(emitted) == 2 and quick not in {e[1] for e in emitted}


async def test_node_removal_drops_devices_slices_and_observed_deployments(db):
    from app.db.postgres.repositories.metric_repository import MetricRepository
    from app.db.postgres.repositories.node_repository import NodeRepository
    from app.services.deployments.external import observe_ids, remove_for_node, sync_from_inventory

    maker, cluster = db
    node = await _node(maker, cluster, "old", status="offline")
    keep = await _node(maker, cluster, "keep")
    dev, kept_dev = uuid.uuid4(), uuid.uuid4()
    async with maker() as s:
        for d, n in ((dev, node), (kept_dev, keep)):
            await s.execute(
                text(
                    "INSERT INTO devices (id, node_id, vendor, index, name, vram_total_bytes,"
                    " health) VALUES (:id, :n, 'nvidia', 0, 'H200', :v, 'healthy')"
                ),
                {"id": d, "n": n, "v": 141 * GIB},
            )
        await s.execute(
            text(
                "INSERT INTO device_slices (device_id, vram_bytes, compute_pct, status, name)"
                " VALUES (:d, :v, 20, 'active', 'direct')"
            ),
            {"d": dev, "v": 8 * GIB},
        )
        inst_id, dep_id = observe_ids(node, "main")
        await sync_from_inventory(
            s,
            node,
            [
                {
                    "observe_id": str(inst_id),
                    "deployment_id": str(dep_id),
                    "name": "main",
                    "engine": "vllm",
                    "health": "healthy",
                    "models": ["m"],
                    "device_ids": [str(dev)],
                }
            ],
        )
        await s.commit()
    async with maker() as s:
        before = await MetricRepository(s).overview_counts()
    assert before["devices"] == 2

    async with maker() as s:
        await NodeRepository(s).soft_remove(node)
        assert await remove_for_node(s, node) == 1
        await s.commit()
    async with maker() as s:
        after = await MetricRepository(s).overview_counts()
        devs = (
            (await s.execute(text("SELECT id, deleted_at FROM devices ORDER BY name, id")))
            .mappings()
            .all()
        )
        slice_status = (
            await s.execute(
                text("SELECT status::text FROM device_slices WHERE device_id=:d"), {"d": dev}
            )
        ).scalar_one()
        dep = (
            (
                await s.execute(
                    text("SELECT deleted_at FROM deployments WHERE id=:id"), {"id": dep_id}
                )
            )
            .mappings()
            .one()
        )
    assert after["devices"] == 1 and after["nodes"] == 1
    assert {d["id"]: d["deleted_at"] is not None for d in devs} == {dev: True, kept_dev: False}
    assert slice_status == "released"
    assert dep["deleted_at"] is not None
