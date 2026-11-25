"""
Integration (real Postgres + Redis, scratch schema): agent update runs and rolling updates
(agent protocol §14) through the admin service and the orchestrator, with the commands written
by the real ``AgentHub.send_command`` (Redis bus under a private prefix, no hub listening) and
the agent side played by applying command results and ``event(agent_update)`` payloads:

- Update button on a node with the version pre-staged: apply_update right away, the node is
  Updating (no offline alert while it restarts), the phases arrive, succeeded;
- a node without the version staged: stage_update with a download token, staged, then
  apply_update; the node reports a rollback; an alert is raised;
- refusals: an agent that predates self-update (update manually), a Kubernetes node (Helm),
  updates switched off;
- a rolling update skips what cannot be updated and stops at the first rolled-back node;
- the cluster policy: an automatic rolling update starts inside the maintenance window only;
- background pre-staging, bounded and backing off after a failure.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import tarfile
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import asyncpg
import pytest
import pytest_asyncio
from app.core.settings import settings
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

pytestmark = [
    pytest.mark.integration,
    pytest.mark.db,
    pytest.mark.asyncio(loop_scope="session"),
]

TARGET = "x86_64-unknown-linux-gnu"
UPDATE_COMMANDS = ["load_model", "stage_update", "apply_update"]


def _dsn() -> str:
    return settings.get_database_url().replace("postgresql+asyncpg://", "postgresql://", 1)


def _tarball(binary: bytes) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("gpudeck-agent")
        info.size = len(binary)
        tar.addfile(info, io.BytesIO(binary))
    return buf.getvalue()


class Env:
    def __init__(self) -> None:
        self.tag = uuid.uuid4().hex[:10]
        self.schema = f"agentupd_{self.tag}"
        self.prefix = f"gpu-deck:agentupd:{self.tag}:"
        self.audit: List[Dict[str, Any]] = []
        self.alerts: List[Dict[str, Any]] = []

    async def sql(self, query: str, *args):
        conn = await asyncpg.connect(_dsn(), server_settings={"search_path": self.schema})
        try:
            return await conn.fetch(query, *args)
        finally:
            await conn.close()

    def session(self):
        from app.db.postgres.connection import get_session_maker

        return get_session_maker()()

    async def cluster(self, name: str, config: Dict[str, Any] = None) -> uuid.UUID:
        cid = uuid.uuid4()
        await self.sql(
            "INSERT INTO clusters (id, name, config) VALUES ($1, $2, $3::jsonb)",
            cid,
            f"{name}-{self.tag}",
            json.dumps(config or {}),
        )
        return cid

    async def node(
        self,
        cluster: uuid.UUID,
        name: str,
        *,
        version: str = "1.0.0",
        commands: List[str] = UPDATE_COMMANDS,
        reported: Dict[str, Any] = None,
        status: str = "online",
        kubernetes: bool = False,
    ) -> uuid.UUID:
        nid = uuid.uuid4()
        caps: Dict[str, Any] = {"commands": commands}
        if kubernetes:
            caps["kubernetes"] = {"node_name": "k", "namespace": "gpu", "pod": "p"}
        meta: Dict[str, Any] = {}
        if reported is not None:
            meta["agent_update"] = {"target": TARGET, "release_keys": 1, **reported}
        await self.sql(
            "INSERT INTO nodes (id, cluster_id, name, status, agent_version, capabilities,"
            " metadata, last_heartbeat_at)"
            " VALUES ($1, $2, $3, $4::node_status, $5, $6::jsonb, $7::jsonb, now())",
            nid,
            cluster,
            name,
            status,
            version,
            json.dumps(caps),
            json.dumps(meta),
        )
        return nid

    async def commands(self, node_id: uuid.UUID, kind: str) -> List[Dict[str, Any]]:
        rows = await self.sql(
            "SELECT id, payload, state FROM agent_commands WHERE node_id = $1 AND command_type = $2"
            " ORDER BY created_at",
            node_id,
            kind,
        )
        return [
            {"id": r["id"], "payload": json.loads(r["payload"]), "state": r["state"]} for r in rows
        ]

    async def result(self, command_id: uuid.UUID, status: str, result=None, error=None):
        """Play the agent: a terminal command_result, then the hub's follow-up."""
        from app.db.postgres.repositories.agent_command_repository import (
            AgentCommandRepository,
        )
        from app.services.agent_updates import orchestrator

        async with self.session() as s:
            await AgentCommandRepository(s).apply_result(
                command_id,
                status,
                result=result,
                error_code=(error or {}).get("code"),
                error_message=(error or {}).get("message"),
                progress_pct=None,
                finished_at=datetime.now(timezone.utc),
            )
            await s.commit()
        await orchestrator.on_command_terminal(command_id)

    async def event(self, node_id: uuid.UUID, phase: str, version: str, **details):
        from app.services.agent_hub.protocol import validate_payload
        from app.services.agent_updates import orchestrator

        payload = {
            "kind": "agent_update",
            "severity": "info",
            "message": f"{phase} {version}",
            "details": {"phase": phase, "version": version, **details},
        }
        validate_payload("event", payload)
        await orchestrator.on_agent_event(node_id, payload)

    async def run(self, run_id) -> Dict[str, Any]:
        rows = await self.sql("SELECT * FROM agent_update_runs WHERE id = $1", run_id)
        row = dict(rows[0])
        row["steps"] = json.loads(row["steps"])
        return row

    async def meta(self, node_id) -> Dict[str, Any]:
        rows = await self.sql("SELECT metadata FROM nodes WHERE id = $1", node_id)
        return json.loads(rows[0]["metadata"])


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def env(tmp_path_factory):
    from app.db.postgres import connection
    from app.db.redis import client as redis_client_module
    from app.db.redis.cache_service import CacheService
    from app.services import alerts as alerts_pkg
    from app.services.agent_updates import releases
    from app.services.audit import audit
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
    # A throwaway release key and a signed local release (air-gapped layout).
    key = Ed25519PrivateKey.generate()
    public = base64.b64encode(
        key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    ).decode()
    dist = tmp_path_factory.mktemp("dist")
    binary = b"#!/bin/sh\necho gpudeck-agent 1.1.0\n"
    archive = _tarball(binary)
    manifest = json.dumps(
        {
            "schema": 1,
            "product": "gpudeck-agent",
            "version": "1.1.0",
            "artifacts": [
                {
                    "target": TARGET,
                    "file": f"gpudeck-agent-1.1.0-{TARGET}.tar.gz",
                    "size_bytes": len(archive),
                    "sha256": hashlib.sha256(archive).hexdigest(),
                    "binary_sha256": hashlib.sha256(binary).hexdigest(),
                    "binary_size_bytes": len(binary),
                }
            ],
        }
    ).encode()
    (dist / "gpudeck-agent-1.1.0-manifest.json").write_bytes(manifest)
    (dist / "gpudeck-agent-1.1.0-manifest.json.sig").write_text(
        base64.b64encode(key.sign(manifest)).decode()
    )
    (dist / f"gpudeck-agent-1.1.0-{TARGET}.tar.gz").write_bytes(archive)
    e.binary = binary
    saved = {
        "engine": connection.engine,
        "REDIS_KEY_PREFIX": settings.REDIS_KEY_PREFIX,
        "AGENT_DIST_DIR": settings.AGENT_DIST_DIR,
        "AGENT_CACHE_DIR": settings.AGENT_CACHE_DIR,
        "AGENT_RELEASE_URL": settings.AGENT_RELEASE_URL,
        "AGENT_RELEASE_PUBLIC_KEYS": settings.AGENT_RELEASE_PUBLIC_KEYS,
        "AGENT_UPDATE_TARGET_VERSION": settings.AGENT_UPDATE_TARGET_VERSION,
        "AGENT_UPDATE_STAGE_CONCURRENCY": settings.AGENT_UPDATE_STAGE_CONCURRENCY,
        "emit": alerts_pkg.emit,
    }
    connection.engine = e.engine
    settings.REDIS_KEY_PREFIX = e.prefix
    settings.AGENT_DIST_DIR = str(dist)
    settings.AGENT_CACHE_DIR = str(tmp_path_factory.mktemp("cache"))
    settings.AGENT_RELEASE_URL = ""
    settings.AGENT_RELEASE_PUBLIC_KEYS = public
    settings.AGENT_UPDATE_TARGET_VERSION = ""
    releases.reset_caches()
    await CacheService.reset()
    await redis_client_module.close_redis()
    audit.set_sink(e.audit.append)

    async def capture(**kw):
        e.alerts.append(kw)

    alerts_pkg.emit = capture
    try:
        yield e
    finally:
        audit.set_sink(None)
        alerts_pkg.emit = saved.pop("emit")
        try:
            redis = await redis_client_module.get_redis()
            keys = [k async for k in redis.scan_iter(match=f"{e.prefix}*", count=500)]
            if keys:
                await redis.delete(*keys)
        finally:
            await CacheService.reset()
            await redis_client_module.close_redis()
            connection.engine = saved.pop("engine")
            for k, v in saved.items():
                setattr(settings, k, v)
            releases.reset_caches()
            await e.engine.dispose()
            conn = await asyncpg.connect(_dsn())
            try:
                await drop_scratch_schema(conn, e.schema)
            finally:
                await conn.close()


