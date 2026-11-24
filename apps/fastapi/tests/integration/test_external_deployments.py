"""
Integration (real Postgres + Redis, scratch schema): observed (external) engines.

The agent reports model servers GPU Deck did not start in
``inventory.observed_engines``; the hub registers them as external deployments
and instances (also on a drained node), stores their engine samples in the
normal metric tables, marks them stale when the agent stops reporting them,
and never acts on them: no scheduling, reconciling, rules, swaps, layouts,
routing, benchmarks or agent commands (409 ``EXTERNAL_DEPLOYMENT``).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import asyncpg
import pytest
import pytest_asyncio
from app.core.logger import get_logger
from app.core.settings import settings

logger = get_logger(__name__)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.db,
    pytest.mark.asyncio(loop_scope="session"),
]


def _dsn() -> str:
    return settings.get_database_url().replace("postgresql+asyncpg://", "postgresql://", 1)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class Env:
    def __init__(self) -> None:
        self.tag = uuid.uuid4().hex[:10]
        self.schema = f"exttest_{self.tag}"
        self.prefix = f"gpu-deck:exttest:{self.tag}:"
        self.cluster = uuid.uuid4()
        self.node = uuid.uuid4()
        self.device = uuid.uuid5(self.node, "nvidia:0")

    async def sql(self, query: str, *args):
        conn = await asyncpg.connect(_dsn(), server_settings={"search_path": self.schema})
        try:
            return await conn.fetch(query, *args)
        finally:
            await conn.close()

    def session(self):
        from app.db.postgres.connection import get_session_maker

        return get_session_maker()()

    def ids(self, name: str):
        return uuid.uuid5(self.node, f"observe:{name}"), uuid.uuid5(
            self.node, f"observe-deployment:{name}"
        )

    def observed(self, name: str, engine: str = "vllm", **kw) -> Dict[str, Any]:
        inst, dep = self.ids(name)
        item = {
            "observe_id": str(inst),
            "deployment_id": str(dep),
            "name": name,
            "engine": engine,
            "endpoint": "127.0.0.1:8000",
            "sources": ["metrics", "health", "models", "log"],
            "models": ["Qwen/Qwen3-8B"],
            "version": "0.34.0",
            "health": "healthy",
            "message": None,
            "last_ok_at": _now(),
            "pid": 4242,
            "device_ids": [str(self.device)],
        }
        item.update(kw)
        return item

    def inventory(self, observed: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "devices": [],
            "disks": [],
            "cache": {
                "dir": "/var/lib/gpudeck/cache",
                "limit_bytes": 0,
                "used_bytes": 0,
                "items": [],
            },
            "instances": [],
            "observed_engines": observed,
        }


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def env():
    from app.db.postgres import connection
    from app.db.redis import client as redis_client_module
    from app.db.redis.cache_service import CacheService
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool
    from tests.schema_helpers import create_scratch_schema, drop_scratch_schema

    e = Env()
    conn = await asyncpg.connect(_dsn())
    try:
        await create_scratch_schema(conn, e.schema)
    finally:
        await conn.close()
    e.engine = create_async_engine(
        settings.get_database_url(),
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": e.schema}},
    )
    saved = {"engine": connection.engine, "prefix": settings.REDIS_KEY_PREFIX}
    connection.engine = e.engine
    settings.REDIS_KEY_PREFIX = e.prefix
    await CacheService.reset()
    await redis_client_module.close_redis()
    await e.sql("INSERT INTO clusters (id, name) VALUES ($1, 'droplets')", e.cluster)
    # The node is drained on purpose and stays drained.
    await e.sql(
        "INSERT INTO nodes (id, cluster_id, name, status, os, arch, drained_at) "
        "VALUES ($1, $2, 'droplet', 'drained', 'linux', 'x86_64', now())",
        e.node,
        e.cluster,
    )
    await e.sql(
        "INSERT INTO devices (id, node_id, vendor, index, name, vram_total_bytes, health) "
        "VALUES ($1, $2, 'nvidia', 0, 'NVIDIA H200', 150754820096, 'healthy')",
        e.device,
        e.node,
    )
    try:
        yield e
    finally:
        try:
            redis = await redis_client_module.get_redis()
            keys = [k async for k in redis.scan_iter(match=f"{e.prefix}*", count=500)]
            if keys:
                await redis.delete(*keys)
        finally:
            await CacheService.reset()
            await redis_client_module.close_redis()
            connection.engine = saved["engine"]
            settings.REDIS_KEY_PREFIX = saved["prefix"]
            await e.engine.dispose()
            conn = await asyncpg.connect(_dsn())
            try:
                await drop_scratch_schema(conn, e.schema)
            finally:
                await conn.close()


async def _ingest_inventory(env: Env, observed: List[Dict[str, Any]]) -> Dict[str, Any]:
    from app.services.agent_hub.ingest import HubIngest

    return await HubIngest().inventory(env.node, env.inventory(observed))


async def test_observed_engines_register_and_report_on_a_drained_node(env: Env):
    from app.services.agent_hub.ingest import HubIngest
    from app.services.deployments.service import DeploymentService

    summary = await _ingest_inventory(
        env,
        [
            env.observed("main"),
            env.observed(
                "img",
                engine="generic",
                endpoint=None,
                sources=["log"],
                models=["example-image-model"],
                version=None,
                pid=None,
                device_ids=[],
            ),
        ],
    )
    assert summary["observed_engines"] == {"external": 2, "stale": 0}
    inst, dep = env.ids("main")
    rows = await env.sql(
        "SELECT id, name, origin::text, status::text, model_id, engine_version_id, external "
        "FROM deployments ORDER BY name"
    )
    assert [r["origin"] for r in rows] == ["external", "external"]
    main = next(r for r in rows if r["id"] == dep)
    assert main["name"] == "main@droplet" and main["status"] == "running"
    assert main["model_id"] is None and main["engine_version_id"] is None
    irow = (await env.sql("SELECT * FROM instances WHERE id = $1", inst))[0]
    assert irow["origin"] == "external" and irow["state"] == "ready"
    assert irow["traffic_weight"] == 0 and irow["port"] is None
    assert irow["device_ids"] == [env.device] and irow["pid"] == 4242
    assert irow["served_model_name"] == "Qwen/Qwen3-8B"
    node = (await env.sql("SELECT status::text FROM nodes WHERE id = $1", env.node))[0]
    assert node["status"] == "drained", "observing never changes the node's drain state"

    # Engine samples land in the normal metric tables (drained node included).
    counts = await HubIngest().metrics_batch(
        env.node,
        {
            "interval_seconds": 2,
            "gpu": [],
            "engine": [
                {
                    "ts": _now(),
                    "instance_id": str(inst),
                    "deployment_id": str(dep),
                    "observed": True,
                    "active_sessions": 4,
                    "generation_tps": 148.0,
                    "ttft_ms_p95": 180.0,
                    "kv_cache_usage_pct": 31.0,
                    "prompt_tokens_total": 52000,
                    "generated_tokens_total": 10000,
                    "health": "healthy",
                    "extra": {
                        "engine": "vllm",
                        "status_counts": {"2xx": 9, "5xx": 1},
                        "top_endpoints": [
                            {
                                "method": "POST",
                                "path": "/v1/chat/completions",
                                "count": 10,
                            }
                        ],
                    },
                }
            ],
            "sessions": [],
        },
    )
    assert counts["engine"] == 1
    stored = await env.sql(
        "SELECT active_sessions, generation_tps FROM metric_samples_engine WHERE instance_id=$1",
        inst,
    )
    assert stored and stored[0]["active_sessions"] == 4

    async with env.session() as s:
        svc = DeploymentService.for_session(s, publish=False)
        listed, total = await svc.list_deployments(origin="external")
        assert total == 2
        ext = next(d for d in listed if d.id == dep)
        assert ext.origin.value == "external"
        assert ext.model_name == "Qwen/Qwen3-8B"
        assert ext.engine_name.value == "vllm"
        assert ext.external.endpoint == "127.0.0.1:8000"
        assert ext.external.engine_label == "vLLM"
        assert ext.external.status_counts == {"2xx": 9, "5xx": 1}
        assert ext.instances and ext.instances[0].origin.value == "external"
        managed, mtotal = await svc.list_deployments(origin="managed")
        assert mtotal == 0
        instances, itotal = await svc.list_instances(origin="external")
        assert itotal == 2
        assert {i.deployment_name for i in instances} == {"main@droplet", "img@droplet"}


async def test_gpu_deck_never_acts_on_external_deployments(env: Env):
    from app.core.exceptions import ExternalDeploymentError
    from app.db.postgres.repositories.cluster_state_repository import (
        ClusterStateRepository,
    )
    from app.db.postgres.repositories.deployment_repository import DeploymentRepository
    from app.db.postgres.repositories.instance_repository import InstanceRepository
    from app.db.postgres.repositories.inventory_repository import InventoryRepository
    from app.schemas.benchmarks_schema import BenchmarkCreate
    from app.schemas.deployments_schema import (
        DeploymentUpdate,
        InstanceDrainRequest,
        InstanceUpdate,
        ScaleRequest,
        TrafficWeightsRequest,
    )
    from app.schemas.layouts_schema import LayoutCreate, LayoutItemInput
    from app.schemas.swaps_schema import SwapCreate
    from app.services.agent_hub.service import AgentHub, SessionAgentHub
    from app.services.benchmark.service import BenchmarkService
    from app.services.deployments.reconcile import Reconciler
    from app.services.deployments.routing_publisher import build_routing_table
    from app.services.deployments.service import DeploymentService
    from app.services.layouts.service import LayoutService
    from app.services.rules.inputs import DbRuleInputs
    from app.services.swap.service import SwapService

    await _ingest_inventory(env, [env.observed("main")])
    inst, dep = env.ids("main")
    before = (
        await env.sql("SELECT state, health, traffic_weight FROM instances WHERE id=$1", inst)
    )[0]

    async with env.session() as s:
        svc = DeploymentService.for_session(s, publish=False)
        mutations = [
            svc.update_deployment(dep, DeploymentUpdate(description="x")),
            svc.delete_deployment(dep),
            svc.scale(dep, ScaleRequest(replicas=2)),
            svc.stop(dep),
            svc.start(dep),
            svc.restart(dep, InstanceDrainRequest()),
            svc.set_traffic(dep, TrafficWeightsRequest(weights={str(inst): 100})),
            svc.update_instance(inst, InstanceUpdate(traffic_weight=100)),
            svc.remove_instance(inst),
            svc.drain_instance(inst, InstanceDrainRequest()),
            svc.restart_instance(inst, InstanceDrainRequest()),
            svc.start_instance(inst),
            svc.stop_instance(inst),
            svc.place_instance(dep),
        ]
        for call in mutations:
            with pytest.raises(ExternalDeploymentError) as exc:
                await call
            assert exc.value.status_code == 409
            assert exc.value.error_code == "EXTERNAL_DEPLOYMENT"
        # Command dispatch: never for an external instance (both entry points).
        with pytest.raises(ExternalDeploymentError):
            await SessionAgentHub(s).send_command(
                env.node, "unload_model", {"instance_id": str(inst)}, instance_id=inst
            )
        with pytest.raises(ExternalDeploymentError):
            await SwapService(s).create_swap(
                SwapCreate(kind="restart", deployment_id=dep), created_by=None
            )
        with pytest.raises(ExternalDeploymentError):
            await BenchmarkService.for_session(s).create_benchmark(
                BenchmarkCreate(name="ext-bench", deployment_id=dep)
            )
        with pytest.raises(ExternalDeploymentError):
            await LayoutService(s).create_layout(
                LayoutCreate(name="ext-layout", items=[LayoutItemInput(deployment_id=dep)]),
                created_by=None,
            )
        await s.rollback()
    with pytest.raises(ExternalDeploymentError):
        await AgentHub.send_command(
            env.node,
            "drain_instance",
            {"instance_id": str(inst)},
            instance_id=inst,
            wait=False,
        )

    async with env.session() as s:
        # Scheduler / reconciler / rules / layouts / routing views leave them out.
        assert dep not in [d.id for d in await DeploymentRepository(s).list_for_reconcile()]
        assert inst not in [i.id for i in await InstanceRepository(s).all_live()]
        state = ClusterStateRepository(s)
        assert await state.get_deployment(dep) is None
        assert await state.get_instance(inst) is None
        assert inst not in [i.id for i in await state.live_instances()]
        assert dep not in [d["deployment"].id for d in await state.deployments_with_models()]
        assert dep not in [d.id for d in await DbRuleInputs(s).deployments()]
        table = await build_routing_table(s)
        assert str(inst) not in str(table) and str(dep) not in str(table)
        # Drain bookkeeping ignores them: a drained node stays drained.
        assert await InventoryRepository(s).instance_ids_for_node(env.node) == []
        # A full reconcile tick sends nothing and changes nothing.
        summary = await Reconciler(DeploymentService.for_session(s, publish=False)).tick()
        assert summary.started == []
    assert not await env.sql("SELECT id FROM agent_commands")
    assert not await env.sql("SELECT id FROM swap_operations")
    after = (
        await env.sql("SELECT state, health, traffic_weight FROM instances WHERE id=$1", inst)
    )[0]
    assert dict(after) == dict(before)

    # The managed-instance inventory reconcile does not error the external instance.
    async with env.session() as s:
        updated, errored = await InventoryRepository(s).reconcile_instances(env.node, [], [])
        await s.commit()
    assert inst not in errored
    assert (await env.sql("SELECT state FROM instances WHERE id=$1", inst))[0]["state"] == "ready"


async def test_stale_purge_revive_and_foreign_ids(env: Env):
    from app.services.deployments.external import purge_stale

    await _ingest_inventory(env, [env.observed("main")])
    inst, dep = env.ids("main")
    # The agent stops reporting it ([[observe]] entry removed): stale and removed at once
    # (FULL-TEST #22: observed deployments outlived their entries for a day).
    summary = await _ingest_inventory(env, [])
    assert summary["observed_engines"]["stale"] >= 1
    d = (
        await env.sql(
            "SELECT status::text, deleted_at, external FROM deployments WHERE id=$1",
            dep,
        )
    )[0]
    assert d["status"] == "stopped" and d["deleted_at"] is not None
    ext = d["external"] if isinstance(d["external"], dict) else json.loads(d["external"])
    assert ext["stale_since"]
    i = (await env.sql("SELECT state::text FROM instances WHERE id=$1", inst))[0]
    assert i["state"] == "stopped"
    # Back again: revived.
    await _ingest_inventory(
        env, [env.observed("main", health="unhealthy", message="GET /metrics: timeout")]
    )
    d = (await env.sql("SELECT status::text, external FROM deployments WHERE id=$1", dep))[0]
    assert d["status"] == "degraded"
    i = (await env.sql("SELECT state::text, health::text FROM instances WHERE id=$1", inst))[0]
    assert (i["state"], i["health"]) == ("error", "unhealthy")
    # Leftover stale rows (older versions kept them listed) are soft-deleted by the purge.
    await env.sql(
        "UPDATE deployments SET external = external || $2::jsonb WHERE id=$1 RETURNING id",
        dep,
        json.dumps({"stale_since": "2026-01-01T00:00:00+00:00"}),
    )
    async with env.session() as s:
        assert await purge_stale(s, older_than=timedelta(hours=24)) >= 1
        await s.commit()
    d = (await env.sql("SELECT deleted_at FROM deployments WHERE id=$1", dep))[0]
    assert d["deleted_at"] is not None
    await _ingest_inventory(env, [env.observed("main")])
    d = (await env.sql("SELECT deleted_at, status::text FROM deployments WHERE id=$1", dep))[0]
    assert d["deleted_at"] is None and d["status"] == "running"
    # Ids that are not uuid5(node, name) are ignored (an agent describes only its own).
    foreign = env.observed("evil", observe_id=str(uuid.uuid4()))
    summary = await _ingest_inventory(env, [env.observed("main"), foreign])
    assert summary["observed_engines"]["external"] == 1
    assert not await env.sql("SELECT id FROM deployments WHERE name LIKE 'evil%'")
