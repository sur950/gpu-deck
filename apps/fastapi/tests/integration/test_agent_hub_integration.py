"""
End-to-end integration: a Python fake agent against the real agent hub.

Setup (module scope):
- a scratch Postgres database (``CREATE DATABASE`` through a superuser URL,
  ``tests.schema_helpers.pg_admin_url()``: ``TEST_PG_ADMIN_URL``, written to
  apps/fastapi/.env by bootstrap.dev.sh for the dev Postgres container;
  owned by the app role) with ``001_init/up.sql`` applied; dropped afterwards;
- a unique Redis key prefix (``gpu-deck:t-hub-<hex>:``) so nothing collides
  with the dev data;
- the hub as its **own process** (``python -m app.agent_hub_main``) on a random
  port with mTLS, pointed at the scratch DB.

The test process plays the admin API / jobs side (services + ``AgentHub``),
so every command goes test process -> Redis -> hub process -> WebSocket ->
fake agent and back (cross-process dispatch).

Covered: join (token + CSR) -> mTLS WSS -> hello/hello_ack/config_push ->
inventory -> metrics_batch -> heartbeat -> command round trip -> replay after
reconnect -> log/crash events -> rollups + metrics queries + Prometheus
exposition + admin routes -> CRL revocation closes the socket and blocks the
cert -> offline detection -> trace index.
"""

import asyncio
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

import httpx
import pytest
import pytest_asyncio
import websockets
from app.core.settings import settings
from app.services.agent_hub.protocol import build_envelope, dumps, validate_envelope
from app.services.pki import ca as cax
from sqlalchemy import text
from tests.schema_helpers import pg_admin_url

pytestmark = [pytest.mark.integration, pytest.mark.db]

APP_DIR = Path(__file__).resolve().parents[2]
INIT_SQL = APP_DIR / "app/db/postgres/alembic/migrations/001_init/up.sql"
STATE: Dict[str, Any] = {}