async def test_update_button_with_a_prestaged_version(env: Env):
    from app.db.postgres.repositories.node_repository import NodeRepository
    from app.schemas.agent_update_schema import AgentUpdateRequest
    from app.services.agent_updates import orchestrator
    from app.services.agent_updates.service import AgentUpdateService
    from app.services.nodes.service import NodeService

    cluster = await env.cluster("one")
    node = await env.node(
        cluster,
        "gpu-a",
        reported={"mode": "helper", "state": "staged", "staged_version": "1.1.0"},
    )
    async with env.session() as s:
        before = await NodeService(s).get(node)
        await s.commit()
    assert before.agent_update.status == "staged" and before.agent_update.can_update
    assert before.agent_update.target_version == "1.1.0"
    async with env.session() as s:
        run = await AgentUpdateService(s).start_node(node, AgentUpdateRequest(), None)
        await s.commit()
    # Nothing to download: apply_update right away.
    assert run.state == "swapping", run
    assert await env.commands(node, "stage_update") == []
    (apply,) = await env.commands(node, "apply_update")
    assert apply["payload"]["args"] == {
        "version": "1.1.0",
        "health_timeout_seconds": 120,
        "run_id": str(run.id),
    }
    meta = await env.meta(node)
    assert meta["agent_update_active"]["run_id"] == str(run.id)
    async with env.session() as s:
        shown = await NodeService(s).get(node)
        await s.commit()
    assert shown.agent_update.status == "updating"
    assert shown.agent_update.active_run.id == run.id
    # The agent restarts: socket closed -> offline, but no offline alert while updating.
    await env.sql(
        "UPDATE nodes SET status = 'offline', disconnected_at = now() - interval '10 minutes'"
        " WHERE id = $1",
        node,
    )
    await orchestrator.on_disconnected(node)
    assert (await env.run(run.id))["state"] == "restarting"
    rows = await env.sql("SELECT status_message FROM nodes WHERE id = $1", node)
    assert "updating" in rows[0]["status_message"]
    async with env.session() as s:
        claimed = await NodeRepository(s).claim_offline_alerts(datetime.now(timezone.utc))
        await s.commit()
    assert node not in [c["id"] for c in claimed], "no offline alert during the update"
    # The new agent connects, re-adopts the engines and reports healthy.
    await env.sql(
        "UPDATE nodes SET status = 'online', agent_version = '1.1.0' WHERE id = $1",
        node,
    )
    await orchestrator.on_connected(node, "1.1.0")
    assert (await env.run(run.id))["state"] == "readopting"
    await env.event(node, "readopting", "1.1.0", adopted=3, relaunched=0, run_id=str(run.id))
    await env.event(node, "health_check", "1.1.0")
    assert (await env.run(run.id))["state"] == "health_check"
    await env.event(node, "succeeded", "1.1.0")
    done = await env.run(run.id)
    assert done["state"] == "succeeded" and done["finished_at"] is not None
    phases = [s["phase"] for s in done["steps"]]
    for p in (
        "pending",
        "swapping",
        "restarting",
        "readopting",
        "health_check",
        "succeeded",
    ):
        assert p in phases, phases
    assert (await env.meta(node)).get("agent_update_active") is None
    assert any(a.get("event_type") == "node.agent_updated" for a in env.audit)
    # The late command result changes nothing.
    await env.result(apply["id"], "succeeded", {"version": "1.1.0"})
    assert (await env.run(run.id))["state"] == "succeeded"


