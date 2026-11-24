"""
Integration tests against the real local Postgres and Redis.

Isolation: a scratch schema ``w2ptest_<hex>`` built from every migration
(``tests/schema_helpers.migration_chain``) used through a dedicated engine,
and a scratch Redis prefix; both removed at the end. Agent commands go through
the fleet domain's session-bound hub (rows validated against command.json); tests play the
agent by moving ``agent_commands`` rows to a terminal state.

Covers: SSH GPU instance lifecycle (placement full GPU / slice / queued,
start -> running -> stop -> stopped, TTL auto-stop, inventory sync, metering,
direct-slice adoption), direct device slices (create / capacity / resize /
delete), stable device ids + cross-node PP inventory rules, agent downloads and
the install script over HTTP with a join token.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import asyncpg
import httpx
import pytest
import pytest_asyncio
from app.core.settings import settings
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

pytestmark = [
    pytest.mark.integration,
    pytest.mark.db,
    pytest.mark.asyncio(loop_scope="session"),
]

GIB = 1024**3
API = settings.API_V1_PREFIX


def _dsn() -> str:
    return settings.get_database_url().replace("postgresql+asyncpg://", "postgresql://", 1)


def _key() -> str:
    pub = (
        ed25519.Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
    )
    return pub.decode() + " tester@gpudeck"


class Env:
    def __init__(self) -> None:
        self.tag = uuid.uuid4().hex[:10]
        self.schema = f"w2ptest_{self.tag}"
        self.prefix = f"gpu-deck:w2ptest:{self.tag}:"
        self.audit: List[Dict[str, Any]] = []
        self.ids: Dict[str, uuid.UUID] = {}

    async def sql(self, query: str, *args):
        conn = await asyncpg.connect(_dsn(), server_settings={"search_path": self.schema})
        try:
            return await conn.fetch(query, *args)
        finally:
            await conn.close()

    def session(self):
        from app.db.postgres.connection import get_session_maker

        return get_session_maker()()

    async def finish(self, command_id, state="succeeded", result=None, code=None):
        await self.sql(
            "UPDATE agent_commands SET state=$2::agent_command_state, result=$3::jsonb,"
            " error_code=$4, error_message=$5, completed_at=now() WHERE id=$1",
            command_id,
            state,
            json.dumps(result or {}),
            code,
            None if state == "succeeded" else f"agent said {code or state}",
        )

    async def gpu(self, instance_id) -> Dict[str, Any]:
        rows = await self.sql("SELECT * FROM gpu_instances WHERE id=$1", instance_id)
        return dict(rows[0]) if rows else {}


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def env():
    from app.db.postgres import connection
    from app.db.redis import client as redis_client_module
    from app.db.redis.cache_service import CacheService
    from app.services.audit import audit
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool
    from tests.schema_helpers import migration_chain

    e = Env()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute(f'CREATE SCHEMA "{e.schema}"')
        await conn.execute(f'SET search_path TO "{e.schema}", public')
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
    saved = {"engine": connection.engine, "prefix": settings.REDIS_KEY_PREFIX}
    connection.engine = e.engine
    settings.REDIS_KEY_PREFIX = e.prefix
    await CacheService.reset()
    await redis_client_module.close_redis()
    audit.set_sink(e.audit.append)
    await _seed(e)
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
            settings.REDIS_KEY_PREFIX = saved["prefix"]
            await e.engine.dispose()
            conn = await asyncpg.connect(_dsn())
            await conn.execute(f'DROP SCHEMA IF EXISTS "{e.schema}" CASCADE')
            await conn.close()


async def _seed(e: Env) -> None:
    e.ids = {
        k: uuid.uuid4()
        for k in (
            "cluster",
            "gpu1",
            "gpu2",
            "mac",
            "nv0",
            "nv1",
            "nv2",
            "apple0",
            "tok",
        )
    }
    await e.sql("INSERT INTO clusters (id, name) VALUES ($1, 'lab')", e.ids["cluster"])
    ssh = json.dumps({"runtimes": ["native", "container"], "ssh_instances": True})
    no_ssh = json.dumps({"runtimes": ["native"]})
    for key, name, caps, os_, addr in (
        ("gpu1", "gpu-1", ssh, "linux", "10.0.0.6"),
        ("gpu2", "gpu-2", no_ssh, "linux", "10.0.0.7"),
        ("mac", "mac-1", ssh, "macos", "10.0.0.5"),
    ):
        await e.sql(
            "INSERT INTO nodes (id, cluster_id, name, status, os, arch, proxy_address,"
            " capabilities) VALUES ($1,$2,$3,'online',$4,'x86_64',$5,$6::jsonb)",
            e.ids[key],
            e.ids["cluster"],
            name,
            os_,
            addr,
            caps,
        )
    for key, node, idx in (("nv0", "gpu1", 0), ("nv1", "gpu1", 1), ("nv2", "gpu2", 0)):
        await e.sql(
            "INSERT INTO devices (id, node_id, vendor, index, name, vram_total_bytes, health)"
            " VALUES ($1,$2,'nvidia',$3,'H100',$4,'healthy')",
            e.ids[key],
            e.ids[node],
            idx,
            80 * GIB,
        )
    await e.sql(
        "INSERT INTO devices (id, node_id, vendor, index, name, vram_total_bytes,"
        " is_unified_memory, health) VALUES ($1,$2,'apple',0,'M3',$3,true,'healthy')",
        e.ids["apple0"],
        e.ids["mac"],
        48 * GIB,
    )


def _svc(session):
    from app.services.gpu_instances.service import GpuInstanceService

    return GpuInstanceService.for_session(session)


async def _create(env: Env, **kw):
    from app.schemas.gpu_instances_schema import GpuInstanceCreate

    async with env.session() as s:
        out = await _svc(s).create(GpuInstanceCreate(authorized_keys=[_key()], **kw), actor_id=None)
        await s.commit()
    return out


async def _terminal(command_id) -> bool:
    from app.services.gpu_instances.lifecycle import on_command_terminal

    return await on_command_terminal(command_id)


async def _tick(env: Env):
    from app.services.gpu_instances.lifecycle import tick

    async with env.session() as s:
        return await tick(s)


async def _start_result(env: Env, gi, port=2201):
    return {
        "ssh_instance_id": str(gi.id),
        "container_id": "c0ffee",
        "host_address": "0.0.0.0",
        "host_port": port,
        "ssh_user": "gpudeck",
        "host_key_fingerprint": "SHA256:" + "A" * 43,
        "host_keys": [
            {
                "type": "ssh-ed25519",
                "fingerprint": "SHA256:" + "A" * 43,
                "public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample",
            }
        ],
        "gpu_attached": True,
        "warnings": [],
    }


# ======================================================== GPU instances


async def test_gpu_instance_lifecycle_full_gpu(env: Env):
    from app.core.exceptions import ConflictError
    from tests.integration.test_deployments_integration import COMMAND

    a = await _create(env, name="box-a", gpu_count=1, ttl_seconds=3600)
    assert a.state == "starting" and a.node_id == env.ids["gpu1"]
    cmd = (await env.sql("SELECT * FROM agent_commands WHERE id=$1", a.start_command_id))[0]
    payload = cmd["payload"] if isinstance(cmd["payload"], dict) else json.loads(cmd["payload"])
    assert not list(COMMAND.iter_errors({"command": payload["command"], "args": payload["args"]}))
    assert payload["args"]["container_port"] == 2222 and payload["args"]["ttl_seconds"] >= 3500
    slices = await env.sql("SELECT * FROM device_slices WHERE gpu_instance_id=$1", a.id)
    assert len(slices) == 1 and slices[0]["vram_bytes"] == 80 * GIB

    b = await _create(env, name="box-b", gpu_count=1)
    assert set(b.device_ids).isdisjoint(a.device_ids)
    # gpu-2 has no container runtime, Apple is excluded by default -> nothing left
    with pytest.raises(ConflictError) as exc:
        await _create(env, name="box-c", gpu_count=1)
    assert "NO_CAPACITY" in exc.value.detail
    assert not await env.sql("SELECT 1 FROM gpu_instances WHERE name='box-c'")
    queued = await _create(env, name="box-q", gpu_count=1, wait_for_capacity_seconds=600)
    assert queued.state == "requested"

    # deployments see the reservation: the device is full
    from app.services.scheduler.service import SchedulerService

    async with env.session() as s:
        nodes = await SchedulerService.for_session(s).snapshot(use_observed=False)
    dev = next(d for n in nodes for d in n.devices if d.id == a.device_ids[0])
    assert dev.reserved_bytes == 80 * GIB

    # agent reports success -> running with connection details
    await env.finish(a.start_command_id, result=await _start_result(env, a))
    assert await _terminal(a.start_command_id)
    row = await env.gpu(a.id)
    assert row["state"] == "running" and row["host_port"] == 2201
    async with env.session() as s:
        conn = await _svc(s).connection(a.id)
    assert conn["ssh_command"] == "ssh -p 2201 gpudeck@10.0.0.6" and conn["ready"]
    assert conn["known_hosts"][0].startswith("[10.0.0.6]:2201 ssh-ed25519")

    # pretend it has run for 10 minutes, then stop -> stopping -> stopped (+ metering)
    await env.sql(
        "UPDATE gpu_instances SET running_at = now() - interval '10 minutes',"
        " metered_until = now() - interval '10 minutes' WHERE id=$1",
        a.id,
    )
    async with env.session() as s:
        stopped = await _svc(s).stop(a.id, reason="user")
        await s.commit()
    assert stopped.state == "stopping"
    await env.finish(stopped.stop_command_id, result={"stopped": True})
    assert await _terminal(stopped.stop_command_id)
    row = await env.gpu(a.id)
    assert row["state"] == "stopped" and float(row["gpu_seconds_total"]) >= 599
    assert not await env.sql(
        "SELECT 1 FROM device_slices WHERE gpu_instance_id=$1 AND status <> 'released'",
        a.id,
    )
    usage = await env.sql("SELECT * FROM usage_events WHERE endpoint='gpu_instance'")
    assert usage and usage[0]["model_name"] == "gpu-instance:box-a"
    assert float(usage[0]["gpu_seconds"]) >= 599

    # the queued request is placed on the freed GPU by the next tick
    summary = await _tick(env)
    assert summary["placed"] == 1
    assert (await env.gpu(queued.id))["state"] == "starting"

    # box-b: start failure -> error, alert, reservation released
    await env.finish(b.start_command_id, state="failed", code="image_pull_failed")
    assert await _terminal(b.start_command_id)
    row = await env.gpu(b.id)
    assert row["state"] == "error" and "image_pull_failed" in row["state_message"]
    assert not await env.sql(
        "SELECT 1 FROM device_slices WHERE gpu_instance_id=$1 AND status <> 'released'",
        b.id,
    )
    events = [a["event_type"] for a in env.audit]
    assert "gpu_instance.created" in events and "gpu_instance.running" in events


async def test_gpu_instance_ttl_inventory_and_slice(env: Env):
    q = (await env.sql("SELECT * FROM gpu_instances WHERE name='box-q'"))[0]
    from app.services.gpu_instances.lifecycle import apply_status, sync_from_inventory

    # the agent reports it running (inventory) before the command result arrived
    async with env.session() as s:
        await apply_status(
            s,
            env.ids["gpu1"],
            {
                "ssh_instance_id": str(q["id"]),
                "state": "running",
                "health": "healthy",
                "host_address": "10.0.0.6",
                "host_port": 2202,
                "active_connections": 1,
                "connections_total": 3,
            },
        )
        await s.commit()
    row = await env.gpu(q["id"])
    assert row["state"] == "running" and row["active_connections"] == 1

    # TTL expiry -> stop requested by the tick
    await env.sql(
        "UPDATE gpu_instances SET expires_at = now() - interval '1 second' WHERE id=$1",
        q["id"],
    )
    summary = await _tick(env)
    assert summary["expired"] == 1
    row = await env.gpu(q["id"])
    assert row["state"] == "stopping" and row["stop_reason"] == "ttl_expired"

    # a GPU slice request lands next to nothing else on gpu-1; half the compute
    s1 = await _create(
        env,
        name="slice-1",
        slice_vram_bytes=8 * GIB,
        slice_compute_pct=50,
        ttl_seconds=None,
    )
    assert s1.state == "starting" and s1.slice_compute_pct == 50
    sl = await env.sql("SELECT * FROM device_slices WHERE gpu_instance_id=$1", s1.id)
    assert sl[0]["vram_bytes"] == 8 * GIB and sl[0]["compute_pct"] == 50
    await env.finish(s1.start_command_id, result=await _start_result(env, s1, 2203))
    await _terminal(s1.start_command_id)

    # inventory without it (after the grace period) -> error "lost"
    await env.sql(
        "UPDATE gpu_instances SET running_at = now() - interval '1 hour' WHERE id=$1",
        s1.id,
    )
    async with env.session() as s:
        result = await sync_from_inventory(s, env.ids["gpu1"], [])
        await s.commit()
    assert result["lost"] >= 1
    assert (await env.gpu(s1.id))["state"] == "error"


async def test_direct_slices_and_adoption(env: Env):
    from app.core.exceptions import ConflictError
    from app.schemas.fleet_schema import DeviceSliceCreate, DeviceSliceUpdate
    from app.services.nodes.devices import DeviceService

    nv2 = env.ids["nv2"]
    async with env.session() as s:
        svc = DeviceService(s)
        made = await svc.create_slice(
            nv2,
            DeviceSliceCreate(name="reserved-a", vram_bytes=60 * GIB, compute_pct=60),
            None,
        )
        await s.commit()
    sid = made.slice.id
    async with env.session() as s:
        svc = DeviceService(s)
        with pytest.raises(ConflictError):
            await svc.create_slice(nv2, DeviceSliceCreate(vram_bytes=30 * GIB), None)
        with pytest.raises(ConflictError):
            await svc.create_slice(
                nv2, DeviceSliceCreate(vram_bytes=10 * GIB, compute_pct=50), None
            )
        resized = await svc.update_slice(nv2, sid, DeviceSliceUpdate(vram_bytes=40 * GIB), None)
        assert resized.slice.vram_bytes == 40 * GIB and not resized.rolling_update
        await s.commit()
    async with env.session() as s:
        # 40 GiB + 30 GiB fits now; rolled back to keep the device for the adoption below
        await DeviceService(s).create_slice(
            nv2, DeviceSliceCreate(vram_bytes=30 * GIB, compute_pct=40), None
        )
        await s.rollback()

    # adopt the standalone slice for an SSH GPU instance: gpu-2 has no container runtime
    await env.sql(
        "UPDATE nodes SET capabilities = capabilities || '{\"ssh_instances\": true}'::jsonb"
        " WHERE id=$1",
        env.ids["gpu2"],
    )
    gi = await _create(env, name="on-slice", slice_id=sid)
    assert gi.state == "starting" and gi.device_ids == [nv2]
    assert gi.slice_vram_bytes == 40 * GIB
    row = (await env.sql("SELECT * FROM device_slices WHERE id=$1", sid))[0]
    assert row["gpu_instance_id"] == gi.id
    async with env.session() as s:
        with pytest.raises(ConflictError):
            await DeviceService(s).update_slice(
                nv2, sid, DeviceSliceUpdate(vram_bytes=10 * GIB), None
            )
    # stop before it ran -> the slice goes back to the pool (unbound, reserved)
    async with env.session() as s:
        out = await _svc(s).stop(gi.id)
        await s.commit()
    await env.finish(out.stop_command_id)
    await _terminal(out.stop_command_id)
    row = (await env.sql("SELECT * FROM device_slices WHERE id=$1", sid))[0]
    assert row["gpu_instance_id"] is None and row["status"] == "reserved"
    async with env.session() as s:
        deleted = await DeviceService(s).delete_slice(nv2, sid, False, None)
        await s.commit()
    assert deleted.slice.status == "released"


# ============================================================== inventory


async def test_inventory_stable_ids_and_pp_rules(env: Env):
    from app.db.postgres.repositories.inventory_repository import (
        InventoryRepository,
        stable_device_id,
    )

    node = env.ids["mac"]
    async with env.session() as s:
        mapping = await InventoryRepository(s).reconcile_devices(
            node,
            [
                {
                    "vendor": "apple",
                    "index": 0,
                    "name": "M3",
                    "vram_total_bytes": 48 * GIB,
                },
                {
                    "vendor": "apple",
                    "index": 1,
                    "name": "eGPU?",
                    "vram_total_bytes": GIB,
                },
            ],
        )
        await s.commit()
    assert mapping[0] == env.ids["apple0"]  # pre-existing row keeps its id
    assert mapping[1] == stable_device_id(node, "apple", 1)
    # gone and back: the soft-deleted stable row is revived, same id
    async with env.session() as s:
        repo = InventoryRepository(s)
        await repo.reconcile_devices(
            node,
            [
                {
                    "vendor": "apple",
                    "index": 0,
                    "name": "M3",
                    "vram_total_bytes": 48 * GIB,
                }
            ],
        )
        await s.commit()
    async with env.session() as s:
        mapping = await InventoryRepository(s).reconcile_devices(
            node,
            [
                {
                    "vendor": "apple",
                    "index": 0,
                    "name": "M3",
                    "vram_total_bytes": 48 * GIB,
                },
                {
                    "vendor": "apple",
                    "index": 1,
                    "name": "eGPU",
                    "vram_total_bytes": GIB,
                },
            ],
        )
        await s.commit()
    assert mapping[1] == stable_device_id(node, "apple", 1)

    # cross-node PP instance: head on gpu-1, worker rank on gpu-2
    ids = {k: uuid.uuid4() for k in ("model", "ver", "ev", "dep", "inst")}
    await env.sql(
        "INSERT INTO models (id, name, source, repo_id, type) VALUES ($1,'pp','huggingface',"
        "'org/pp','chat')",
        ids["model"],
    )
    await env.sql(
        "INSERT INTO model_versions (id, model_id, version) VALUES ($1,$2,'v')",
        ids["ver"],
        ids["model"],
    )
    await env.sql(
        "INSERT INTO engine_versions (id, engine_id, version) SELECT $1, id, 'x' FROM engines"
        " WHERE name='vllm'",
        ids["ev"],
    )
    await env.sql(
        "INSERT INTO deployments (id, name, model_id, model_version_id, engine_version_id)"
        " VALUES ($1,'pp',$2,$3,$4)",
        ids["dep"],
        ids["model"],
        ids["ver"],
        ids["ev"],
    )
    shards = [
        {
            "node_id": str(env.ids["gpu1"]),
            "device_ids": [str(env.ids["nv0"])],
            "pp_rank": 0,
        },
        {
            "node_id": str(env.ids["gpu2"]),
            "device_ids": [str(env.ids["nv2"])],
            "pp_rank": 1,
        },
    ]
    await env.sql(
        "INSERT INTO instances (id, deployment_id, node_id, device_ids, state, resources)"
        " VALUES ($1,$2,$3,$4,'loading',$5::jsonb)",
        ids["inst"],
        ids["dep"],
        env.ids["gpu1"],
        [env.ids["nv0"], env.ids["nv2"]],
        json.dumps({"pp_shards": shards}),
    )
    async with env.session() as s:
        repo = InventoryRepository(s)
        # head reports only its own device: device_ids must keep the worker's too
        await repo.reconcile_instances(
            env.ids["gpu1"],
            [
                {
                    "instance_id": str(ids["inst"]),
                    "state": "ready",
                    "health": "healthy",
                    "device_ids": [str(env.ids["nv0"])],
                }
            ],
            [],
        )
        # worker rank reports the same instance from gpu-2
        await repo.reconcile_instances(
            env.ids["gpu2"],
            [
                {
                    "instance_id": str(ids["inst"]),
                    "state": "ready",
                    "health": "healthy",
                    "distributed_rank": 1,
                    "device_ids": [str(env.ids["nv2"])],
                }
            ],
            [],
        )
        # a non-failure event from the worker node does not move the head's state
        assert not await repo.apply_instance_event(
            ids["inst"], state="stopped", node_id=env.ids["gpu2"]
        )
        await s.commit()
    row = (await env.sql("SELECT * FROM instances WHERE id=$1", ids["inst"]))[0]
    assert row["node_id"] == env.ids["gpu1"] and row["state"] == "ready"
    assert set(row["device_ids"]) == {env.ids["nv0"], env.ids["nv2"]}
    res = row["resources"] if isinstance(row["resources"], dict) else json.loads(row["resources"])
    assert res["pp_workers"][str(env.ids["gpu2"])]["rank"] == 1


# ================================================================ HTTP


async def test_agent_downloads_over_http_with_join_token(env: Env, tmp_path, monkeypatch):
    from app.main import app
    from app.services.nodes import agent_downloads as dl
    from app.services.nodes.service import hash_token

    target = tmp_path / "1.1.0" / "aarch64-apple-darwin"
    target.mkdir(parents=True)
    (target / "gpudeck-agent").write_bytes(b"\x7fELF-ish agent")
    monkeypatch.setattr(dl.download_settings, "AGENT_DIST_DIR", str(tmp_path))
    token = "gdj_" + uuid.uuid4().hex + "abcdefgh"
    await env.sql(
        "INSERT INTO join_tokens (cluster_id, token_prefix, token_hash, node_name_hint,"
        " expires_at) VALUES ($1,$2,$3,'mac-2',$4)",
        env.ids["cluster"],
        token[:12],
        hash_token(token),
        datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        url = f"{API}/agent/downloads/latest/aarch64-apple-darwin"
        assert (await client.get(url)).status_code == 401
        resp = await client.get(url, headers={"X-GPUDeck-Join-Token": token})
        assert resp.status_code == 200 and resp.content == b"\x7fELF-ish agent"
        want = hashlib.sha256(resp.content).hexdigest()
        assert resp.headers["x-checksum-sha256"] == want
        sha = await client.get(f"{url}/sha256", params={"token": token})
        assert sha.text.split()[0] == want
        script = await client.get(f"{API}/agent/install.sh", params={"token": token})
        assert script.status_code == 200 and token in script.text
        assert "--name $NODE_NAME" in script.text and "NODE_NAME=mac-2" in script.text
        await env.sql(
            "UPDATE join_tokens SET used_at = now() WHERE token_hash=$1",
            hash_token(token),
        )
        assert (await client.get(url, headers={"X-GPUDeck-Join-Token": token})).status_code == 401
        listing = await client.get(f"{API}/agent/downloads")
        assert listing.status_code == 401  # admin only


async def test_on_demand_start_request(env: Env):
    from app.services.deployments.on_demand import handle_start_request

    assert await handle_start_request(uuid.uuid4()) == "unknown"
    dep = (await env.sql("SELECT id FROM deployments WHERE name='pp'"))[0]["id"]
    # already has a ready replica and desired_running -> left alone
    assert await handle_start_request(dep) == "already_running"
