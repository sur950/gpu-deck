"""
Integration tests against the real local Postgres and Redis.

Isolation (gpu_deck_dev and the gpu-deck Redis user are shared): a scratch
schema ``b3test_<hex>`` built from 001_init + 400, used through a dedicated
engine whose search_path points at it, dropped at the end; Redis keys get a
scratch ``gpu-deck:b3test:<hex>:`` prefix (routing table, accepting set,
locks) and are deleted at the end; audit events go to an in-memory sink.

Agent commands go through the real hub port (``SessionAgentHub``, which
validates args against command.json and writes ``agent_commands`` rows; the
the ``DbQueueAgentHub`` fallback for the cache tests); tests play the agent by
moving those rows to a terminal state, then run the reconciler. Models are
registered from this Mac's real Ollama and LM Studio directories (read only).
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import asyncpg
import httpx
import pytest
import pytest_asyncio
from app.core.settings import settings
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from sqlalchemy import text

pytestmark = [
    pytest.mark.integration,
    pytest.mark.db,
    pytest.mark.asyncio(loop_scope="session"),
]

MIGRATIONS = Path(__file__).resolve().parents[2] / "app/db/postgres/alembic/migrations"
CONTRACTS = Path(__file__).resolve().parents[4] / "libs/contracts/agent-protocol"
API = settings.API_V1_PREFIX
GIB = 1024**3
OLLAMA_QWEN = Path(
    os.path.expanduser("~/.ollama/models/manifests/registry.ollama.ai/library/qwen3/0.6b")
)
OLLAMA_TINY = Path(
    os.path.expanduser("~/.ollama/models/manifests/registry.ollama.ai/library/tinyllama/latest")
)
LMS_GEMMA = Path(os.path.expanduser("~/.lmstudio/models/mlx-community/gemma-3-1b-it-qat-4bit"))
LMS_NOMIC = Path(
    os.path.expanduser(
        "~/.lmstudio/.internal/bundled-models/nomic-ai/nomic-embed-text-v1.5-GGUF/"
        "nomic-embed-text-v1.5.Q4_K_M.gguf"
    )
)
DEPLOYMENT_ROUTES = (
    "catalog",
    "models",
    "engines",
    "deployments",
    "instances",
    "model_cache",
    "benchmarks",
)
TABLES = (
    "clusters, models, engine_versions, engine_compat_checks, deployments, instances, "
    "device_slices, agent_commands, node_model_cache, benchmarks, benchmark_runs, swap_operations"
)

needs_local_models = pytest.mark.skipif(
    not (OLLAMA_QWEN.exists() and LMS_GEMMA.exists()),
    reason="local Ollama / LM Studio models absent",
)


def _plain_dsn() -> str:
    return settings.get_database_url().replace("postgresql+asyncpg://", "postgresql://", 1)


def _command_validator():
    registry = Registry()
    schemas = {}
    for path in CONTRACTS.glob("*.json"):
        schema = json.loads(path.read_text())
        schemas[path.stem] = schema
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    return Draft202012Validator(schemas["command"], registry=registry)


COMMAND = _command_validator()


def assert_valid_command(row) -> None:
    payload = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
    body = {"command": payload["command"], "args": payload["args"]}
    errors = list(COMMAND.iter_errors(body))
    assert not errors, [f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in errors[:5]]


class Env:
    def __init__(self) -> None:
        self.tag = uuid.uuid4().hex[:10]
        self.schema = f"b3test_{self.tag}"
        self.prefix = f"gpu-deck:b3test:{self.tag}:"
        self.audit: List[Dict[str, Any]] = []

    async def sql(self, query: str, *args):
        conn = await asyncpg.connect(_plain_dsn(), server_settings={"search_path": self.schema})
        try:
            return await conn.fetch(query, *args)
        finally:
            await conn.close()

    async def execute(self, query: str, *args):
        conn = await asyncpg.connect(_plain_dsn(), server_settings={"search_path": self.schema})
        try:
            return await conn.execute(query, *args)
        finally:
            await conn.close()


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def env():
    from app.db.postgres import connection
    from app.db.redis import client as redis_client_module
    from app.db.redis.cache_service import CacheService
    from app.services.audit import audit
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    e = Env()
    conn = await asyncpg.connect(_plain_dsn())
    try:
        await conn.execute(f'CREATE SCHEMA "{e.schema}"')
        await conn.execute(f'SET search_path TO "{e.schema}", public')
        from tests.schema_helpers import migration_chain

        for folder in migration_chain():
            sql = "\n".join(
                line
                for line in (folder / "up.sql").read_text().splitlines()
                if not line.strip().startswith("VACUUM")
            )
            await conn.execute(sql)
    except Exception:
        await conn.execute(f'DROP SCHEMA IF EXISTS "{e.schema}" CASCADE')
        await conn.close()
        raise
    await conn.close()

    e.engine = create_async_engine(
        settings.get_database_url(),
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": e.schema}},
    )
    saved = {"engine": connection.engine, "REDIS_KEY_PREFIX": settings.REDIS_KEY_PREFIX}
    connection.engine = e.engine
    settings.REDIS_KEY_PREFIX = e.prefix
    await CacheService.reset()
    await redis_client_module.close_redis()
    audit.set_sink(e.audit.append)
    try:
        yield e
    finally:
        audit.set_sink(None)
        try:
            redis = await redis_client_module.get_redis()
            keys = [k async for k in redis.scan_iter(match=f"{e.prefix}*", count=500)]
            if keys:
                await redis.delete(*keys)
        finally:
            await CacheService.reset()
            await redis_client_module.close_redis()
            connection.engine = saved["engine"]
            settings.REDIS_KEY_PREFIX = saved["REDIS_KEY_PREFIX"]
            await e.engine.dispose()
            conn = await asyncpg.connect(_plain_dsn())
            await conn.execute(f'DROP SCHEMA IF EXISTS "{e.schema}" CASCADE')
            await conn.close()


class World:
    """Fleet + models for one test."""

    def __init__(self, env: Env) -> None:
        self.env = env
        self.ids: Dict[str, uuid.UUID] = {}

    def session(self):
        from app.db.postgres.connection import get_session_maker

        return get_session_maker()()

    def deployments(self, session):
        """Deployment services on the real hub port: the session-bound hub when present."""
        from app.services.deployments.agent_hub_port import get_agent_hub
        from app.services.deployments.service import DeploymentService

        return DeploymentService.for_session(session, hub=get_agent_hub(session))

    async def commands(self, command: Optional[str] = None, state: Optional[str] = None):
        q = "SELECT * FROM agent_commands WHERE TRUE"
        args: List[Any] = []
        if command:
            args.append(command)
            q += f" AND command_type = ${len(args)}"
        if state:
            args.append(state)
            q += f" AND state = ${len(args)}::agent_command_state"
        return await self.env.sql(q + " ORDER BY created_at, id", *args)

    async def finish(
        self,
        command_id,
        state: str = "succeeded",
        result: Optional[dict] = None,
        code=None,
    ):
        await self.env.execute(
            "UPDATE agent_commands SET state=$2::agent_command_state, result=$3::jsonb,"
            " error_code=$4,"
            " error_message=$5, completed_at=now() WHERE id=$1",
            command_id,
            state,
            json.dumps(result or {}),
            code,
            None if state == "succeeded" else f"agent said {code or state}",
        )

    async def finish_all(self, command: str, state: str = "succeeded", result_for=None):
        rows = await self.commands(command, "pending")
        for i, row in enumerate(rows):
            res = result_for(i, row) if result_for else {}
            await self.finish(row["id"], state, res)
        return rows

    async def tick(self):
        from app.services.deployments.reconcile import Reconciler

        async with self.session() as s:
            summary = await Reconciler(self.deployments(s)).tick()
            await s.commit()
        return summary

    async def instances(self, deployment_id=None, states=None):
        q = "SELECT * FROM instances WHERE TRUE"
        args: List[Any] = []
        if deployment_id:
            args.append(deployment_id)
            q += f" AND deployment_id = ${len(args)}"
        if states:
            args.append(list(states))
            q += f" AND state::text = ANY(${len(args)})"
        return await self.env.sql(q + " ORDER BY created_at", *args)


@pytest_asyncio.fixture(loop_scope="session")
async def world(env):
    from app.schemas.catalog_schema import ModelCreate, ModelVersionCreate
    from app.services.catalog.service import CatalogService
    from app.services.engines.service import EngineService

    await env.execute(f"TRUNCATE {TABLES} CASCADE")
    w = World(env)
    w.ids = {k: uuid.uuid4() for k in ("cluster", "mac", "gpu", "apple0", "nv0", "nv1")}
    await env.execute("INSERT INTO clusters (id, name) VALUES ($1, 'lab')", w.ids["cluster"])
    caps_mac = json.dumps(
        {"engines": [{"name": "llama_cpp"}, {"name": "mlx"}], "runtimes": ["native"]}
    )
    caps_gpu = json.dumps({"engines": [], "runtimes": ["native", "container"]})
    await env.execute(
        "INSERT INTO nodes (id, cluster_id, name, status, os, arch, proxy_address, capabilities,"
        " cache_dir,"
        " cache_limit_bytes) VALUES ($1,$2,'mac-1','online','macos','aarch64','10.0.0.5',$3::jsonb,"
        " '/var/gd/cache', $4)",
        w.ids["mac"],
        w.ids["cluster"],
        caps_mac,
        20 * GIB,
    )
    await env.execute(
        "INSERT INTO nodes (id, cluster_id, name, status, os, arch, proxy_address, capabilities)"
        " VALUES ($1,$2,'gpu-1','online','linux','x86_64','10.0.0.6',$3::jsonb)",
        w.ids["gpu"],
        w.ids["cluster"],
        caps_gpu,
    )
    await env.execute(
        "INSERT INTO devices (id, node_id, vendor, index, name, vram_total_bytes,"
        " is_unified_memory, health)"
        " VALUES ($1,$2,'apple',0,'Apple M3 Max',$3,true,'healthy')",
        w.ids["apple0"],
        w.ids["mac"],
        48 * GIB,
    )
    for i, key in enumerate(("nv0", "nv1")):
        await env.execute(
            "INSERT INTO devices (id, node_id, vendor, index, name, vram_total_bytes, health,"
            " numa_node,"
            " driver_version) VALUES ($1,$2,'nvidia',$3,'H100',$4,'healthy',0,'580.65.06')",
            w.ids[key],
            w.ids["gpu"],
            i,
            80 * GIB,
        )
    async with w.session() as s:
        created = await EngineService.for_session(s).seed_builtin_versions()
        assert created == 11
        assert await EngineService.for_session(s).seed_builtin_versions() == 0
        # These scenarios model llama.cpp as operator-installed only (on the Mac): drop the
        # pinned downloads so gpu-1 stays ineligible for it.
        await s.execute(
            text(
                "UPDATE engine_versions SET artifacts = '{}'::jsonb FROM engines e"
                " WHERE e.id = engine_versions.engine_id AND e.name = 'llama_cpp'"
            )
        )
        if OLLAMA_QWEN.exists():
            catalog = CatalogService.for_session(s)
            qwen = await catalog.create_model(
                ModelCreate(
                    name="qwen3-0.6b",
                    source="local_path",
                    repo_id=str(OLLAMA_QWEN),
                    type="chat",
                    aliases=["qwen3"],
                )
            )
            w.ids["qwen"] = qwen.id
            w.ids["qwen_v"] = qwen.default_version.id
            gemma = await catalog.create_model(
                ModelCreate(
                    name="gemma-3-1b",
                    source="local_path",
                    repo_id=str(LMS_GEMMA),
                    type="chat",
                )
            )
            w.ids["gemma"] = gemma.id
            w.ids["gemma_v"] = gemma.default_version.id
            if OLLAMA_TINY.exists():
                tiny = await catalog.create_model(
                    ModelCreate(
                        name="tinyllama",
                        source="local_path",
                        repo_id=str(OLLAMA_TINY),
                        type="chat",
                        priority="evictable",
                        initial_version=ModelVersionCreate(version="latest"),
                    )
                )
                w.ids["tiny"] = tiny.id
            if LMS_NOMIC.exists():
                nomic = await catalog.create_model(
                    ModelCreate(
                        name="nomic-embed",
                        source="local_path",
                        repo_id=str(LMS_NOMIC),
                        type="embedding",
                    )
                )
                w.ids["nomic"] = nomic.id
        await s.commit()
    return w


async def _create(w: World, **kw):
    from app.schemas.deployments_schema import DeploymentCreate

    async with w.session() as s:
        svc = w.deployments(s)
        out = await svc.create_deployment(DeploymentCreate(**kw))
        await s.commit()
    return out


def _load_result(port: int):
    def make(i, row):
        args = (row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"]))[
            "args"
        ]
        return {
            "instance_id": args["instance_id"],
            "port": port + i,
            "pid": 4000 + i,
            "served_model_name": args["model"]["served_model_name"],
            "ready_at": "2026-11-17T00:00:00Z",
            "load_seconds": 3.2,
            "vram_bytes_used": 900_000_000,
        }

    return make


# ---------------------------------------------------------------- catalog


@needs_local_models
async def test_registers_real_local_models(world):
    rows = await world.env.sql(
        "SELECT m.name, m.family, m.type, v.format, v.status, v.total_size_bytes, v.config,"
        " f.path, f.sha256 FROM models m JOIN model_versions v ON v.model_id=m.id"
        " JOIN model_files f ON f.model_version_id=v.id WHERE f.is_required ORDER BY m.name, f.path"
    )
    by = {}
    for r in rows:
        by.setdefault(r["name"], []).append(r)
    qwen = by["qwen3-0.6b"][0]
    assert qwen["format"] == "gguf" and qwen["family"] == "qwen3"
    assert qwen["sha256"] and len(qwen["sha256"]) == 64
    cfg = json.loads(qwen["config"]) if isinstance(qwen["config"], str) else qwen["config"]
    assert cfg["n_layers"] == 28 and cfg["source_kind"] == "ollama_blob"
    assert by["gemma-3-1b"][0]["format"] == "mlx"
    if "nomic-embed" in by:
        assert by["nomic-embed"][0]["type"] == "embedding"
    if "tinyllama" in by:
        assert by["tinyllama"][0]["format"] == "gguf"


# ---------------------------------------------------------------- engines


@needs_local_models
async def test_auto_pick_and_compat_records(world):
    from app.schemas.engines_schema import AutoPickRequest, CompatCheckRequest
    from app.services.engines.service import EngineService

    async with world.session() as s:
        svc = EngineService.for_session(s)
        pick = await svc.auto_pick(
            AutoPickRequest(model_version_id=world.ids["qwen_v"], node_id=world.ids["mac"])
        )
        assert pick.engine_version.engine_name.value == "llama_cpp"
        assert pick.check.result.value == "compatible"
        assert pick.suggested_launch_flags.get("jinja") is True
        mlx = await svc.auto_pick(
            AutoPickRequest(model_version_id=world.ids["gemma_v"], node_id=world.ids["mac"])
        )
        assert mlx.engine_version.engine_name.value == "mlx"
        checks = await svc.run_compat_check(
            CompatCheckRequest(model_version_id=world.ids["qwen_v"], node_id=world.ids["gpu"])
        )
        by = {c.engine_name.value: c for c in checks}
        assert by["vllm"].result.value == "incompatible"
        assert any(r.code == "format_unsupported" for r in by["vllm"].reasons)
        rows, total = await svc.list_checks(
            page=1, page_size=100, model_version_id=world.ids["qwen_v"]
        )
        assert total >= 11
        await s.commit()


# ------------------------------------------------------------ deployments


@needs_local_models
async def test_deploy_load_ready_and_routing_table(world):
    from app.services.deployments.routing_publisher import (
        build_routing_table,
        publish_routing_table,
    )

    dep = await _create(
        world,
        name="qwen",
        model_id=world.ids["qwen"],
        cluster_id=world.ids["cluster"],
        context_size=8192,
    )
    assert dep.engine_name.value == "llama_cpp" and dep.tool_parser
    insts = await world.instances(dep.id)
    assert len(insts) == 1 and insts[0]["state"] == "loading"
    assert insts[0]["node_id"] == world.ids["mac"]  # gpu-1 has no llama.cpp installed
    assert insts[0]["device_ids"] == [world.ids["apple0"]]
    starts = await world.commands("start_engine")
    loads = await world.commands("load_model")
    assert len(starts) == 1 and len(loads) == 1
    for row in starts + loads:
        assert_valid_command(row)
    load = (
        json.loads(loads[0]["payload"])
        if isinstance(loads[0]["payload"], str)
        else loads[0]["payload"]
    )
    args = load["args"]
    assert args["engine_process_id"] and args["model"]["source"]["kind"] == "local_path"
    assert args["model"]["cache_dir"].startswith("/var/gd/cache/")
    assert (
        args["launch"]["context_size"] == 8192
        and args["engine"]["launch_template"]["router_mode"] is True
    )
    assert args["engine"]["artifact"] is None  # installed on the node (engine_paths wins)

    await world.finish(
        starts[0]["id"],
        "succeeded",
        {"engine_process_id": args["engine_process_id"], "port": 10240},
    )
    await world.finish_all("load_model", result_for=_load_result(10240))
    summary = await world.tick()
    assert summary.applied >= 1
    inst = (await world.instances(dep.id))[0]
    assert inst["state"] == "ready" and inst["port"] == 10240 and inst["pid"] == 4000
    assert inst["health"] == "healthy"
    dep_row = (await world.env.sql("SELECT status FROM deployments WHERE id=$1", dep.id))[0]
    assert dep_row["status"] == "running"

    from app.services.metrics import live_cache

    now = datetime.now(timezone.utc).isoformat()
    await live_cache.put(
        "engine",
        inst["id"],
        {
            "ts": now,
            "instance_id": str(inst["id"]),
            "active_sessions": 3,
            "waiting_sessions": 1,
        },
    )
    async with world.session() as s:
        table = await build_routing_table(s)
        version = await publish_routing_table(s)
        shown = await world.deployments(s).get_deployment(dep.id)
    assert shown.live.active_sessions == 3 and shown.instances[0].live.waiting_sessions == 1
    assert table["models"]["qwen3-0.6b"]["instances"][0]["active_sessions"] == 3
    entry = table["models"]["qwen3-0.6b"]
    assert entry["aliases"] == ["qwen3"] and entry["deployment_id"] == str(dep.id)
    ri = entry["instances"][0]
    assert ri["accepting"] and ri["state"] == "ready" and ri["proxy_url"] == "https://10.0.0.5:9443"
    assert ri["served_model_name"] == inst["served_model_name"]
    assert table["nodes"][str(world.ids["mac"])]["name"] == "mac-1"
    assert version and version >= 1

    from app.db.redis.client import get_redis

    redis = await get_redis()
    stored = json.loads(await redis.get(f"{world.env.prefix}routing:v1"))
    assert stored["version"] == version and "qwen3-0.6b" in stored["models"]
    # The API layer's parser accepts what we publish.
    from app.services.serving.routing import RoutingTable

    rt = RoutingTable()
    rt.load_from_dict(stored)
    assert rt.get("qwen3").ready_instances


@needs_local_models
async def test_two_deployments_of_one_model_are_addressable(world):
    """FULL-TEST #20: two deployments of one model shared one served name and could not be
    targeted one by one. Each deployment is now routable by its own name; /v1/models lists
    the model plus each deployment name when the model name is shared."""
    from app.services.deployments.routing_publisher import build_routing_table
    from app.services.serving import models_listing
    from app.services.serving.routing import RoutingTable

    one = await _create(world, name="qwen-one", model_id=world.ids["qwen"], context_size=4096)
    await world.finish_all("start_engine")
    await world.finish_all("load_model", result_for=_load_result(10700))
    await world.tick()
    async with world.session() as s:
        table = await build_routing_table(s)
    assert table["models"]["qwen-one"]["listed"] is False  # resolvable, not listed
    rt = RoutingTable()
    rt.load_from_dict(table)
    assert [e.name for e in rt.list_models()] == ["qwen3-0.6b"]
    assert rt.get("qwen-one").deployment_id == str(one.id)

    two = await _create(world, name="qwen-two", model_id=world.ids["qwen"], context_size=2048)
    await world.finish_all("start_engine")
    await world.finish_all("load_model", result_for=_load_result(10710))
    await world.tick()
    async with world.session() as s:
        table = await build_routing_table(s)
    shared = table["models"]["qwen3-0.6b"]
    assert len(shared["instances"]) == 2
    assert {d["name"] for d in shared["deployments"]} == {"qwen-one", "qwen-two"}
    for dep in (one, two):
        entry = table["models"][dep.name]
        assert entry["listed"] is True and entry["model_name"] == "qwen3-0.6b"
        assert [i["deployment_id"] for i in entry["instances"]] == [str(dep.id)]
        assert entry["deployment_id"] == str(dep.id)
    assert table["models"]["qwen-two"]["context_size"] == 2048

    rt = RoutingTable()
    rt.load_from_dict(table)
    assert [e.name for e in rt.list_models()] == ["qwen-one", "qwen-two", "qwen3-0.6b"]
    for _ in range(4):  # the deployment name pins the deployment; the model name balances
        target = rt.resolve("QWEN-TWO", "chat/completions", {})
        assert target.instance.deployment_id == str(two.id)
        assert target.model.deployment_id == str(two.id)
    picked = {rt.resolve("qwen3", "chat/completions", {}).instance.deployment_id}
    rt.models["qwen3-0.6b"].traffic["strategy"] = "round_robin"
    picked |= {rt.resolve("qwen3", "chat/completions", {}).instance.deployment_id for _ in range(2)}
    assert picked == {str(one.id), str(two.id)}

    import app.services.serving.models_listing as listing_mod

    original = listing_mod.get_routing_table
    listing_mod.get_routing_table = lambda: rt
    try:
        listed = models_listing.openai_models()["data"]
    finally:
        listing_mod.get_routing_table = original
    by_id = {m["id"]: m for m in listed}
    assert {"qwen3-0.6b", "qwen3", "qwen-one", "qwen-two"} <= set(by_id)
    assert by_id["qwen-two"]["gpudeck"]["model"] == "qwen3-0.6b"
    assert len(by_id["qwen3-0.6b"]["gpudeck"]["deployments"]) == 2


@needs_local_models
async def test_scale_spread_then_down(world):
    await world.env.execute(
        "UPDATE nodes SET capabilities = $2::jsonb WHERE id=$1",
        world.ids["gpu"],
        json.dumps({"engines": [{"name": "llama_cpp"}], "runtimes": ["native"]}),
    )
    from app.schemas.deployments_schema import ScaleRequest

    dep = await _create(world, name="qwen-s", model_id=world.ids["qwen"], context_size=4096)
    await world.finish_all("start_engine")
    await world.finish_all("load_model", result_for=_load_result(10240))
    await world.tick()
    async with world.session() as s:
        refs = await world.deployments(s).scale(dep.id, ScaleRequest(replicas=3))
        await s.commit()
    assert len(refs.command_ids) >= 2
    insts = await world.instances(dep.id, ("pending", "loading", "ready"))
    assert len(insts) == 3
    nodes = {i["node_id"] for i in insts}
    assert nodes == {world.ids["mac"], world.ids["gpu"]}  # spread
    gpu_devs = {d for i in insts if i["node_id"] == world.ids["gpu"] for d in i["device_ids"]}
    assert gpu_devs == {world.ids["nv0"], world.ids["nv1"]}
    for row in await world.commands("load_model"):
        assert_valid_command(row)
    await world.finish_all("start_engine")
    await world.finish_all("load_model", result_for=_load_result(10300))
    await world.tick()
    assert len(await world.instances(dep.id, ("ready",))) == 3

    async with world.session() as s:
        refs = await world.deployments(s).scale(
            dep.id, ScaleRequest(replicas=1, drain_timeout_seconds=30)
        )
        await s.commit()
    unloads = await world.commands("unload_model", "pending")
    assert len(unloads) == 2 and len(refs.command_ids) == 2
    for row in unloads:
        assert_valid_command(row)
    oldest = (await world.instances(dep.id))[0]
    assert oldest["state"] == "ready"  # the newest replicas go first
    await world.finish_all("unload_model")
    await world.tick()
    assert len(await world.instances(dep.id, ("stopped",))) == 2
    assert (await world.env.sql("SELECT status FROM deployments WHERE id=$1", dep.id))[0][
        "status"
    ] == "running"


@needs_local_models
async def test_stop_start_delete(world):
    dep = await _create(world, name="gemma", model_id=world.ids["gemma"])
    assert dep.engine_name.value == "mlx"
    loads = await world.commands("load_model")
    assert len(loads) == 1 and not await world.commands("start_engine")  # mlx: dedicated process
    assert_valid_command(loads[0])
    await world.finish_all("load_model", result_for=_load_result(10250))
    await world.tick()
    async with world.session() as s:
        svc = world.deployments(s)
        await svc.stop(dep.id)
        await s.commit()
    assert (await world.instances(dep.id))[0]["state"] == "unloading"
    await world.finish_all("unload_model")
    await world.tick()
    row = (
        await world.env.sql("SELECT status, desired_running FROM deployments WHERE id=$1", dep.id)
    )[0]
    assert row["status"] == "stopped" and row["desired_running"] is False
    await world.tick()
    assert len(await world.instances(dep.id, ("pending", "loading", "ready"))) == 0  # stays stopped

    async with world.session() as s:
        refs = await world.deployments(s).start(dep.id)
        await s.commit()
    assert refs.command_ids
    await world.finish_all("load_model", result_for=_load_result(10260))
    await world.tick()
    async with world.session() as s:
        await world.deployments(s).delete_deployment(dep.id)
        await s.commit()
    await world.finish_all("unload_model")
    await world.tick()
    row = (await world.env.sql("SELECT status, deleted_at FROM deployments WHERE id=$1", dep.id))[0]
    assert row["deleted_at"] is not None and row["status"] == "stopped"
    async with world.session() as s:
        rows, total = await world.deployments(s).list_deployments()
    assert all(r.id != dep.id for r in rows)
    # the jobs-process entry point: locked tick on its own session
    from app.services.deployments.reconcile import run_reconcile_tick
    from app.workers.tasks.deployments_tasks import _prune

    summary = await run_reconcile_tick()
    assert "deployments" in summary and not summary["errors"]
    assert await _prune() == 0  # stopped rows are younger than the retention window


@needs_local_models
async def test_rolling_update_surges_one(world):
    from app.schemas.deployments_schema import DeploymentUpdate

    dep = await _create(world, name="qwen-r", model_id=world.ids["qwen"], context_size=4096)
    await world.finish_all("start_engine")
    await world.finish_all("load_model", result_for=_load_result(10240))
    await world.tick()
    old = (await world.instances(dep.id, ("ready",)))[0]
    async with world.session() as s:
        out = await world.deployments(s).update_deployment(
            dep.id, DeploymentUpdate(launch_flags={"parallel": 6})
        )
        await s.commit()
    assert out.launch_flags == {"parallel": 6}
    live = await world.instances(dep.id, ("pending", "loading", "ready"))
    assert len(live) == 2  # surge
    new = next(i for i in live if i["id"] != old["id"])
    loads = await world.commands("load_model", "pending")
    payload = (
        loads[-1]["payload"]
        if isinstance(loads[-1]["payload"], dict)
        else json.loads(loads[-1]["payload"])
    )
    assert payload["args"]["launch"]["flags"]["parallel"] == 6
    assert payload["args"]["engine_process_id"] == str(
        old["engine_process_id"]
    )  # reuses the router
    assert not await world.commands("start_engine", "pending")
    await world.tick()
    assert (await world.env.sql("SELECT state FROM instances WHERE id=$1", old["id"]))[0][
        "state"
    ] == "ready"
    await world.finish_all("load_model", result_for=_load_result(10270))
    await world.tick()
    states = {r["id"]: r["state"] for r in await world.instances(dep.id)}
    assert states[new["id"]] == "ready" and states[old["id"]] == "unloading"
    await world.finish_all("unload_model")
    await world.tick()
    assert (await world.env.sql("SELECT status FROM deployments WHERE id=$1", dep.id))[0][
        "status"
    ] == "running"


@needs_local_models
async def test_rolling_update_surge_does_not_count_replaced_replica(world, monkeypatch):
    """A surge replaces the oldest stale replica: that replica and the ones retired in the same
    tick are left out of the spread count, so with one replica per node the replacement goes
    to the node whose replica it replaces instead of piling onto the other node."""
    from app.schemas.deployments_schema import DeploymentUpdate
    from app.services.deployments.service import DeploymentService

    await world.env.execute(
        "UPDATE nodes SET capabilities = $2::jsonb WHERE id=$1",
        world.ids["gpu"],
        json.dumps({"engines": [{"name": "llama_cpp"}], "runtimes": ["native"]}),
    )
    dep = await _create(
        world, name="qwen-rs", model_id=world.ids["qwen"], context_size=4096, replicas=2
    )
    await world.finish_all("start_engine")
    await world.finish_all("load_model", result_for=_load_result(10240))
    await world.tick()
    before = sorted(await world.instances(dep.id, ("ready",)), key=lambda i: i["node_id"])
    assert {i["node_id"] for i in before} == {world.ids["mac"], world.ids["gpu"]}
    oldest = before[0]
    await world.env.execute(
        "UPDATE instances SET created_at = created_at - interval '1 hour' WHERE id=$1",
        oldest["id"],
    )
    seen: List[List[uuid.UUID]] = []
    real = DeploymentService.placement_request

    def spy(self, ctx, estimate, replicas, **kw):
        seen.append([i.id for i in kw.get("existing", ())])
        return real(self, ctx, estimate, replicas, **kw)

    monkeypatch.setattr(DeploymentService, "placement_request", spy)
    async with world.session() as s:
        await world.deployments(s).update_deployment(
            dep.id, DeploymentUpdate(launch_flags={"parallel": 3})
        )
        await s.commit()
    await world.tick()
    assert seen, "the surge was planned"
    assert oldest["id"] not in seen[0]  # the replica the first surge replaces
    assert before[1]["id"] in seen[0]  # the other one still counts
    for step in range(6):
        await world.finish_all("start_engine")
        await world.finish_all("load_model", result_for=_load_result(10500 + step * 10))
        await world.tick()
        await world.finish_all("unload_model")
        await world.tick()
    after = await world.instances(dep.id, ("ready",))
    assert len(after) == 2 and not {i["id"] for i in after} & {i["id"] for i in before}
    assert {i["node_id"] for i in after} == {world.ids["mac"], world.ids["gpu"]}


@needs_local_models
async def test_node_drain_plans_move_swaps(world):
    """Node drain with move_instances: one move swap per instance to another node, an unload
    swap when nothing else has room (``app.services.swap.node_drain.plan_node_drain``).
    """
    from app.services.nodes.service import _node_drain_planner
    from app.services.swap.node_drain import plan_node_drain

    assert _node_drain_planner() is plan_node_drain
    await world.env.execute(
        "UPDATE nodes SET capabilities = $2::jsonb WHERE id=$1",
        world.ids["gpu"],
        json.dumps({"engines": [{"name": "llama_cpp"}], "runtimes": ["native"]}),
    )
    dep = await _create(world, name="qwen-d", model_id=world.ids["qwen"], context_size=4096)
    await world.finish_all("start_engine")
    await world.finish_all("load_model", result_for=_load_result(10240))
    await world.tick()
    inst = (await world.instances(dep.id, ("ready",)))[0]
    other = world.ids["gpu"] if inst["node_id"] == world.ids["mac"] else world.ids["mac"]
    await world.env.execute("UPDATE nodes SET status='draining' WHERE id=$1", inst["node_id"])
    async with world.session() as s:
        ids = await plan_node_drain(
            s, inst["node_id"], [inst["id"]], timeout_seconds=60, reason="test"
        )
        await s.commit()
    rows = await world.env.sql("SELECT * FROM swap_operations WHERE id = ANY($1)", ids)
    assert len(rows) == 1 and rows[0]["kind"] == "move"
    plan = rows[0]["plan"] if isinstance(rows[0]["plan"], dict) else json.loads(rows[0]["plan"])
    assert plan["unload"] == [str(inst["id"])]
    assert plan["load"]["node_id"] == str(other) and plan["reason"] == "node drain: test"
    async with world.session() as s:  # a running swap owns the instance: nothing new
        assert await plan_node_drain(s, inst["node_id"], [inst["id"]], timeout_seconds=60) == []
    await world.env.execute("UPDATE swap_operations SET state='failed' WHERE id = ANY($1)", ids)
    await world.env.execute("UPDATE nodes SET status='offline' WHERE id=$1", other)
    async with world.session() as s:
        ids = await plan_node_drain(s, inst["node_id"], [inst["id"]], timeout_seconds=60)
        await s.commit()
    rows = await world.env.sql("SELECT kind FROM swap_operations WHERE id = ANY($1)", ids)
    assert [r["kind"] for r in rows] == ["unload"]  # no room elsewhere


@needs_local_models
async def test_failures_cleanup_and_crash_loop(world, monkeypatch):
    monkeypatch.setattr(settings, "DEPLOYMENT_ERROR_GRACE_SECONDS", 0, raising=False)
    dep = await _create(world, name="gemma-f", model_id=world.ids["gemma"])
    for attempt in range(3):
        rows = await world.commands("load_model", "pending")
        assert rows, f"attempt {attempt}: a replacement load was sent"
        for r in rows:
            await world.finish(r["id"], "failed", code="engine_start_failed")
        await world.tick()
        await world.finish_all("unload_model")  # cleanup of the errored instance
        await world.tick()
    row = (
        await world.env.sql("SELECT status, status_message FROM deployments WHERE id=$1", dep.id)
    )[0]
    assert row["status"] == "failed" and "crash loop" in row["status_message"]
    assert not await world.commands("load_model", "pending")
    errored = await world.instances(dep.id, ("error", "stopped"))
    assert len(errored) == 3
    assert all(e["state"] == "stopped" for e in errored)  # cleaned up
    async with world.session() as s:
        await world.deployments(s).start(dep.id)
        await s.commit()
    assert await world.commands("load_model", "pending")  # reset after an explicit start


@needs_local_models
async def test_update_of_errored_deployment_keeps_its_replacement(world):
    """FULL-TEST #14: PATCH of a deployment whose only instance is in error. The errored
    instance (old spec, still inside the error grace period and re-reported by the agent)
    must not count as a replica: the update's replacement is kept and loaded, the errored
    one is cleaned up — before, the reconciler unloaded the replacement every tick."""
    from app.db.postgres.repositories.inventory_repository import InventoryRepository
    from app.schemas.deployments_schema import DeploymentUpdate

    dep = await _create(world, name="gemma-u", model_id=world.ids["gemma"])
    rows = await world.commands("load_model", "pending")
    for r in rows:
        await world.finish(r["id"], "failed", code="engine_start_failed")
    await world.tick()
    failed = (await world.instances(dep.id, ("error",)))[0]
    before = failed["state_changed_at"]
    async with world.session() as s:  # the agent repeats engine_crashed for it
        await InventoryRepository(s).apply_instance_event(failed["id"], state="error")
        await s.commit()
    row = await world.env.sql("SELECT state_changed_at FROM instances WHERE id=$1", failed["id"])
    assert row[0]["state_changed_at"] == before  # repeated event: grace not restarted
    assert not await world.commands("load_model", "pending")  # held inside the grace period

    async with world.session() as s:
        await world.deployments(s).update_deployment(
            dep.id, DeploymentUpdate(launch_flags={"max_tokens": 256})
        )
        await s.commit()
    await world.tick()
    await world.tick()
    live = await world.instances(dep.id, ("pending", "loading", "ready", "unloading"))
    assert len(live) == 1 and live[0]["id"] != failed["id"]
    new = live[0]
    assert new["state"] in ("pending", "loading")
    unloads = await world.commands("unload_model")
    targets = {
        (u["payload"] if isinstance(u["payload"], dict) else json.loads(u["payload"]))["args"][
            "instance_id"
        ]
        for u in unloads
    }
    assert str(new["id"]) not in targets  # the replacement is never unloaded
    assert str(failed["id"]) in targets  # the failed instance is cleaned up at once
    await world.finish_all("load_model", result_for=_load_result(10600))
    await world.finish_all("unload_model")
    await world.tick()
    states = {r["id"]: r["state"] for r in await world.instances(dep.id)}
    assert states[new["id"]] == "ready" and states[failed["id"]] == "stopped"
    assert (await world.env.sql("SELECT status FROM deployments WHERE id=$1", dep.id))[0][
        "status"
    ] == "running"


@needs_local_models
async def test_preview_capacity_evictions_and_pinning(world):
    from app.core.exceptions import ConflictError
    from app.schemas.deployments_schema import DeploymentSpec, PlacementPreviewRequest

    async with world.session() as s:
        svc = world.deployments(s)
        preview = await svc.preview(
            PlacementPreviewRequest(
                deployment=DeploymentSpec(
                    name="big", model_id=world.ids["qwen"], context_size=2_000_000
                )
            )
        )
        assert not preview.feasible and preview.rejected
        assert any("needs" in r.reason for r in preview.rejected)
        assert any("not installed" in r.reason for r in preview.rejected)  # gpu-1
        ok = await svc.preview(
            PlacementPreviewRequest(
                deployment=DeploymentSpec(
                    name="ok", model_id=world.ids["qwen"], context_size=4096, replicas=2
                )
            )
        )
        assert ok.feasible and len(ok.placements) == 2 and ok.compat is not None
        assert ok.placements[0].node_name == "mac-1" and ok.placements[0].estimated_vram_bytes > 0
    from app.services.metrics import live_cache

    await live_cache.put(
        "gpu",
        world.ids["apple0"],
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "device_id": str(world.ids["apple0"]),
            "memory_used_bytes": 47 * GIB,
        },
    )
    async with world.session() as s:
        busy = await world.deployments(s).preview(
            PlacementPreviewRequest(
                deployment=DeploymentSpec(name="ok", model_id=world.ids["qwen"], context_size=4096)
            )
        )
    assert not busy.feasible  # observed usage on the Mac counts
    await live_cache.put(
        "gpu",
        world.ids["apple0"],
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "device_id": str(world.ids["apple0"]),
            "memory_used_bytes": 1 * GIB,
        },
    )
    if "tiny" in world.ids:
        tiny = await _create(world, name="tiny", model_id=world.ids["tiny"], context_size=2048)
        await world.finish_all("start_engine")
        await world.finish_all("load_model", result_for=_load_result(10280))
        await world.tick()
        # Occupy the Mac so a normal-priority model needs room: reserve most of the device.
        await world.env.execute(
            "INSERT INTO device_slices (device_id, vram_bytes, compute_pct, status)"
            " VALUES ($1,$2,1,'active')",
            world.ids["apple0"],
            46 * GIB,
        )
        async with world.session() as s:
            preview = await world.deployments(s).preview(
                PlacementPreviewRequest(
                    deployment=DeploymentSpec(
                        name="needs-room", model_id=world.ids["qwen"], context_size=4096
                    )
                )
            )
        assert not preview.feasible
        await world.env.execute("DELETE FROM device_slices WHERE instance_id IS NULL")
        assert tiny.id
    async with world.session() as s:
        dep = await _create(
            world,
            name="pinned",
            model_id=world.ids["qwen"],
            context_size=4096,
            start=False,
        )
        assert dep.status.value == "stopped"
        svc = world.deployments(s)
        inst = await svc.place_instance(
            dep.id, node_id=world.ids["mac"], device_ids=[world.ids["apple0"]]
        )
        assert inst.state == "pending" and inst.device_ids == [world.ids["apple0"]]
        assert await svc.stop_instance(inst.id) is None  # never started: just cancelled
        with pytest.raises(ConflictError):
            await svc.place_instance(dep.id, node_id=world.ids["gpu"])  # engine missing there
        await s.commit()


async def test_vllm_tp_pd_split_and_slices(world):
    """Fixture-only hardware path: vLLM PD split / TP=2 on two H100s; slices on llama.cpp."""
    from app.schemas.catalog_schema import ModelCreate
    from app.services.catalog.hf_client import HuggingFaceClient
    from app.services.catalog.service import CatalogService

    tree = [
        {"type": "file", "path": "config.json", "size": 700},
        {
            "type": "file",
            "path": "model-00001-of-00002.safetensors",
            "size": 4 * GIB,
            "lfs": {"oid": "a" * 64, "size": 4 * GIB},
        },
        {
            "type": "file",
            "path": "model-00002-of-00002.safetensors",
            "size": 3 * GIB,
            "lfs": {"oid": "b" * 64, "size": 3 * GIB},
        },
        {"type": "file", "path": "tokenizer.json", "size": 9000},
    ]
    config = {
        "model_type": "llama",
        "num_hidden_layers": 32,
        "num_attention_heads": 32,
        "num_key_value_heads": 8,
        "hidden_size": 4096,
        "max_position_embeddings": 131072,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if "/tree/" in request.url.path:
            return httpx.Response(200, json=tree)
        if request.url.path.endswith("config.json"):
            return httpx.Response(200, json=config)
        return httpx.Response(
            200,
            json={
                "sha": "f" * 40,
                "pipeline_tag": "text-generation",
                "tags": ["safetensors"],
                "config": {},
            },
        )

    async with world.session() as s:
        catalog = CatalogService.for_session(s)
        catalog._hf = HuggingFaceClient(
            "https://huggingface.co", transport=httpx.MockTransport(handler)
        )
        llama = await catalog.create_model(
            ModelCreate(
                name="llama-8b",
                source="huggingface",
                repo_id="acme/llama-8b",
                type="chat",
            )
        )
        await s.commit()
    vllm_ev = (
        await world.env.sql(
            "SELECT v.id FROM engine_versions v JOIN engines e ON e.id=v.engine_id"
            " WHERE e.name='vllm'"
        )
    )[0]["id"]
    dep = await _create(
        world,
        name="llama-pd",
        model_id=llama.id,
        engine_version_id=vllm_ev,
        gpu_count=1,
        replicas=1,
        context_size=8192,
        pd_split=True,
        pd_config={"prefill_replicas": 1, "decode_replicas": 1, "transfer": "nixl"},
        launch_flags={"gpu-memory-utilization": 0.45},
    )
    insts = await world.instances(dep.id)
    assert sorted(i["pd_role"] for i in insts) == ["decode", "prefill"]
    assert all(i["node_id"] == world.ids["gpu"] for i in insts)
    for row in await world.commands("load_model"):
        assert_valid_command(row)
        args = (row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"]))[
            "args"
        ]
        assert args["engine"]["image"].startswith("vllm/vllm-openai:v0.34.0@sha256:") and args[
            "launch"
        ]["pd_role"] in ("prefill", "decode")
        assert (
            args["model"]["source"]["kind"] == "huggingface"
            and args["model"]["source"]["revision"] == "f" * 40
        )
    res = [
        (json.loads(i["resources"]) if isinstance(i["resources"], str) else i["resources"])
        for i in insts
    ]
    assert all(r["vram_bytes_per_device"] >= int(80 * GIB * 0.45) for r in res)

    tp = await _create(
        world,
        name="llama-tp",
        model_id=llama.id,
        engine_version_id=vllm_ev,
        gpu_count=2,
        tp_size=2,
        context_size=8192,
        start=False,
    )
    async with world.session() as s:
        from app.schemas.deployments_schema import PlacementPreviewRequest

        preview = await world.deployments(s).preview(
            PlacementPreviewRequest(deployment_id=tp.id, replicas=1)
        )
    assert not preview.feasible  # the PD pair already holds 45% of each H100 + default 0.9 fraction

    # FULL-TEST #10: the dry run shows what vLLM really reserves (gpu-memory-utilization x
    # device VRAM), not the ~10 GiB model estimate.
    from app.schemas.deployments_schema import DeploymentSpec

    async with world.session() as s:
        frac = await world.deployments(s).preview(
            PlacementPreviewRequest(
                deployment=DeploymentSpec(
                    name="llama-frac",
                    model_id=llama.id,
                    engine_version_id=vllm_ev,
                    context_size=8192,
                    launch_flags={"gpu-memory-utilization": 0.38},
                ),
                replicas=1,
            )
        )
    assert frac.feasible
    cand = frac.placements[0]
    assert cand.estimated_vram_bytes == int(80 * GIB * 0.38)
    assert any("pre-allocates 0.38 of the device" in r for r in cand.reasons)

    await world.env.execute(
        "UPDATE nodes SET capabilities = $2::jsonb WHERE id=$1",
        world.ids["gpu"],
        json.dumps({"engines": [{"name": "llama_cpp"}], "runtimes": ["native", "container"]}),
    )
    if "qwen" in world.ids:
        sl = await _create(
            world,
            name="qwen-slices",
            model_id=world.ids["qwen"],
            context_size=2048,
            replicas=2,
            slice_vram_bytes=4 * GIB,
            slice_compute_pct=25,
            node_selector={"node_ids": [str(world.ids["gpu"])]},
        )
        slices = await world.env.sql("SELECT * FROM device_slices WHERE deployment_id=$1", sl.id)
        assert len(slices) == 2 and {s["status"] for s in slices} == {"reserved"}
        assert all(s["compute_pct"] == 25 and s["vram_bytes"] == 4 * GIB for s in slices)
        for row in await world.commands("load_model", "pending"):
            args = (
                row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
            )["args"]
            if args["deployment_id"] == str(sl.id):
                assert args["devices"][0]["slice"] == {
                    "vram_bytes": 4 * GIB,
                    "compute_pct": 25,
                }
        await world.finish_all("start_engine")
        await world.finish_all("load_model", result_for=_load_result(10290))
        await world.tick()
        slices = await world.env.sql(
            "SELECT status FROM device_slices WHERE deployment_id=$1", sl.id
        )
        assert {s["status"] for s in slices} == {"active"}
        async with world.session() as s:
            await world.deployments(s).stop(sl.id)
            await s.commit()
        await world.finish_all("unload_model")
        await world.tick()
        slices = await world.env.sql(
            "SELECT status FROM device_slices WHERE deployment_id=$1", sl.id
        )
        assert {s["status"] for s in slices} == {"released"}


# ------------------------------------------------------------ model cache


@needs_local_models
async def test_model_cache_prefetch_inventory_and_lru(world, monkeypatch):
    from app.services.deployments.agent_hub_port import DbQueueAgentHub
    from app.services.model_cache.service import ModelCacheService

    async with world.session() as s:
        cache = ModelCacheService.for_session(s, DbQueueAgentHub(s))
        cmd = await cache.prefetch(
            world.ids["mac"], world.ids["qwen_v"], pin=True, pin_reason="layout night"
        )
        await s.commit()
    assert cmd
    rows = await world.commands("prefetch_model")
    assert_valid_command(rows[0])
    items = await world.env.sql("SELECT * FROM node_model_cache WHERE node_id=$1", world.ids["mac"])
    assert items and all(i["state"] == "queued" and i["pinned"] for i in items)
    file_rows = await world.env.sql(
        "SELECT id, size_bytes FROM model_files WHERE model_version_id=$1 AND is_required",
        world.ids["qwen_v"],
    )
    async with world.session() as s:
        cache = ModelCacheService.for_session(s, DbQueueAgentHub(s))
        touched = await cache.apply_inventory(
            world.ids["mac"],
            [
                {
                    "model_file_id": str(f["id"]),
                    "model_version_id": str(world.ids["qwen_v"]),
                    "local_path": "/var/gd/cache/x",
                    "size_bytes": f["size_bytes"],
                    "state": "ready",
                    "sha256_verified": True,
                    "pinned": True,
                    "last_used_at": "2026-11-01T00:00:00Z",
                }
                for f in file_rows
            ],
        )
        assert touched == len(file_rows)
        assert await cache.prefetch(world.ids["mac"], world.ids["qwen_v"]) is None  # already ready
        summary = await cache.summary()
        mac = next(x for x in summary if x.node_id == world.ids["mac"])
        assert mac.used_bytes >= file_rows[0]["size_bytes"]
        await s.commit()
    # LRU: unpin, shrink the limit below usage -> one evict_cache command
    await world.env.execute("UPDATE node_model_cache SET pinned=false")
    await world.env.execute(
        "UPDATE nodes SET cache_limit_bytes=$2 WHERE id=$1",
        world.ids["mac"],
        100_000_000,
    )
    async with world.session() as s:
        cache = ModelCacheService.for_session(s, DbQueueAgentHub(s))
        assert await cache.lru_sweep(world.ids["mac"])
        await s.commit()
    evicts = await world.commands("evict_cache")
    assert evicts and all(assert_valid_command(r) is None for r in evicts)


# ------------------------------------------------------------ admin API


def build_admin_app():
    import importlib

    from app.core.exceptions import (
        BaseAPIException,
        base_exception_handler,
        generic_exception_handler,
        http_exception_handler,
        validation_exception_handler,
    )
    from app.services.catalog.auth import get_current_admin
    from fastapi import FastAPI
    from fastapi.exceptions import RequestValidationError
    from starlette.exceptions import HTTPException as StarletteHTTPException

    app = FastAPI()
    app.add_exception_handler(BaseAPIException, base_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, generic_exception_handler)
    for name in DEPLOYMENT_ROUTES:
        module = importlib.import_module(f"app.api.v1.routes.{name}")
        app.include_router(module.router, prefix=API)
    app.dependency_overrides[get_current_admin] = lambda: {
        "id": None,
        "username": "admin",
    }
    return app


@needs_local_models
async def test_admin_routes_end_to_end(world, monkeypatch):
    from app.services.deployments import agent_hub_port
    from app.services.deployments.agent_hub_port import FakeAgentHub

    hub = FakeAgentHub()
    agent_hub_port.set_agent_hub_override(hub)
    try:
        app = build_admin_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://adm"
        ) as c:
            r = await c.get(f"{API}/engines")
            assert r.status_code == 200 and len(r.json()["data"]) == 11
            r = await c.post(f"{API}/catalog/local/scan", json={"path": str(OLLAMA_QWEN.parent)})
            assert r.status_code == 200 and r.json()["data"][0]["kind"] == "ollama_blob"

            body = {
                "name": "api-qwen",
                "model_id": str(world.ids["qwen"]),
                "context_size": 4096,
            }
            r = await c.post(f"{API}/deployments", json={**body, "dry_run": True})
            assert r.status_code == 200 and r.json()["data"]["feasible"] is True
            r = await c.post(f"{API}/deployments", json=body)
            assert r.status_code == 201, r.text
            dep = r.json()["data"]
            assert dep["engine_name"] == "llama_cpp" and dep["status"] in (
                "deploying",
                "scheduling",
            )
            assert [x.command for x in hub.sent] == ["start_engine", "load_model"]
            r = await c.post(f"{API}/deployments", json=body)
            assert r.status_code == 409

            r = await c.get(f"{API}/deployments", params={"search": "api-q", "engine": "llama_cpp"})
            assert r.json()["meta"]["total"] == 1
            inst_id = dep["instances"][0]["id"]
            # play the agent: mark the instance ready like the fleet domain's inventory would
            await world.env.execute(
                "UPDATE instances SET state='ready', health='healthy', port=10240 WHERE id=$1",
                uuid.UUID(inst_id),
            )
            r = await c.patch(f"{API}/instances/{inst_id}", json={"traffic_weight": 250})
            assert r.status_code == 200 and r.json()["data"]["traffic_weight"] == 250
            r = await c.put(
                f"{API}/deployments/{dep['id']}/traffic",
                json={"weights": {inst_id: 300}, "strategy": "weighted"},
            )
            assert (
                r.status_code == 200
                and r.json()["data"]["traffic_config"]["strategy"] == "weighted"
            )
            r = await c.get(f"{API}/instances", params={"deployment_id": dep["id"]})
            assert r.json()["data"][0]["traffic_weight"] == 300
            r = await c.get(f"{API}/instances/{inst_id}/sessions")
            assert r.status_code == 200 and r.json()["data"]["instance_id"] == inst_id
            r = await c.get(f"{API}/deployments/{dep['id']}/swaps")
            assert r.status_code == 200 and r.json()["data"] == []
            r = await c.post(
                f"{API}/deployments/preview",
                json={"deployment_id": dep["id"], "replicas": 2},
            )
            assert r.status_code == 200 and len(r.json()["data"]["placements"]) == 1
            r = await c.post(f"{API}/instances/{inst_id}/drain", json={"timeout_seconds": 30})
            assert r.status_code == 202 and hub.sent[-1].command == "drain_instance"
            r = await c.post(f"{API}/instances/{inst_id}/restart", json={"timeout_seconds": 30})
            assert r.status_code == 202 and hub.sent[-1].command == "restart_instance"
            await world.env.execute(
                "UPDATE instances SET state='ready' WHERE id=$1", uuid.UUID(inst_id)
            )

            # benchmarks
            r = await c.post(
                f"{API}/benchmarks",
                json={
                    "name": "qwen-bench",
                    "deployment_id": dep["id"],
                    "concurrency_levels": [1, 2, 4],
                    "workload": {
                        "input_tokens": 32,
                        "output_tokens": 8,
                        "requests_per_level": 6,
                    },
                    "target_slo": {"ttft_ms_p95": 2000},
                    "duration_seconds": 30,
                },
            )
            assert r.status_code == 201, r.text
            bench = r.json()["data"]
            monkeypatch.setattr(settings, "BENCHMARK_API_KEY", "sk-test", raising=False)
            monkeypatch.setattr(
                settings, "BENCHMARK_TARGET_URL", "http://serving.test", raising=False
            )
            enq = []
            monkeypatch.setattr(
                "app.services.benchmark.service._enqueue",
                lambda rid: enq.append(rid) or True,
            )
            r = await c.post(f"{API}/benchmarks/{bench['id']}/start", json={})
            assert r.status_code == 202, r.text
            run = r.json()["data"]
            assert run["status"] == "pending" and enq == [uuid.UUID(run["id"])]
            r = await c.post(f"{API}/benchmarks/{bench['id']}/start", json={})
            assert r.status_code == 409  # one active run per deployment

            from app.services.benchmark.service import BenchmarkService
            from tests.unit.test_benchmark import fake_openai

            transport, _ = fake_openai(tokens=8)
            async with world.session() as s:
                out = await BenchmarkService.for_session(s).execute_run(
                    uuid.UUID(run["id"]), transport=transport
                )
                await s.commit()
            assert out.status.value == "completed", out.error_message
            assert [x["concurrency"] for x in out.results] == [1, 2, 4]
            assert out.best_concurrency == 4 and out.slo_met is True
            assert out.recommendation["per_instance_concurrency"] == 4
            r = await c.get(f"{API}/benchmark-runs/{run['id']}/export")
            assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
            assert r.text.splitlines()[0].startswith("variant,concurrency")
            assert len(r.text.strip().splitlines()) == 4
            r = await c.post(
                f"{API}/benchmark-runs/{run['id']}/apply",
                json={"apply_launch_flags": False},
            )
            assert r.status_code == 200, r.text
            assert r.json()["data"]["traffic_config"]["max_concurrent_per_instance"] == 4
            r = await c.get(f"{API}/benchmarks/{bench['id']}")
            assert (
                r.json()["data"]["run_count"] == 1
                and r.json()["data"]["last_run"]["status"] == "completed"
            )
            r = await c.get(f"{API}/benchmarks/{bench['id']}/runs")
            assert r.json()["meta"]["total"] == 1
            r = await c.post(f"{API}/benchmarks/{bench['id']}/auto-tune", json={})
            assert r.status_code == 202, r.text
            tune_run = r.json()["data"]
            r = await c.post(f"{API}/benchmark-runs/{tune_run['id']}/cancel")
            assert r.status_code == 200 and r.json()["data"]["status"] == "cancelled"
            r = await c.delete(f"{API}/benchmarks/{bench['id']}")
            assert r.status_code == 204

            # scale / stop / delete through the API
            r = await c.post(f"{API}/deployments/{dep['id']}/scale", json={"replicas": 0})
            assert r.status_code == 202 and hub.sent[-1].command == "unload_model"
            r = await c.delete(f"{API}/deployments/{dep['id']}")
            assert r.status_code == 202
            r = await c.get(f"{API}/deployments/{dep['id']}")
            assert r.status_code == 404
            for cmd in hub.sent:
                errors = list(COMMAND.iter_errors({"command": cmd.command, "args": cmd.args}))
                assert not errors, (cmd.command, [e.message for e in errors[:3]])
    finally:
        agent_hub_port.set_agent_hub_override(None)


@pytest.mark.integration
async def test_hf_live_call():
    """The one live HF call: public model info, no token."""
    from app.core.exceptions import ExternalServiceError
    from app.services.catalog.hf_client import HuggingFaceClient

    client = HuggingFaceClient("https://huggingface.co", timeout=20)
    try:
        repo = await client.repo("Qwen/Qwen3-0.6B-GGUF")
    except ExternalServiceError:
        pytest.skip("Hugging Face unreachable")
    gguf = next(f for f in repo.files if f.path.endswith(".gguf"))
    assert len(gguf.sha256) == 64 and gguf.size_bytes > 100_000_000


@needs_local_models
async def test_admin_catalog_engine_and_cache_routes(world, monkeypatch):
    from app.services.deployments import agent_hub_port
    from app.services.deployments.agent_hub_port import FakeAgentHub

    hashed = []
    monkeypatch.setattr(
        "app.workers.tasks.catalog_tasks.enqueue_hash_local_version",
        lambda vid: hashed.append(vid),
    )
    hub = FakeAgentHub()
    agent_hub_port.set_agent_hub_override(hub)
    qwen, qwen_v = world.ids["qwen"], world.ids["qwen_v"]
    try:
        app = build_admin_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://adm"
        ) as c:
            r = await c.get(f"{API}/models", params={"search": "qwen"})
            assert r.status_code == 200 and r.json()["data"][0]["name"] == "qwen3-0.6b"
            r = await c.get(f"{API}/models/{qwen}/versions")
            assert r.json()["data"][0]["format"] == "gguf"
            r = await c.get(f"{API}/models/{qwen}/versions/{qwen_v}/files")
            files = r.json()["data"]
            assert any(f["format"] == "gguf" and f["sha256"] for f in files)
            r = await c.post(f"{API}/models/{qwen}/versions/{qwen_v}/verify")
            assert r.status_code == 200 and r.json()["data"]["status"] in (
                "ready",
                "verifying",
            )
            r = await c.post(
                f"{API}/models",
                json={
                    "name": "no-such",
                    "source": "local_path",
                    "repo_id": "/nonexistent/x",
                    "type": "chat",
                },
            )
            assert r.status_code == 404
            r = await c.patch(f"{API}/models/{qwen}", json={"priority": "always_on"})
            assert r.status_code == 200 and r.json()["data"]["priority"] == "always_on"

            r = await c.post(
                f"{API}/engines/compat-check",
                json={
                    "model_version_id": str(qwen_v),
                    "node_id": str(world.ids["mac"]),
                },
            )
            assert r.status_code == 200
            by = {x["engine_name"]: x for x in r.json()["data"]}
            assert (
                by["llama_cpp"]["result"] == "compatible" and by["vllm"]["result"] == "incompatible"
            )
            r = await c.post(f"{API}/engines/auto-pick", json={"model_version_id": str(qwen_v)})
            assert r.json()["data"]["engine_version"]["engine_name"] == "llama_cpp"
            r = await c.get(f"{API}/engines/compat-checks", params={"result": "incompatible"})
            assert r.json()["meta"]["total"] > 0
            custom = next(
                e for e in (await c.get(f"{API}/engines")).json()["data"] if e["name"] == "custom"
            )
            r = await c.post(
                f"{API}/engines/{custom['id']}/versions",
                json={
                    "version": "my-1",
                    "image": "ghcr.io/acme/serve:1",
                    "launch_template": {"command": ["serve", "--port", "{{port}}"]},
                },
            )
            assert r.status_code == 201, r.text
            ver = r.json()["data"]
            r = await c.patch(
                f"{API}/engines/{custom['id']}/versions/{ver['id']}",
                json={"status": "available", "is_default": True},
            )
            assert r.status_code == 200 and r.json()["data"]["is_default"] is True
            r = await c.patch(f"{API}/engines/{custom['id']}", json={"is_enabled": False})
            assert r.json()["data"]["is_enabled"] is False
            r = await c.delete(f"{API}/engines/{custom['id']}/versions/{ver['id']}")
            assert r.status_code == 204

            r = await c.post(
                f"{API}/model-cache/prefetch",
                json={
                    "node_ids": [str(world.ids["mac"])],
                    "model_version_id": str(qwen_v),
                    "pin": True,
                },
            )
            assert r.status_code == 202 and hub.sent[-1].command == "prefetch_model"
            r = await c.get(f"{API}/model-cache", params={"node_id": str(world.ids["mac"])})
            items = r.json()["data"]
            assert items and items[0]["pinned"] is True and items[0]["state"] == "queued"
            r = await c.get(f"{API}/model-cache/summary")
            assert r.status_code == 200 and r.json()["data"]
            r = await c.get(f"{API}/models/{qwen}/versions/{qwen_v}/cache")
            assert r.status_code == 200 and len(r.json()["data"]) == len(items)
            r = await c.post(f"{API}/model-cache/{items[0]['id']}/unpin")
            assert r.status_code == 200 and r.json()["data"]["pinned"] is False
            r = await c.post(
                f"{API}/model-cache/evict",
                json={
                    "node_ids": [str(world.ids["mac"])],
                    "model_version_id": str(qwen_v),
                },
            )
            assert r.status_code == 202 and hub.sent[-1].command == "evict_cache"
            for cmd in hub.sent:
                errors = list(COMMAND.iter_errors({"command": cmd.command, "args": cmd.args}))
                assert not errors, (cmd.command, [e.message for e in errors[:3]])
    finally:
        agent_hub_port.set_agent_hub_override(None)