async def test_stage_then_apply_and_a_rollback(env: Env):
    from app.schemas.agent_update_schema import AgentUpdateRequest
    from app.services.agent_updates import releases, tokens
    from app.services.agent_updates.service import AgentUpdateService

    cluster = await env.cluster("two")
    node = await env.node(cluster, "gpu-b", reported={"mode": "self", "state": "idle"})
    async with env.session() as s:
        run = await AgentUpdateService(s).start_node(
            node, AgentUpdateRequest(health_timeout_seconds=60), None
        )
        await s.commit()
    assert run.state == "staging"
    (stage,) = await env.commands(node, "stage_update")
    args = stage["payload"]["args"]
    assert args["version"] == "1.1.0" and args["target"] == TARGET
    manifest = base64.b64decode(args["manifest"]["manifest"])
    assert releases.verify_signature(
        manifest, args["manifest"]["signature"], releases.public_keys()
    )
    # The download token opens exactly this node's binary.
    assert await tokens.check(args["download_token"], "1.1.0", TARGET) == node
    assert (await releases.binary_for("1.1.0", TARGET)).read_bytes() == env.binary
    await env.result(stage["id"], "succeeded", {"staged_version": "1.1.0", "sha256": "a" * 64})
    (apply,) = await env.commands(node, "apply_update")
    assert apply["payload"]["args"]["health_timeout_seconds"] == 60
    assert (await env.run(run.id))["state"] == "swapping"
    assert (await env.meta(node))["agent_update"]["staged_version"] == "1.1.0"
    # The new agent never came up: the node rolled back on its own and says so.
    await env.event(node, "rolled_back", "1.1.0", error_code="rolled_back")
    await env.result(
        apply["id"],
        "failed",
        error={"code": "rolled_back", "message": "agent 1.1.0 did not report healthy"},
    )
    done = await env.run(run.id)
    assert done["state"] == "rolled_back" and done["error_code"] == "rolled_back"
    assert any(a["event_type"] == "node.agent_update_rolled_back" for a in env.alerts)
    assert (await env.meta(node)).get("agent_update_active") is None