# ==========================================
# Helpers
# ==========================================


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _now(delta_s: float = 0.0) -> str:
    ts = datetime.now(timezone.utc) - timedelta(seconds=delta_s)
    return ts.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _psql(url: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["psql", url, "-v", "ON_ERROR_STOP=1", "-q", *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


async def _wait_for(predicate, timeout: float = 15.0, interval: float = 0.2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = await predicate()
        if value:
            return value
        await asyncio.sleep(interval)
    raise AssertionError("condition not met in time")


class FakeAgent:
    """Minimal agent speaking the v1 protocol over mTLS."""

    def __init__(self, ws_url: str, node_id: str, workdir: Path, ca_pem: str, key, cert_pem: str):
        self.ws_url = ws_url
        self.node_id = node_id
        self.ca_pem = ca_pem
        self.key_path = workdir / "agent.key"
        self.cert_path = workdir / "agent.crt"
        self.key_path.write_text(cax.key_to_pem(key))
        self.cert_path.write_text(cert_pem)
        self.ws = None

    def ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context(cadata=self.ca_pem)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_3
        ctx.load_cert_chain(str(self.cert_path), str(self.key_path))
        return ctx

    async def connect(self):
        self.ws = await websockets.connect(self.ws_url, ssl=self.ssl_context(), max_size=2**22)
        return self

    async def send(self, mtype: str, payload: Dict[str, Any], correlation_id=None) -> str:
        frame = build_envelope(mtype, payload, self.node_id, correlation_id)
        validate_envelope(json.loads(dumps(frame)))  # the fake agent obeys the contract too
        await self.ws.send(dumps(frame))
        return frame["id"]

    async def recv(self, timeout: float = 10.0) -> Dict[str, Any]:
        raw = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
        frame = json.loads(raw)
        validate_envelope(frame)  # every hub frame must match the contract
        return frame

    async def recv_type(self, mtype: str, timeout: float = 10.0) -> Dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            frame = await self.recv(max(0.1, deadline - time.monotonic()))
            if frame["type"] == mtype:
                return frame

    async def hello(self, config_version: int = 0) -> Dict[str, Any]:
        hello_id = await self.send(
            "hello",
            {
                "agent_version": "1.0.0",
                "protocol_version": 1,
                "os": "macos",
                "arch": "aarch64",
                "hostname": "fake-mac",
                "ip_addresses": ["127.0.0.1"],
                "cpu_cores": 10,
                "memory_total_bytes": 32 * 2**30,
                "started_at": _now(),
                "capabilities": {
                    "engines": [
                        {
                            "name": "llama_cpp",
                            "versions": ["b8798"],
                            "runtime": "native",
                        }
                    ],
                    "runtimes": ["native"],
                    "metal": True,
                },
                "cache": {
                    "dir": "/tmp/gpudeck-cache",
                    "limit_bytes": 2**34,
                    "used_bytes": 0,
                },
                "proxy": {"port": 9443, "address": "127.0.0.1"},
                "config_version": config_version,
                "labels": {"zone": "office"},
            },
        )
        ack = await self.recv_type("hello_ack")
        assert ack["correlation_id"] == hello_id
        return ack

    async def close(self):
        if self.ws is not None:
            await self.ws.close()


# ==========================================
# Fixtures
# ==========================================


@pytest.fixture(scope="module")
def scratch_db():
    admin_url = pg_admin_url()
    if not admin_url:
        pytest.skip("no Postgres superuser URL (TEST_PG_ADMIN_URL; bootstrap.dev.sh writes it)")
    if shutil.which("psql") is None:
        pytest.skip("psql client not on PATH")
    probe = _psql(admin_url, "-Atc", "select 1")
    if probe.returncode != 0:
        pytest.skip(f"no Postgres superuser access for a scratch DB: {probe.stderr.strip()}")
    name = f"gpu_deck_b2_test_{uuid.uuid4().hex[:8]}"
    owner = settings.POSTGRES_USER
    from sqlalchemy.engine import make_url

    app_url = make_url(settings.get_sync_database_url())
    owner = app_url.username or owner
    created = _psql(admin_url, "-c", f'CREATE DATABASE {name} OWNER "{owner}"')
    assert created.returncode == 0, created.stderr
    scratch_admin = admin_url.rsplit("/", 1)[0] + f"/{name}"
    for ext in ("uuid-ossp", "pgcrypto", "pg_trgm"):
        _psql(scratch_admin, "-c", f'CREATE EXTENSION IF NOT EXISTS "{ext}"')
    app_libpq = app_url.set(drivername="postgresql", database=name).render_as_string(
        hide_password=False
    )
    applied = _psql(app_libpq, "-f", str(INIT_SQL))
    assert applied.returncode == 0, applied.stderr[-2000:]
    # Later migrations (generated models include their columns, e.g. gpu_instances).
    for up in sorted(INIT_SQL.parent.parent.glob("[0-9][0-9][0-9]_*/up.sql")):
        if up.parent.name == "001_init":
            continue
        applied = _psql(app_libpq, "-v", "ON_ERROR_STOP=1", "-f", str(up))
        assert applied.returncode == 0, f"{up.parent.name}: {applied.stderr[-2000:]}"
    try:
        yield app_libpq
    finally:
        _psql(admin_url, "-c", f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


@pytest_asyncio.fixture(scope="module")
async def hub_env(scratch_db, tmp_path_factory):
    """Point the test process at the scratch DB + a private Redis prefix."""
    from app.db.postgres import connection
    from app.services.pki.service import reset_ca_cache_for_tests
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    saved = {
        k: getattr(settings, k)
        for k in (
            "DATABASE_URL",
            "REDIS_KEY_PREFIX",
            "PKI_CA_DIR",
            "AGENT_HUB_PUBLIC_URL",
        )
    }
    prefix = f"gpu-deck:t-hub-{uuid.uuid4().hex[:8]}:"
    pki_dir = tmp_path_factory.mktemp("pki")
    port = _free_port()
    settings.DATABASE_URL = scratch_db
    settings.REDIS_KEY_PREFIX = prefix
    settings.PKI_CA_DIR = str(pki_dir)
    settings.AGENT_HUB_PUBLIC_URL = f"https://127.0.0.1:{port}"
    old_engine = connection.engine
    connection.engine = create_async_engine(settings.get_database_url(), poolclass=NullPool)
    reset_ca_cache_for_tests()
    from app.services.audit import audit

    captured = []
    audit.set_sink(captured.append)
    env = {
        "port": port,
        "prefix": prefix,
        "pki_dir": pki_dir,
        "db_url": scratch_db,
        "audit": captured,
        "workdir": tmp_path_factory.mktemp("agent"),
    }
    try:
        yield env
    finally:
        audit.set_sink(None)
        await connection.engine.dispose()
        connection.engine = old_engine
        for k, v in saved.items():
            setattr(settings, k, v)
        reset_ca_cache_for_tests()
        from app.db.redis.client import get_redis

        client = await get_redis()
        keys = [k async for k in client.scan_iter(match=prefix + "*", count=500)]
        if keys:
            await client.delete(*keys)


@pytest_asyncio.fixture(scope="module")
async def seeded(hub_env):
    """CA (created by the test process first), a cluster and a join token."""
    from app.db.postgres.connection import get_session_maker
    from app.schemas.fleet_schema import ClusterCreate, JoinTokenCreate
    from app.services.clusters.service import ClusterService
    from app.services.nodes.service import NodeService
    from app.services.pki.service import ensure_ca_standalone

    await ensure_ca_standalone()
    async with get_session_maker()() as session:
        clusters = await ClusterService.as_dependency(None, session)
        cluster = await clusters.create(
            ClusterCreate(name="lab", type="bare_metal", egress_allowlist=["10.0.0.0/8"]),
            None,
        )
        await session.commit()
        token = await NodeService(session).create_join_token(
            JoinTokenCreate(cluster_id=cluster.id, node_name_hint="fake-mac", labels={"rack": "1"}),
            None,
        )
        await session.commit()
    STATE["cluster_id"] = cluster.id
    STATE["token"] = token
    return STATE


@pytest.fixture(scope="module")
def hub_process(hub_env, seeded):
    log_path = hub_env["workdir"] / "hub.log"
    env = dict(os.environ)
    env.update(
        {
            "DATABASE_URL": hub_env["db_url"],
            "REDIS_KEY_PREFIX": hub_env["prefix"],
            "PKI_CA_DIR": str(hub_env["pki_dir"]),
            "AGENT_HUB_HOST": "127.0.0.1",
            "AGENT_HUB_PORT": str(hub_env["port"]),
            "AGENT_HUB_PUBLIC_URL": f"https://127.0.0.1:{hub_env['port']}",
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
    url = f"https://127.0.0.1:{hub_env['port']}/health"
    deadline = time.monotonic() + 40
    ready = False
    while time.monotonic() < deadline and proc.poll() is None:
        try:
            if httpx.get(url, verify=False, timeout=1.0).status_code == 200:
                ready = True
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    if not ready:
        proc.kill()
        log.close()
        pytest.fail(f"hub did not start:\n{log_path.read_text()[-4000:]}")
    yield {
        "proc": proc,
        "log": log_path,
        "base": f"https://127.0.0.1:{hub_env['port']}",
    }
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


# ==========================================
# Tests (ordered: each builds on the previous state)
# ==========================================


@pytest.mark.asyncio
async def test_join_with_token_and_csr(hub_process, seeded, hub_env):
    key = cax.generate_private_key("ecdsa_p256")
    body = {
        "join_token": seeded["token"].token,
        "csr_pem": cax.build_csr(key),
        "hostname": "fake-mac",
        "name": "fake-mac",
        "os": "macos",
        "arch": "aarch64",
        "agent_version": "1.0.0",
        "protocol_version": 1,
        "proxy_port": 9443,
        "labels": {"zone": "office"},
    }
    async with httpx.AsyncClient(verify=False, timeout=10) as client:
        bad = await client.post(
            f"{hub_process['base']}/agent/v1/join", json={**body, "csr_pem": "garbage"}
        )
        assert bad.status_code == 422 and bad.json()["error"]["code"] == "CSR_INVALID"
        resp = await client.post(f"{hub_process['base']}/agent/v1/join", json=body)
        assert resp.status_code == 200, resp.text
        joined = resp.json()["data"]
        again = await client.post(f"{hub_process['base']}/agent/v1/join", json=body)
        assert again.status_code == 401
        assert again.json()["error"]["code"] == "JOIN_TOKEN_INVALID"
    # TOFU pin: the CA we got matches the fingerprint printed in the join command
    assert cax.pem_fingerprint(joined["ca_bundle_pem"]) == seeded["token"].ca_fingerprint_sha256
    cert = cax.cert_info(cax.cert_from_pem(joined["certificate_pem"]))
    assert cert.common_name == joined["node_id"] and cert.organization == "gpudeck-nodes"
    assert joined["hub_ws_url"].endswith("/agent/v1/ws")
    rows = await _db("SELECT status, labels FROM nodes WHERE id = :id", id=joined["node_id"])
    assert rows[0]["status"] == "pending" and rows[0]["labels"] == {
        "zone": "office",
        "rack": "1",
    }
    STATE["joined"] = joined
    STATE["agent_key"] = key


@pytest.mark.asyncio
async def test_mtls_hello_ack_and_config_push(hub_process, hub_env):
    joined = STATE["joined"]
    ws_url = f"wss://127.0.0.1:{hub_env['port']}/agent/v1/ws"
    STATE["ws_url"] = ws_url
    agent = FakeAgent(
        ws_url,
        joined["node_id"],
        hub_env["workdir"],
        joined["ca_bundle_pem"],
        STATE["agent_key"],
        joined["certificate_pem"],
    )
    await agent.connect()
    ack = await agent.hello()
    assert ack["payload"]["accepted"] is True
    assert ack["payload"]["node_name"] == "fake-mac"
    push = await agent.recv_type("config_push")
    payload = push["payload"]
    assert payload["full"] is True and payload["config_version"] >= 1
    assert f"127.0.0.1:{hub_env['port']}" in payload["egress_allowlist"]
    assert "10.0.0.0/8" in payload["egress_allowlist"]
    assert payload["crl"]["serials"] == []
    await agent.send(
        "event",
        {
            "kind": "config_applied",
            "severity": "info",
            "message": "ok",
            "details": {"config_version": payload["config_version"]},
        },
    )

    async def online():
        rows = await _db("SELECT status FROM nodes WHERE id = :id", id=joined["node_id"])
        return rows[0]["status"] == "online"

    await _wait_for(online)
    from app.services.agent_hub.service import AgentHub

    assert await AgentHub.is_connected(uuid.UUID(joined["node_id"]))
    STATE["agent"] = agent


@pytest.mark.asyncio
async def test_inventory_metrics_and_heartbeat_ingest(hub_process, hub_env):
    agent: FakeAgent = STATE["agent"]
    node_id = STATE["joined"]["node_id"]
    # Catalog / deployment rows the agent will report an instance for.
    ids = {k: str(uuid.uuid4()) for k in ("model", "version", "ev", "dep", "inst")}
    await _db(
        "INSERT INTO models (id, name, source, repo_id, type) VALUES "
        "(:m, 'qwen3-0.6b', 'local_path', '/models/qwen3', 'chat')",
        m=ids["model"],
    )
    await _db(
        "INSERT INTO model_versions (id, model_id, version) VALUES (:v, :m, 'q4')",
        v=ids["version"],
        m=ids["model"],
    )
    await _db(
        "INSERT INTO engine_versions (id, engine_id, version) "
        "SELECT :ev, id, 'b8798' FROM engines WHERE name = 'llama_cpp'",
        ev=ids["ev"],
    )
    await _db(
        "INSERT INTO deployments (id, name, model_id, model_version_id, engine_version_id) "
        "VALUES (:d, 'qwen3', :m, :v, :ev)",
        d=ids["dep"],
        m=ids["model"],
        v=ids["version"],
        ev=ids["ev"],
    )
    await _db(
        "INSERT INTO instances (id, deployment_id, node_id, state, port, served_model_name) "
        "VALUES (:i, :d, :n, 'loading', 10240, 'qwen3-q4')",
        i=ids["inst"],
        d=ids["dep"],
        n=node_id,
    )
    STATE["ids"] = ids
    await agent.send(
        "inventory",
        {
            "devices": [
                {
                    "vendor": "apple",
                    "index": 0,
                    "uuid": "apple-m5-test",
                    "name": "Apple M5",
                    "vram_total_bytes": 24 * 2**30,
                    "is_unified_memory": True,
                    "health": "healthy",
                    "core_count": 10,
                }
            ],
            "disks": [
                {
                    "mount": "/",
                    "total_bytes": 10**12,
                    "free_bytes": 5 * 10**11,
                    "is_cache_dir": True,
                }
            ],
            "cache": {
                "dir": "/tmp/gpudeck-cache",
                "limit_bytes": 2**34,
                "used_bytes": 123,
                "items": [],
            },
            "instances": [
                {
                    "instance_id": ids["inst"],
                    "deployment_id": ids["dep"],
                    "state": "ready",
                    "health": "healthy",
                    "port": 10240,
                    "served_model_name": "qwen3-q4",
                }
            ],
            "system": {"cpu_cores": 10, "memory_total_bytes": 32 * 2**30},
        },
    )

    async def device_row():
        rows = await _db("SELECT id FROM devices WHERE node_id = :n", n=node_id)
        return rows[0]["id"] if rows else None

    device_id = await _wait_for(device_row)
    STATE["device_id"] = str(device_id)
    rows = await _db("SELECT state, health FROM instances WHERE id = :i", i=ids["inst"])
    assert rows[0]["state"] == "ready" and rows[0]["health"] == "healthy"
    await _db(
        "UPDATE instances SET device_ids = ARRAY[CAST(:d AS uuid)] WHERE id = :i",
        d=str(device_id),
        i=ids["inst"],
    )

    # Three minutes of samples (two per minute) ending in the previous complete minute.
    base = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=3)
    STATE["metrics_base"] = base
    gpu, engine, sessions = [], [], []
    for minute in range(3):
        for half, sec in enumerate((5, 35)):
            ts = (base + timedelta(minutes=minute, seconds=sec)).isoformat().replace("+00:00", "Z")
            n = minute * 2 + half
            gpu.append(
                {
                    "ts": ts,
                    "device_id": str(device_id),
                    "utilization_pct": 40.0 + 10 * half,
                    "memory_used_bytes": 2**30,
                    "memory_total_bytes": 24 * 2**30,
                    "power_watts": 20.0,
                    "temperature_c": 50.0,
                }
            )
            engine.append(
                {
                    "ts": ts,
                    "instance_id": ids["inst"],
                    "deployment_id": ids["dep"],
                    "active_sessions": 1,
                    "waiting_sessions": 0,
                    "requests_total": 10 * (n + 1),
                    "errors_total": n // 2,
                    "prompt_tokens_total": 100 * (n + 1),
                    "generated_tokens_total": 50 * (n + 1),
                    "generation_tps": 30.0,
                    "ttft_ms_p95": 120.0,
                    "kv_cache_usage_pct": 25.0,
                    "health": "healthy",
                }
            )
    sessions.append(
        {
            "ts": _now(),
            "instance_id": ids["inst"],
            "slot_id": 0,
            "session_key": "slot-0",
            "state": "generating",
            "prompt_tokens": 12,
            "generated_tokens": 5,
            "kv_cache_tokens": 17,
            "context_size": 4096,
        }
    )
    # Fresh samples for the live cache / Prometheus.
    gpu.append(
        {
            "ts": _now(),
            "device_id": str(device_id),
            "utilization_pct": 77.0,
            "memory_used_bytes": 2**30,
            "memory_total_bytes": 24 * 2**30,
            "power_watts": 21.0,
        }
    )
    engine.append(
        {
            "ts": _now(),
            "instance_id": ids["inst"],
            "deployment_id": ids["dep"],
            "active_sessions": 1,
            "waiting_sessions": 2,
            "slots_total": 4,
            "requests_total": 70,
            "health": "healthy",
            "generation_tps": 31.0,
        }
    )
    # A sample for an unknown device must be dropped (no FK on sample tables).
    gpu.append({"ts": _now(), "device_id": str(uuid.uuid4()), "utilization_pct": 1.0})
    await agent.send(
        "metrics_batch",
        {
            "interval_seconds": 30,
            "sequence": 1,
            "gpu": gpu,
            "engine": engine,
            "sessions": sessions,
            "agent": {"cpu_pct": 1.5, "rss_bytes": 1000},
        },
    )
    await agent.send(
        "heartbeat",
        {
            "uptime_seconds": 10,
            "config_version": 1,
            "instances": [
                {
                    "instance_id": ids["inst"],
                    "state": "ready",
                    "health": "healthy",
                    "active_sessions": 1,
                }
            ],
            "load": {"cpu_pct": 12.0, "memory_used_bytes": 2**33},
        },
    )

    async def samples_in():
        rows = await _db(
            "SELECT (SELECT COUNT(*) FROM metric_samples_gpu) AS g, "
            "(SELECT COUNT(*) FROM metric_samples_engine) AS e, "
            "(SELECT COUNT(*) FROM metric_samples_session) AS s"
        )
        return rows[0] if rows[0]["g"] >= 7 and rows[0]["e"] >= 7 and rows[0]["s"] >= 1 else None

    counts = await _wait_for(samples_in)
    assert counts["g"] == 7  # the unknown device's sample was dropped
    rows = await _db("SELECT last_heartbeat_at, disks FROM nodes WHERE id = :n", n=node_id)
    assert rows[0]["last_heartbeat_at"] is not None and rows[0]["disks"][0]["is_cache_dir"]


@pytest.mark.asyncio
async def test_rollups_and_metric_queries(hub_process, hub_env):
    from app.db.postgres.connection import get_session_maker
    from app.db.postgres.repositories.metric_repository import MetricRepository
    from app.services.metrics.prometheus import render_fleet_exposition
    from app.services.metrics.service import MetricsService

    base = STATE["metrics_base"]
    ids = STATE["ids"]
    async with get_session_maker()() as session:
        repo = MetricRepository(session)
        await repo.rollup_minutes(base, base + timedelta(minutes=3), 30)
        await repo.rollup_minutes(base, base + timedelta(minutes=3), 30)  # idempotent upsert
        await session.commit()
    rollups = await _db(
        "SELECT ts, requests_delta, errors_delta, error_rate_pct, active_sessions_avg, "
        "ttft_ms_p95, healthy_seconds FROM metric_rollups_engine_1m ORDER BY ts"
    )
    assert len(rollups) == 3
    # first minute: only the in-minute increase (10); later minutes include the boundary step
    assert [r["requests_delta"] for r in rollups] == [10, 20, 20]
    assert rollups[1]["errors_delta"] == 1 and rollups[1]["error_rate_pct"] == pytest.approx(5.0)
    assert rollups[0]["healthy_seconds"] == pytest.approx(60.0)
    gpu_rollups = await _db(
        "SELECT utilization_pct_avg, utilization_pct_max, sample_count, energy_wh "
        "FROM metric_rollups_gpu_1m ORDER BY ts"
    )
    assert len(gpu_rollups) == 3
    assert gpu_rollups[0]["utilization_pct_avg"] == pytest.approx(45.0)
    assert gpu_rollups[0]["utilization_pct_max"] == pytest.approx(50.0)
    assert gpu_rollups[0]["energy_wh"] == pytest.approx(20.0 * 60 / 3600, rel=1e-3)

    frm = base.isoformat()
    to = (base + timedelta(minutes=3)).isoformat()
    async with get_session_maker()() as session:
        svc = MetricsService(session)
        gpu = await svc.gpu(
            frm,
            to,
            "1m",
            ["gpu_utilization_pct", "gpu_power_watts"],
            None,
            None,
            None,
            "none",
        )
        util = next(s for s in gpu["series"] if s["metric"] == "gpu_utilization_pct")
        assert [p[1] for p in util["points"]] == [45.0, 45.0, 45.0]
        assert util["labels"]["device_name"] == "Apple M5"
        raw = await svc.gpu(
            frm,
            to,
            "30s",
            ["gpu_utilization_pct"],
            STATE["cluster_id"],
            None,
            None,
            "max",
        )
        assert [p[1] for p in raw["series"][0]["points"]] == [40.0, 50.0] * 3
        inf = await svc.inference(
            frm,
            to,
            "1m",
            ["requests_delta", "error_rate_pct", "requests_per_minute", "health"],
            None,
            uuid.UUID(ids["dep"]),
            None,
            None,
            "sum",
        )
        by = {s["metric"]: [p[1] for p in s["points"]] for s in inf["series"]}
        assert by["requests_delta"] == [10.0, 20.0, 20.0]
        assert by["requests_per_minute"] == [10.0, 20.0, 20.0]
        assert by["error_rate_pct"][1] == pytest.approx(5.0)
        assert by["health"] == [1.0, 1.0, 1.0]
        three = await svc.inference(
            frm,
            to,
            "3m",
            ["requests_delta"],
            uuid.UUID(ids["model"]),
            None,
            None,
            None,
            "none",
        )
        assert sum(p[1] or 0 for p in three["series"][0]["points"]) == 50.0
        live = await svc.sessions_live(None, None, uuid.UUID(ids["inst"]))
        assert live[0]["waiting"] == 2 and live[0]["sessions"][0]["session_key"] == "slot-0"
        sess = await svc.sessions(uuid.UUID(ids["inst"]), "-10m", None, "1m", None, None)
        kv = next(s for s in sess["series"] if s["metric"] == "kv_cache_tokens")
        assert 17.0 in [p[1] for p in kv["points"]]
        overview = await svc.overview()
        assert overview["counts"]["nodes_online"] == 1
        assert overview["live"]["gpu_utilization_pct_avg"] == pytest.approx(77.0)
        assert overview["live"]["waiting_sessions"] == 2
        text_body = await render_fleet_exposition(session)
    assert 'gpudeck_gpu_utilization_ratio{node="fake-mac"' in text_body
    assert "gpudeck_engine_waiting_sessions" in text_body and 'deployment="qwen3"' in text_body
    assert "gpudeck_node_cpu_ratio" in text_body


@pytest.mark.asyncio
async def test_admin_routes_against_live_state(hub_process, hub_env, async_client):
    from app.main import app
    from app.middlewares.session_verification import get_current_admin

    app.dependency_overrides[get_current_admin] = lambda: SimpleNamespace(id=None, username="t")
    node_id = STATE["joined"]["node_id"]
    r = await async_client.get("/api/v1/nodes")
    assert r.status_code == 200 and r.json()["meta"]["total"] == 1
    node = r.json()["data"][0]
    assert node["status"] == "online" and node["device_count"] == 1
    assert node["metadata"]["config_applied_version"] >= 1
    r = await async_client.get(f"/api/v1/nodes/{node_id}/devices")
    dev = r.json()["data"][0]
    assert dev["live"]["utilization_pct"] == 77.0 and dev["instance_ids"] == [STATE["ids"]["inst"]]
    r = await async_client.get(f"/api/v1/nodes/{node_id}/instances")
    assert r.json()["data"][0]["live"]["waiting_sessions"] == 2
    r = await async_client.get("/api/v1/topology")
    topo = r.json()["data"]
    assert topo["clusters"][0]["nodes"][0]["devices"][0]["instances"][0]["state"] == "ready"
    r = await async_client.get(f"/api/v1/clusters/{STATE['cluster_id']}")
    assert r.json()["data"]["counts"]["nodes_online"] == 1
    r = await async_client.delete(f"/api/v1/clusters/{STATE['cluster_id']}")
    assert r.status_code == 409  # still has nodes
    r = await async_client.get("/api/v1/metrics/catalog")
    assert any(e["name"] == "kv_cache_usage_pct" for e in r.json()["data"])
    r = await async_client.get("/api/v1/metrics/gpu", params={"metrics": "nope"})
    assert r.status_code == 422
    r = await async_client.get("/api/v1/pki/ca")
    assert r.json()["data"]["fingerprint_sha256"] == cax.pem_fingerprint(
        STATE["joined"]["ca_bundle_pem"]
    )
    r = await async_client.post("/api/v1/pki/service-certificates", json={"kind": "api_layer"})
    assert r.status_code == 201, r.text
    issued = r.json()["data"]
    assert issued["private_key_pem"].startswith("-----BEGIN PRIVATE KEY")
    assert "O=gpudeck-services" in issued["certificate"]["subject"]
    r = await async_client.get("/api/v1/pki/certificates", params={"kind": "node_agent"})
    assert r.json()["meta"]["total"] == 1
    r = await async_client.get(f"/api/v1/nodes/{node_id}/cache")
    assert r.json()["data"]["used_bytes"] == 123
    app.dependency_overrides.pop(get_current_admin, None)


@pytest.mark.asyncio
async def test_command_round_trip_across_processes(hub_process):
    from app.services.agent_hub.service import AgentHub

    agent: FakeAgent = STATE["agent"]
    node_id = uuid.UUID(STATE["joined"]["node_id"])
    inst = STATE["ids"]["inst"]
    task = asyncio.create_task(
        AgentHub.send_command(
            node_id,
            "drain_instance",
            {"instance_id": inst, "timeout_seconds": 5},
            timeout=30,
        )
    )
    cmd = await agent.recv_type("command", timeout=10)
    assert cmd["payload"]["command"] == "drain_instance"
    assert cmd["payload"]["args"]["instance_id"] == inst
    await agent.send(
        "command_result",
        {"command": "drain_instance", "status": "accepted"},
        correlation_id=cmd["id"],
    )
    await agent.send(
        "command_result",
        {
            "command": "drain_instance",
            "status": "succeeded",
            "finished_at": _now(),
            "result": {"instance_id": inst, "drained": True},
        },
        correlation_id=cmd["id"],
    )
    outcome = await asyncio.wait_for(task, timeout=15)
    assert outcome.ok and outcome.command_id == uuid.UUID(cmd["id"])
    assert outcome.result == {"instance_id": inst, "drained": True}
    rows = await _db(
        "SELECT state, attempts, accepted_at FROM agent_commands WHERE id = :i",
        i=cmd["id"],
    )
    assert rows[0]["state"] == "succeeded" and rows[0]["attempts"] == 1
    assert rows[0]["accepted_at"] is not None


@pytest.mark.asyncio
async def test_pending_command_replayed_after_reconnect(hub_process, hub_env):
    from app.services.agent_hub.service import AgentHub

    agent: FakeAgent = STATE["agent"]
    node_id = uuid.UUID(STATE["joined"]["node_id"])
    await agent.close()

    async def disconnected():
        return not await AgentHub.is_connected(node_id)

    await _wait_for(disconnected)
    outcome = await AgentHub.send_command(
        node_id,
        "evict_cache",
        {"model_version_id": STATE["ids"]["version"]},
        timeout=120,
        wait=False,
    )
    assert outcome.state == "pending"
    await asyncio.sleep(0.5)
    rows = await _db("SELECT state FROM agent_commands WHERE id = :i", i=outcome.command_id)
    assert rows[0]["state"] == "pending"  # offline: stays pending
    agent2 = FakeAgent(
        STATE["ws_url"],
        str(node_id),
        hub_env["workdir"],
        STATE["joined"]["ca_bundle_pem"],
        STATE["agent_key"],
        STATE["joined"]["certificate_pem"],
    )
    await agent2.connect()
    ack = await agent2.hello(config_version=1)
    assert ack["payload"]["accepted"]
    replayed = await agent2.recv_type("command")
    assert replayed["id"] == str(outcome.command_id)  # same envelope id (idempotency key)
    await agent2.send(
        "command_result",
        {
            "command": "evict_cache",
            "status": "succeeded",
            "finished_at": _now(),
            "result": {"freed_bytes": 0},
        },
        correlation_id=replayed["id"],
    )
    final = await AgentHub.wait_for(outcome.command_id, 10)
    assert final.state == "succeeded"
    STATE["agent"] = agent2


@pytest.mark.asyncio
async def test_events_store_logs_and_raise_alerts(hub_process):
    from app.db.postgres.connection import get_session_maker
    from app.services.logs.service import LogService

    agent: FakeAgent = STATE["agent"]
    ids = STATE["ids"]
    await agent.send(
        "event",
        {
            "kind": "log",
            "severity": "info",
            "message": "engine output",
            "instance_id": ids["inst"],
            "deployment_id": ids["dep"],
            "log": {
                "source": "engine",
                "stream": "stderr",
                "lines": [
                    {"ts": _now(1), "level": "info", "text": "llama server ready"},
                    {"ts": _now(), "level": "error", "text": "slot 0 oom"},
                ],
            },
        },
    )
    await agent.send(
        "event",
        {
            "kind": "engine_crashed",
            "severity": "critical",
            "message": "llama-server exited",
            "instance_id": ids["inst"],
            "exit": {"code": 137, "oom": True},
        },
    )

    async def logs_in():
        async with get_session_maker()() as session:
            page = await LogService(session).query("-1h", None, search="oom")
        return page if page["items"] else None

    page = await _wait_for(logs_in)
    chunk = page["items"][0]
    assert chunk["level"] == "error" and chunk["line_count"] == 2 and chunk["node_name"]

    async def crashed():
        rows = await _db("SELECT state, health FROM instances WHERE id = :i", i=ids["inst"])
        return rows[0]["state"] == "error"

    await _wait_for(crashed)
    alerts = await _db("SELECT severity, event_type FROM alert_events")
    assert any(a["event_type"] == "node.engine_crashed" for a in alerts)


@pytest.mark.asyncio
async def test_live_stream_and_trace_index(hub_process, hub_env):
    from app.db.postgres.connection import get_session_maker
    from app.services.metrics.live import (
        LiveFilter,
        live_event_stream,
        publish_live_event,
    )
    from app.services.traces.exporter import TraceIndexExporter
    from app.services.traces.service import TraceService
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.trace import SpanKind

    flt = LiveFilter(topics={"metrics", "swap"})
    gen = live_event_stream(flt, interval_seconds=1)
    first = await asyncio.wait_for(gen.__anext__(), 10)
    assert first["event"] == "metrics" and "overview" in first["data"]
    await publish_live_event("swap", {"swap_id": "s1", "state": "loading"})
    deadline = time.monotonic() + 10
    got = None
    while time.monotonic() < deadline and got is None:
        event = await asyncio.wait_for(gen.__anext__(), 10)
        if event["event"] == "swap":
            got = event
    await gen.aclose()
    assert got and got["data"]["state"] == "loading" and got["id"]

    provider = TracerProvider(resource=Resource.create({"service.name": "gpu-deck-serving"}))
    provider.add_span_processor(SimpleSpanProcessor(TraceIndexExporter()))
    tracer = provider.get_tracer("hub-test")
    request_id = uuid.uuid4()
    with tracer.start_as_current_span("POST /v1/chat/completions", kind=SpanKind.SERVER) as root:
        root.set_attribute("gpudeck.request_id", str(request_id))
        root.set_attribute("gpudeck.format", "openai")
        root.set_attribute("http.status_code", 200)
        with tracer.start_as_current_span("upstream", kind=SpanKind.CLIENT):
            pass
    provider.shutdown()
    async with get_session_maker()() as session:
        roots, total = await TraceService(session).search(
            1, 10, "-1h", None, {"request_id": request_id}
        )
        assert total == 1 and roots[0]["service"] == "api_layer"
        trace = await TraceService(session).get(roots[0]["trace_id"])
    assert len(trace["spans"]) == 2 and trace["root"]["format"] == "openai"


@pytest.mark.asyncio
async def test_crl_revocation_disconnects_and_blocks_agent(hub_process, hub_env):
    from app.db.postgres.connection import get_session_maker
    from app.services.nodes.service import NodeService

    agent: FakeAgent = STATE["agent"]
    node_id = uuid.UUID(STATE["joined"]["node_id"])
    cert_rows = await _db("SELECT id FROM node_certificates WHERE node_id = :n", n=str(node_id))
    async with get_session_maker()() as session:
        revoked = await NodeService(session).revoke_certificate(
            node_id, cert_rows[0]["id"], "compromised", None
        )
        await session.commit()
    assert revoked.revoked_at is not None and revoked.is_active is False
    with pytest.raises(websockets.ConnectionClosed) as closed:
        while True:
            await agent.recv(timeout=10)
    assert closed.value.rcvd.code == 4003
    # The revoked cert still passes TLS (same CA) but the hub refuses it at hello.
    again = FakeAgent(
        STATE["ws_url"],
        str(node_id),
        hub_env["workdir"],
        STATE["joined"]["ca_bundle_pem"],
        STATE["agent_key"],
        STATE["joined"]["certificate_pem"],
    )
    await again.connect()
    ack = await again.hello()
    assert ack["payload"]["accepted"] is False and ack["payload"]["reason"] == "cert_revoked"
    with pytest.raises(websockets.ConnectionClosed) as closed2:
        await again.recv(timeout=10)
    assert closed2.value.rcvd.code == 4003
    async with get_session_maker()() as session:
        from app.services.pki.service import PkiService

        crl = await PkiService.from_session(session).crl()
    assert revoked.serial in crl["serials"] and crl["crl_pem"].startswith("-----BEGIN X509 CRL")


@pytest.mark.asyncio
async def test_offline_detection_marks_node_and_instances(hub_process):
    from app.services.nodes.maintenance import mark_stale_nodes_offline

    node_id = STATE["joined"]["node_id"]
    await _db(
        "UPDATE nodes SET status = 'online', last_heartbeat_at = now() - interval '1 hour' "
        "WHERE id = :n",
        n=node_id,
    )
    await _db(
        "UPDATE instances SET state = 'ready', health = 'healthy' WHERE node_id = :n",
        n=node_id,
    )
    went = await mark_stale_nodes_offline()
    assert uuid.UUID(node_id) in went
    rows = await _db("SELECT status FROM nodes WHERE id = :n", n=node_id)
    assert rows[0]["status"] == "offline"
    rows = await _db("SELECT health FROM instances WHERE node_id = :n", n=node_id)
    assert rows[0]["health"] == "unknown"
    log_text = Path(hub_process["log"]).read_text()
    assert "Traceback" not in log_text, log_text[-3000:]


def test_hub_logged_no_secrets(hub_process):
    text_body = Path(hub_process["log"]).read_text()
    assert STATE["token"].token not in text_body
    assert "PRIVATE KEY" not in text_body


@pytest.mark.asyncio
async def test_ingest_creates_missing_partition_and_retries(hub_env):
    """SQLSTATE 23514 (missing partition) -> create_daily_partitions() -> retry once."""
    from app.db.postgres.connection import get_session_maker
    from app.db.postgres.repositories.metric_repository import MetricRepository

    day = (datetime.now(timezone.utc) + timedelta(days=5)).replace(hour=12)
    name = f"metric_samples_gpu_p{day.strftime('%Y%m%d')}"
    await _db(f"DROP TABLE IF EXISTS {name}")
    async with get_session_maker()() as session:
        inserted = await MetricRepository(session).insert_gpu_samples(
            [
                {
                    "ts": day,
                    "node_id": uuid.uuid4(),
                    "device_id": uuid.uuid4(),
                    "utilization_pct": 1.0,
                }
            ],
            days_ahead=7,
        )
        await session.commit()
    assert inserted == 1
    rows = await _db("SELECT to_regclass(:n) AS t", n=name)
    assert rows[0]["t"] is not None


@pytest.mark.asyncio
async def test_metrics_jobs_partitions_rollup_and_retention(hub_env):
    from app.workers.tasks.metrics_tasks import _create_partitions, _retention, _rollup

    await _create_partitions()
    horizon = datetime.now(timezone.utc) + timedelta(days=settings.PARTITIONS_DAYS_AHEAD)
    rows = await _db(
        "SELECT to_regclass(:n) AS t",
        n=f"metric_rollups_gpu_1m_p{horizon.strftime('%Y%m%d')}",
    )
    assert rows[0]["t"] is not None
    old = datetime.now(timezone.utc) - timedelta(days=400)
    old_name = f"traces_p{old.strftime('%Y%m%d')}"
    await _db(
        f"CREATE TABLE {old_name} PARTITION OF traces FOR VALUES FROM "
        f"('{old.strftime('%Y-%m-%d')}') TO ('{(old + timedelta(days=1)).strftime('%Y-%m-%d')}')"
    )
    dropped = await _retention()
    assert dropped["traces"] >= 1
    rows = await _db("SELECT to_regclass(:n) AS t", n=old_name)
    assert rows[0]["t"] is None
    result = await _rollup()
    assert result["rolled"] is True


@pytest.mark.asyncio
async def test_session_agent_hub_writes_in_caller_transaction(hub_env):
    from app.db.postgres.connection import get_session_maker
    from app.services.agent_hub.service import get_agent_hub

    node_id = uuid.UUID(STATE["joined"]["node_id"])
    async with get_session_maker()() as session:
        hub = get_agent_hub(session)
        command_id = await hub.send_command(
            node_id,
            "unload_model",
            {"instance_id": STATE["ids"]["inst"]},
            timeout_seconds=60,
        )
        await session.rollback()  # caller aborts -> no command row, no dispatch
    assert not await _db("SELECT id FROM agent_commands WHERE id = :i", i=command_id)
    async with get_session_maker()() as session:
        command_id = await get_agent_hub(session).send_command(
            node_id,
            "unload_model",
            {"instance_id": STATE["ids"]["inst"]},
            timeout_seconds=60,
        )
        await session.commit()
    rows = await _db("SELECT state, command_type FROM agent_commands WHERE id = :i", i=command_id)
    assert rows[0]["command_type"] == "unload_model" and rows[0]["state"] == "pending"
