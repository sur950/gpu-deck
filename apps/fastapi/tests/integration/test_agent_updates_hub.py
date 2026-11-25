"""
End to end over the real agent hub (its own process, mTLS) with Python fake agents: agent
updates (agent protocol §14) and the N / N-1 compatibility of the hub.

- an agent that predates updates (no ``agent_update`` in hello, no update commands) is accepted
  as before and shows as "update manually"; the Update button refuses it;
- a current agent's ``hello`` / ``inventory`` ``agent_update`` and ``event(agent_update)`` frames
  pass the hub's schema validation and are stored;
- the Update button (test process = admin API): the hub sends ``stage_update`` with the signed
  manifest and a download token, the agent downloads the binary from
  ``/agent/v1/updates/<version>/<target>`` (the token is required and bound to the version), the
  hub then sends ``apply_update``; the agent restarts (socket closed, reconnect as the new
  version) and reports the phases: the run ends ``succeeded`` with every step.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict

import httpx
import pytest
import pytest_asyncio
from app.core.settings import settings
from app.services.pki import ca as cax
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import text
from tests.integration.test_agent_hub_integration import (  # noqa: F401 - scratch_db fixture
    FakeAgent,
    _free_port,
    _now,
    _wait_for,
    scratch_db,
)

pytestmark = [pytest.mark.integration, pytest.mark.db]

APP_DIR = Path(__file__).resolve().parents[2]
TARGET = "x86_64-unknown-linux-gnu"
BINARY = b"#!/bin/sh\necho 'gpudeck-agent 1.1.0 (protocol v1)'\n"


def _tarball(binary: bytes) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("gpudeck-agent")
        info.size = len(binary)
        tar.addfile(info, io.BytesIO(binary))
    return buf.getvalue()


@pytest_asyncio.fixture(scope="module")
async def upd_env(scratch_db, tmp_path_factory):  # noqa: F811
    from app.db.postgres import connection
    from app.services.agent_updates import releases
    from app.services.pki.service import reset_ca_cache_for_tests
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    key = Ed25519PrivateKey.generate()
    public = base64.b64encode(
        key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    ).decode()
    dist = tmp_path_factory.mktemp("dist")
    archive = _tarball(BINARY)
    manifest = json.dumps(
        {
            "schema": 1,
            "product": "gpudeck-agent",
            "version": "1.1.0",
            "artifacts": [
                {
                    "target": TARGET,
                    "sha256": hashlib.sha256(archive).hexdigest(),
                    "binary_sha256": hashlib.sha256(BINARY).hexdigest(),
                    "binary_size_bytes": len(BINARY),
                }
            ],
        }
    ).encode()
    (dist / "gpudeck-agent-1.1.0-manifest.json").write_bytes(manifest)
    (dist / "gpudeck-agent-1.1.0-manifest.json.sig").write_text(
        base64.b64encode(key.sign(manifest)).decode()
    )
    (dist / f"gpudeck-agent-1.1.0-{TARGET}.tar.gz").write_bytes(archive)
    cache = tmp_path_factory.mktemp("cache")
    values = {
        "DATABASE_URL": scratch_db,
        "REDIS_KEY_PREFIX": f"gpu-deck:t-upd-{uuid.uuid4().hex[:8]}:",
        "PKI_CA_DIR": str(tmp_path_factory.mktemp("pki")),
        "AGENT_HUB_PUBLIC_URL": "",
        "AGENT_RELEASE_PUBLIC_KEYS": public,
        "AGENT_DIST_DIR": str(dist),
        "AGENT_CACHE_DIR": str(cache),
        "AGENT_RELEASE_URL": "",
        "AGENT_UPDATE_TARGET_VERSION": "",
        "AGENT_UPDATE_PRESTAGE": False,
    }
    port = _free_port()
    values["AGENT_HUB_PUBLIC_URL"] = f"https://127.0.0.1:{port}"
    saved = {k: getattr(settings, k) for k in values}
    for k, v in values.items():
        setattr(settings, k, v)
    old_engine = connection.engine
    connection.engine = create_async_engine(settings.get_database_url(), poolclass=NullPool)
    reset_ca_cache_for_tests()
    releases.reset_caches()
    env = {
        "port": port,
        "values": values,
        "workdir": tmp_path_factory.mktemp("agents"),
    }
    try:
        yield env
    finally:
        await connection.engine.dispose()
        connection.engine = old_engine
        for k, v in saved.items():
            setattr(settings, k, v)
        reset_ca_cache_for_tests()
        releases.reset_caches()
        from app.db.redis.client import get_redis

        client = await get_redis()
        keys = [
            k async for k in client.scan_iter(match=values["REDIS_KEY_PREFIX"] + "*", count=500)
        ]
        if keys:
            await client.delete(*keys)


@pytest.fixture(scope="module")
def upd_hub(upd_env):
    log_path = upd_env["workdir"] / "hub.log"
    env = dict(os.environ)
    env.update(
        {k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in upd_env["values"].items()}
    )
    env.update(
        {
            "AGENT_HUB_HOST": "127.0.0.1",
            "AGENT_HUB_PORT": str(upd_env["port"]),
            "NODE_OFFLINE_AFTER_SECONDS": "3600",
            "PYTHONPATH": str(APP_DIR),
            "LOG_LEVEL": "INFO",
            "DEBUG": "false",
        }
    )
    env.pop("PYTEST_CURRENT_TEST", None)
    log = open(log_path, "w")
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.agent_hub_main"],
        cwd=str(APP_DIR),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    base = f"https://127.0.0.1:{upd_env['port']}"
    deadline = time.monotonic() + 40
    ready = False
    while time.monotonic() < deadline and proc.poll() is None:
        try:
            if httpx.get(f"{base}/health", verify=False, timeout=1.0).status_code == 200:
                ready = True
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    if not ready:
        proc.kill()
        log.close()
        pytest.fail(f"hub did not start:\n{log_path.read_text()[-4000:]}")
    yield {"proc": proc, "log": log_path, "base": base}
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
    log.close()


async def _db(sql: str, **params):
    from app.db.postgres.connection import get_session_maker

    async with get_session_maker()() as session:
        result = await session.execute(text(sql), params)
        rows = result.mappings().all() if result.returns_rows else None
        await session.commit()
        return rows


async def _join(upd_env, upd_hub, name: str) -> Dict[str, Any]:
    from app.db.postgres.connection import get_session_maker
    from app.schemas.fleet_schema import ClusterCreate, JoinTokenCreate
    from app.services.clusters.service import ClusterService
    from app.services.nodes.service import NodeService
    from app.services.pki.service import ensure_ca_standalone

    await ensure_ca_standalone()
    async with get_session_maker()() as session:
        if "cluster_id" not in upd_env:
            clusters = await ClusterService.as_dependency(None, session)
            cluster = await clusters.create(ClusterCreate(name="upd", type="bare_metal"), None)
            await session.commit()
            upd_env["cluster_id"] = cluster.id
        token = await NodeService(session).create_join_token(
            JoinTokenCreate(cluster_id=upd_env["cluster_id"], node_name_hint=name), None
        )
        await session.commit()
    key = cax.generate_private_key("ecdsa_p256")
    body = {
        "join_token": token.token,
        "csr_pem": cax.build_csr(key),
        "hostname": name,
        "name": name,
        "os": "linux",
        "arch": "x86_64",
        "agent_version": "1.0.0",
        "protocol_version": 1,
        "proxy_port": 9443,
        "labels": {},
    }
    async with httpx.AsyncClient(verify=False, timeout=10) as client:
        resp = await client.post(f"{upd_hub['base']}/agent/v1/join", json=body)
    assert resp.status_code == 200, resp.text
    joined = resp.json()["data"]
    # The release key reaches the node in its signed policy.
    policy = json.loads(base64.b64decode(joined["security"]["policy"]))
    assert policy["release"]["public_keys"] == [upd_env["values"]["AGENT_RELEASE_PUBLIC_KEYS"]]
    workdir = upd_env["workdir"] / name
    workdir.mkdir()
    agent = FakeAgent(
        f"wss://127.0.0.1:{upd_env['port']}/agent/v1/ws",
        joined["node_id"],
        workdir,
        joined["ca_bundle_pem"],
        key,
        joined["certificate_pem"],
    )
    return {"joined": joined, "agent": agent, "workdir": workdir, "key": key}


def _hello(version: str, *, commands=None, update=None) -> Dict[str, Any]:
    caps: Dict[str, Any] = {"engines": [], "runtimes": ["native"]}
    if commands is not None:
        caps["commands"] = commands
    payload = {
        "agent_version": version,
        "protocol_version": 1,
        "os": "linux",
        "arch": "x86_64",
        "hostname": "gpu",
        "started_at": _now(),
        "capabilities": caps,
        "cache": {
            "dir": "/var/lib/gpudeck/cache",
            "limit_bytes": 2**34,
            "used_bytes": 0,
        },
        "proxy": {"port": 9443, "mode": "tunnel"},
    }
    if update is not None:
        payload["agent_update"] = update
    return payload


async def _say_hello(agent: FakeAgent, payload) -> Dict[str, Any]:
    hello_id = await agent.send("hello", payload)
    ack = await agent.recv_type("hello_ack")
    assert ack["correlation_id"] == hello_id
    assert ack["payload"]["accepted"] is True, ack
    return ack


COMMANDS_N = [
    "load_model",
    "unload_model",
    "drain_instance",
    "start_engine",
    "stop_engine",
    "restart_instance",
    "prefetch_model",
    "evict_cache",
    "run_benchmark",
    "update_config",
    "rotate_cert",
    "install_cert",
    "cancel",
    "scan_path",
    "hash_files",
    "start_ssh_instance",
    "stop_ssh_instance",
    "stage_update",
    "apply_update",
]


@pytest.mark.asyncio
async def test_older_agent_keeps_working_and_shows_update_manually(upd_env, upd_hub):
    from app.db.postgres.connection import get_session_maker
    from app.schemas.agent_update_schema import AgentUpdateRequest
    from app.services.agent_updates.service import AgentUpdateError, AgentUpdateService

    n = await _join(upd_env, upd_hub, "gpu-old")
    agent = await n["agent"].connect()
    ack = await _say_hello(agent, _hello("1.0.0"))  # what a pre-update agent sends
    assert "agent_updates" in ack["payload"]["features"]
    await agent.send(
        "inventory",
        {
            "devices": [],
            "disks": [],
            "cache": {"dir": "/x", "limit_bytes": 0, "used_bytes": 0, "items": []},
            "instances": [],
        },
    )
    node_id = uuid.UUID(n["joined"]["node_id"])

    async def online():
        rows = await _db("SELECT status FROM nodes WHERE id = :id", id=node_id)
        return rows[0]["status"] == "online"

    await _wait_for(online)
    async with get_session_maker()() as s:
        info = await AgentUpdateService(s).node(node_id)
        with pytest.raises(AgentUpdateError) as err:
            await AgentUpdateService(s).start_node(node_id, AgentUpdateRequest(), None)
    assert info.mode == "manual" and info.status == "manual" and info.instructions
    assert err.value.error_code == "AGENT_UPDATE_UNSUPPORTED"
    # Still a working node: a heartbeat is ingested, nothing was sent to it.
    await agent.send("heartbeat", {"uptime_seconds": 5, "config_version": 0, "instances": []})
    rows = await _db("SELECT count(*) AS n FROM agent_commands WHERE node_id = :id", id=node_id)
    assert rows[0]["n"] == 0
    await agent.close()


@pytest.mark.asyncio
async def test_update_button_stage_download_apply_restart_succeeded(upd_env, upd_hub):
    from app.db.postgres.connection import get_session_maker
    from app.schemas.agent_update_schema import AgentUpdateRequest
    from app.services.agent_updates.service import AgentUpdateService

    n = await _join(upd_env, upd_hub, "gpu-new")
    node_id = uuid.UUID(n["joined"]["node_id"])
    status = {"mode": "helper", "target": TARGET, "state": "idle", "release_keys": 1}
    agent = await n["agent"].connect()
    await _say_hello(agent, _hello("1.0.0", commands=COMMANDS_N, update=status))
    await agent.send(
        "inventory",
        {
            "devices": [],
            "disks": [],
            "cache": {"dir": "/x", "limit_bytes": 0, "used_bytes": 0, "items": []},
            "instances": [],
            "agent_update": status,
        },
    )

    async def reported():
        rows = await _db("SELECT status, metadata FROM nodes WHERE id = :id", id=node_id)
        meta = rows[0]["metadata"] or {}
        return rows[0]["status"] == "online" and (meta.get("agent_update") or {}).get("mode")

    await _wait_for(reported)
    async with get_session_maker()() as s:
        run = await AgentUpdateService(s).start_node(node_id, AgentUpdateRequest(), None)
        await s.commit()
    assert run.state == "staging"

    # stage_update with the signed manifest and a download token.
    cmd = await agent.recv_type("command", timeout=15)
    assert cmd["payload"]["command"] == "stage_update"
    args = cmd["payload"]["args"]
    assert args["version"] == "1.1.0" and args["target"] == TARGET
    manifest = json.loads(base64.b64decode(args["manifest"]["manifest"]))
    want = manifest["artifacts"][0]["binary_sha256"]
    await agent.send(
        "command_result",
        {"command": "stage_update", "status": "accepted", "started_at": _now()},
        correlation_id=cmd["id"],
    )
    url = f"{upd_hub['base']}/agent/v1/updates/1.1.0/{TARGET}"
    async with httpx.AsyncClient(verify=False, timeout=20) as client:
        denied = await client.get(url, headers={"X-GPUDeck-Update-Token": "gdu_" + "0" * 48})
        other = await client.get(
            f"{upd_hub['base']}/agent/v1/updates/1.0.9/{TARGET}",
            headers={"X-GPUDeck-Update-Token": args["download_token"]},
        )
        got = await client.get(url, headers={"X-GPUDeck-Update-Token": args["download_token"]})
    assert denied.status_code == 401
    assert other.status_code == 401, "the token is bound to the version"
    assert got.status_code == 200, got.text
    assert hashlib.sha256(got.content).hexdigest() == want
    await agent.send(
        "event",
        {
            "kind": "agent_update",
            "severity": "info",
            "message": "agent 1.1.0 staged and verified",
            "details": {"phase": "staged", "version": "1.1.0"},
        },
    )
    await agent.send(
        "command_result",
        {
            "command": "stage_update",
            "status": "succeeded",
            "result": {
                "staged_version": "1.1.0",
                "sha256": want,
                "size_bytes": len(BINARY),
            },
            "finished_at": _now(),
        },
        correlation_id=cmd["id"],
    )

    # The hub moves on to apply_update by itself.
    cmd = await agent.recv_type("command", timeout=20)
    assert cmd["payload"]["command"] == "apply_update"
    assert cmd["payload"]["args"]["version"] == "1.1.0"
    assert cmd["payload"]["args"]["run_id"] == str(run.id)
    apply_id = cmd["id"]
    await agent.send(
        "command_result",
        {"command": "apply_update", "status": "accepted", "started_at": _now()},
        correlation_id=apply_id,
    )
    await agent.send(
        "event",
        {
            "kind": "agent_update",
            "severity": "info",
            "message": "swapping",
            "command_id": apply_id,
            "details": {
                "phase": "swapping",
                "version": "1.1.0",
                "from_version": "1.0.0",
            },
        },
    )

    async def state(*want_states):
        rows = await _db("SELECT state FROM agent_update_runs WHERE id = :id", id=run.id)
        return rows[0]["state"] in want_states

    await _wait_for(lambda: state("swapping"))
    # Restart: the socket goes away (-> restarting), the new version reconnects.
    await agent.close()
    await _wait_for(lambda: state("restarting"))
    rows = await _db("SELECT metadata FROM nodes WHERE id = :id", id=node_id)
    assert rows[0]["metadata"]["agent_update_active"]["state"] == "restarting"
    agent2 = await FakeAgent(
        agent.ws_url,
        agent.node_id,
        n["workdir"],
        agent.ca_pem,
        n["key"],
        n["joined"]["certificate_pem"],
    ).connect()
    new_status = {**status, "previous_version": "1.0.0"}
    await _say_hello(agent2, _hello("1.1.0", commands=COMMANDS_N, update=new_status))
    await _wait_for(lambda: state("readopting"))
    for phase in ("readopting", "health_check", "succeeded"):
        await agent2.send(
            "event",
            {
                "kind": "agent_update",
                "severity": "info",
                "message": phase,
                "command_id": apply_id,
                "details": {
                    "phase": phase,
                    "version": "1.1.0",
                    "from_version": "1.0.0",
                    "adopted": 2,
                },
            },
        )
    await agent2.send(
        "command_result",
        {
            "command": "apply_update",
            "status": "succeeded",
            "result": {"version": "1.1.0", "from_version": "1.0.0"},
            "finished_at": _now(),
        },
        correlation_id=apply_id,
    )
    await _wait_for(lambda: state("succeeded"))
    rows = await _db("SELECT steps, finished_at FROM agent_update_runs WHERE id = :id", id=run.id)
    phases = [s["phase"] for s in rows[0]["steps"]]
    for p in (
        "staging",
        "staged",
        "swapping",
        "restarting",
        "readopting",
        "health_check",
        "succeeded",
    ):
        assert p in phases, phases
    rows = await _db("SELECT agent_version, metadata FROM nodes WHERE id = :id", id=node_id)
    assert rows[0]["agent_version"] == "1.1.0"
    assert rows[0]["metadata"].get("agent_update_active") is None
    log = upd_hub["log"].read_text()
    assert args["download_token"] not in log, "the download token is never logged"
    await agent2.close()
    await asyncio.sleep(0.2)