async def test_refusals(env: Env, monkeypatch):
    from app.schemas.agent_update_schema import AgentUpdateRequest
    from app.services.agent_updates import releases
    from app.services.agent_updates.service import AgentUpdateError, AgentUpdateService

    cluster = await env.cluster("three")
    old = await env.node(cluster, "gpu-old", commands=["load_model"])  # N-1 agent
    k8s = await env.node(
        cluster,
        "gpu-k8s",
        kubernetes=True,
        reported={"mode": "managed", "state": "idle"},
    )
    stuck = await env.node(
        cluster,
        "gpu-stuck",
        reported={"mode": "unsupported", "reason": "no update helper"},
    )
    for node, code in (
        (old, "AGENT_UPDATE_UNSUPPORTED"),
        (k8s, "AGENT_UPDATE_MANAGED"),
        (stuck, "AGENT_UPDATE_UNSUPPORTED"),
    ):
        async with env.session() as s:
            with pytest.raises(AgentUpdateError) as err:
                await AgentUpdateService(s).start_node(node, AgentUpdateRequest(), None)
        assert err.value.error_code == code
    async with env.session() as s:
        info = await AgentUpdateService(s).node(old)
    assert info.status == "manual" and "install command" in info.instructions
    assert await env.commands(old, "stage_update") == []
    monkeypatch.setattr(settings, "AGENT_RELEASE_PUBLIC_KEYS", "")
    releases.reset_caches()
    try:
        async with env.session() as s:
            with pytest.raises(AgentUpdateError) as err:
                await AgentUpdateService(s).start_node(stuck, AgentUpdateRequest(), None)
        assert err.value.error_code == "AGENT_UPDATES_DISABLED"
    finally:
        monkeypatch.undo()
        releases.reset_caches()


async def test_rolling_update_skips_and_stops_at_the_first_failure(env: Env):
    from app.schemas.agent_update_schema import AgentRolloutRequest
    from app.services.agent_updates.service import AgentUpdateService

    cluster = await env.cluster("rolling")
    staged = {"mode": "helper", "state": "staged", "staged_version": "1.1.0"}
    a = await env.node(cluster, "a-1", reported=staged)
    b = await env.node(cluster, "b-2", commands=["load_model"])  # predates self-update
    c = await env.node(cluster, "c-3", reported=staged)
    d = await env.node(cluster, "d-4", reported=staged)
    async with env.session() as s:
        rollout = await AgentUpdateService(s).start_cluster(cluster, AgentRolloutRequest(), None)
        await s.commit()
    assert rollout.state == "running" and rollout.current_node_id == a
    assert len(await env.commands(a, "apply_update")) == 1
    assert await env.commands(c, "apply_update") == [], "one node at a time"
    (apply_a,) = await env.commands(a, "apply_update")
    await env.result(apply_a["id"], "succeeded", {"version": "1.1.0"})
    async with env.session() as s:
        r = await AgentUpdateService(s).get_rollout(rollout.id)
    assert r.current_node_id == c and r.completed_count == 1
    assert [x.reason for x in r.skipped] == ["agent predates self-update: update manually"]
    (apply_c,) = await env.commands(c, "apply_update")
    await env.result(
        apply_c["id"], "failed", error={"code": "rolled_back", "message": "not healthy"}
    )
    async with env.session() as s:
        r = await AgentUpdateService(s).get_rollout(rollout.id)
        cl = await AgentUpdateService(s).cluster(cluster)
    assert r.state == "failed" and r.failed_node_id == c
    assert "c-3" in r.message and "rolled back" in r.message
    assert await env.commands(d, "apply_update") == [], "stopped before d-4"
    assert await env.commands(b, "stage_update") == []
    assert cl.rollout.id == rollout.id and cl.counts.manual == 1
    assert any(a["event_type"] == "cluster.agent_rollout_stopped" for a in env.alerts)


async def test_policy_and_automatic_rollout_in_the_window(env: Env):
    from app.schemas.agent_update_schema import AgentUpdatePolicy
    from app.services.agent_updates import orchestrator
    from app.services.agent_updates.service import AgentUpdateService

    now = datetime.now(timezone.utc)
    later = now + timedelta(hours=3)
    closed = AgentUpdatePolicy.model_validate(
        {
            "mode": "auto",
            "window": {
                "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                "start": later.strftime("%H:%M"),
                "end": (later + timedelta(hours=1)).strftime("%H:%M"),
                "timezone": "UTC",
            },
        }
    )
    cluster = await env.cluster("auto")
    node = await env.node(
        cluster,
        "gpu-auto",
        reported={"mode": "helper", "state": "staged", "staged_version": "1.1.0"},
    )
    async with env.session() as s:
        await AgentUpdateService(s).set_policy(cluster, closed, None)
        await s.commit()
    rows = await env.sql("SELECT config FROM clusters WHERE id = $1", cluster)
    assert json.loads(rows[0]["config"])["agent_updates"]["mode"] == "auto"
    await orchestrator.tick()
    assert await env.commands(node, "apply_update") == [], "outside the window"
    open_ = AgentUpdatePolicy.model_validate(
        {
            "mode": "auto",
            "window": {
                "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                "start": (now - timedelta(hours=1)).strftime("%H:%M"),
                "end": (now + timedelta(hours=1)).strftime("%H:%M"),
                "timezone": "UTC",
            },
        }
    )
    async with env.session() as s:
        await AgentUpdateService(s).set_policy(cluster, open_, None)
        info = await AgentUpdateService(s).cluster(cluster)
        await s.commit()
    assert info.in_window
    await orchestrator.tick()
    (apply,) = await env.commands(node, "apply_update")
    rows = await env.sql(
        "SELECT trigger, state FROM agent_update_rollouts WHERE cluster_id = $1",
        cluster,
    )
    assert [(r["trigger"], r["state"]) for r in rows] == [("auto", "running")]
    await env.result(apply["id"], "succeeded", {"version": "1.1.0"})
    rows = await env.sql("SELECT state FROM agent_update_rollouts WHERE cluster_id = $1", cluster)
    assert rows[0]["state"] == "succeeded"


async def test_background_prestaging(env: Env, monkeypatch):
    from app.services.agent_updates import orchestrator

    cluster = await env.cluster("prestage")
    ready = await env.node(cluster, "gpu-p1", reported={"mode": "helper", "state": "idle"})
    other = await env.node(cluster, "gpu-p2", reported={"mode": "self", "state": "idle"})
    k8s = await env.node(cluster, "gpu-p3", kubernetes=True, reported={"mode": "managed"})
    failed = await env.node(cluster, "gpu-p4", reported={"mode": "helper", "state": "idle"})
    await env.sql(
        "UPDATE nodes SET metadata = metadata || $2::jsonb WHERE id = $1",
        failed,
        json.dumps(
            {
                "agent_update_stage_failed": {
                    "version": "1.1.0",
                    "at": datetime.now(timezone.utc).isoformat(),
                }
            }
        ),
    )
    monkeypatch.setattr(settings, "AGENT_UPDATE_STAGE_CONCURRENCY", 1000)
    orchestrator.reset_for_tests()
    await orchestrator._prestage("1.1.0")
    assert len(await env.commands(ready, "stage_update")) == 1
    assert len(await env.commands(other, "stage_update")) == 1
    assert await env.commands(k8s, "stage_update") == []
    assert await env.commands(failed, "stage_update") == [], "backs off after a failure"
    # In flight: not sent twice.
    orchestrator.reset_for_tests()
    await orchestrator._prestage("1.1.0")
    assert len(await env.commands(ready, "stage_update")) == 1
