"""
Integration: cloud VM provisioning and lifecycle against the real local
Postgres + Redis, with an in-memory cloud provider (no cloud credentials).

Isolation: scratch schema ``w2ctest_<hex>`` built from every migration up to
650, a scratch Redis prefix, audit events to an in-memory sink, alerts and the
Celery enqueue captured. The VM's real bootstrap script is used: the join token
is read back out of the user-data and joined through the fleet domain's ``NodeJoinService``
with a real CSR, so "cloud-init token -> node" is exercised end to end.
Responses are validated against the ``Cloud`` schemas in admin.yaml.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import asyncpg
import httpx
import pytest
import pytest_asyncio
import yaml
from app.core.settings import settings
from app.services.cloud.base import (
    VM_PENDING,
    VM_RUNNING,
    VM_STOPPED,
    VM_TERMINATED,
    CloudInstanceType,
    CloudProvider,
    CloudQuota,
    CloudQuotaError,
    CloudRegion,
    CloudVm,
    CredentialCheck,
    VmSpec,
)
from jsonschema import Draft202012Validator
from referencing import Registry
from referencing.jsonschema import DRAFT202012

pytestmark = [
    pytest.mark.integration,
    pytest.mark.db,
    pytest.mark.asyncio(loop_scope="session"),
]

ROOT = Path(__file__).resolve().parents[4]
MIGRATIONS = Path(__file__).resolve().parents[2] / "app/db/postgres/alembic/migrations"
API = settings.API_V1_PREFIX
GIB = 1024**3


def _plain_dsn() -> str:
    return settings.get_database_url().replace("postgresql+asyncpg://", "postgresql://", 1)


def _migration_order() -> List[str]:
    """Every migration folder in alembic chain order (tests/schema_helpers.migration_chain)."""
    from tests.schema_helpers import migration_chain

    return [folder.name for folder in migration_chain()]


# ==========================================
# Contract validation (admin.yaml components)
# ==========================================


_SPEC = yaml.safe_load((ROOT / "libs/contracts/openapi/admin.yaml").read_text())
_REGISTRY = Registry().with_resource("urn:admin", DRAFT202012.create_resource(_SPEC))


def _schema_validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(
        {"$ref": f"urn:admin#/components/schemas/{name}"}, registry=_REGISTRY
    )


def assert_contract(name: str, data: Any) -> None:
    errors = list(_schema_validator(name).iter_errors(data))
    assert not errors, [f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in errors[:5]]


# ==========================================
# Fake provider
# ==========================================


class FakeCloud(CloudProvider):
    name = "aws"
    display_name = "fake"
    cluster_type = "cloud_aws"

    def __init__(self) -> None:
        super().__init__({})
        self.vms: Dict[str, Dict[str, Any]] = {}
        self.specs: List[VmSpec] = []
        self.calls: List[str] = []
        self.fail_next: Optional[Exception] = None
        self.credentials_seen: List[Dict[str, Any]] = []

    def check_credentials(self):
        return CredentialCheck(ok=True, account="123456789012")

    def list_regions(self):
        return [CloudRegion(id="us-east-1", name="us-east-1", zones=["us-east-1a"])]

    def list_instance_types(self, region):
        self.calls.append(f"types:{region}")
        return [
            CloudInstanceType(
                id="g5.xlarge",
                gpu_model="NVIDIA A10G",
                gpu_vendor="nvidia",
                gpu_count=1,
                gpu_vram_bytes=24 * GIB,
                vcpus=4,
                memory_bytes=16 * GIB,
                price_per_hour_usd=1.006,
                price_source="aws:pricing:on-demand:linux",
                regions=[region],
                family="g5",
            ),
            CloudInstanceType(
                id="p4d.24xlarge",
                gpu_model="NVIDIA A100",
                gpu_vendor="nvidia",
                gpu_count=8,
                gpu_vram_bytes=40 * GIB,
                vcpus=96,
                regions=[region],
                family="p4d",
            ),
        ]

    def list_quotas(self, region):
        return [
            CloudQuota(
                name="Running On-Demand G and VT instances",
                used=None,
                limit=64,
                unit="vCPU",
                region=region,
            )
        ]

    def create_vm(self, spec: VmSpec) -> CloudVm:
        self.calls.append(f"create:{spec.name}")
        if self.fail_next is not None:
            exc, self.fail_next = self.fail_next, None
            raise exc
        self.specs.append(spec)
        vm_id = f"i-{len(self.specs):04d}"
        self.vms[vm_id] = {"state": VM_PENDING, "spec": spec}
        return CloudVm(id=vm_id, state=VM_PENDING, provider_state="pending", zone="us-east-1a")

    def get_vm(self, vm_id, *, region, zone=None):
        self.calls.append(f"get:{vm_id}")
        vm = self.vms.get(vm_id)
        if vm is None:
            return None
        return CloudVm(
            id=vm_id,
            state=vm["state"],
            provider_state=vm["state"],
            private_ip="10.0.0.7",
            public_ip="54.0.0.7",
            zone="us-east-1a",
        )

    def stop_vm(self, vm_id, *, region, zone=None):
        self.calls.append(f"stop:{vm_id}")
        self.vms[vm_id]["state"] = VM_STOPPED

    def start_vm(self, vm_id, *, region, zone=None):
        self.calls.append(f"start:{vm_id}")
        self.vms[vm_id]["state"] = VM_RUNNING

    def terminate_vm(self, vm_id, *, region, zone=None):
        self.calls.append(f"terminate:{vm_id}")
        if vm_id in self.vms:
            self.vms[vm_id]["state"] = VM_TERMINATED


# ==========================================
# Fixtures
# ==========================================


class Env:
    def __init__(self) -> None:
        self.tag = uuid.uuid4().hex[:10]
        self.schema = f"w2ctest_{self.tag}"
        self.prefix = f"gpu-deck:w2ctest:{self.tag}:"
        self.audit: List[Dict[str, Any]] = []
        self.alerts: List[Dict[str, Any]] = []
        self.enqueued: List[uuid.UUID] = []

    async def sql(self, query: str, *args):
        conn = await asyncpg.connect(_plain_dsn(), server_settings={"search_path": self.schema})
        try:
            return await conn.fetch(query, *args)
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
        for name in _migration_order():
            sql = "\n".join(
                line
                for line in (MIGRATIONS / name / "up.sql").read_text().splitlines()
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


@pytest_asyncio.fixture(loop_scope="session")
async def world(env, monkeypatch):
    """Fresh tables, a vault credential, a cloud_aws cluster and the fake provider."""
    from app.db.postgres.connection import get_session_maker
    from app.services.alerts import service as alerts_service
    from app.services.cloud import credentials as cloud_credentials
    from app.services.cloud import service as cloud_service
    from app.services.vault import VaultService

    await env.sql(
        "TRUNCATE cloud_instances, nodes, join_tokens, clusters, credentials, price_plans,"
        " node_certificates CASCADE"
    )
    env.audit.clear()
    env.alerts.clear()
    env.enqueued.clear()
    fake = FakeCloud()

    def factory(provider, creds):
        fake.credentials_seen.append(creds)
        return fake

    cloud_credentials.set_provider_factory(factory)

    async def fake_emit(event_type, severity, title, message=None, **kw):
        env.alerts.append({"event": event_type, "severity": severity, "title": title, **kw})
        return uuid.uuid4()

    monkeypatch.setattr(alerts_service.alerts, "emit", staticmethod(fake_emit))
    monkeypatch.setattr(cloud_service, "enqueue_advance", env.enqueued.append)

    async with get_session_maker()() as s:
        cred = await VaultService(s).upsert_secret(
            "aws-lab",
            json.dumps({"access_key_id": "AKIAFAKE", "secret_access_key": "fake-secret"}),
            type="cloud_account",
            provider="aws",
        )
        await s.commit()
    cluster_id = uuid.uuid4()
    await env.sql(
        "INSERT INTO clusters (id, name, type, region, credential_id, config)"
        " VALUES ($1, 'lab-aws', 'cloud_aws', 'us-east-1', $2, $3::jsonb)",
        cluster_id,
        cred["id"],
        json.dumps(
            {
                "cloud": {
                    "max_nodes": 2,
                    "subnet_id": "subnet-123",
                    "security_group_ids": ["sg-1"],
                }
            }
        ),
    )
    w = SimpleNamespace(fake=fake, cluster_id=cluster_id, credential_id=cred["id"], env=env)
    try:
        yield w
    finally:
        cloud_credentials.set_provider_factory(None)


def admin_app():
    from app.api.v1.routes import cloud_nodes, cloud_providers
    from app.api.v1.routes_v2 import cloud_price_plans  # v2, mounted here only
    from app.core.rate_limiter import limiter
    from app.middlewares.session_verification import get_current_admin
    from fastapi import FastAPI

    app = FastAPI()
    app.state.limiter = limiter
    app.include_router(cloud_providers.router, prefix=API)
    app.include_router(cloud_nodes.router, prefix=API)
    app.include_router(cloud_price_plans.router, prefix=API)
    app.dependency_overrides[get_current_admin] = lambda: SimpleNamespace(id=None)
    return app


def client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=admin_app()), base_url="http://adm")


async def advance(row_id) -> Optional[str]:
    from app.db.postgres.connection import get_session_maker
    from app.services.cloud.lifecycle import CloudLifecycle

    async with get_session_maker()() as s:
        status = await CloudLifecycle(s).advance(uuid.UUID(str(row_id)))
        await s.commit()
        return status


async def tick() -> Dict[str, int]:
    from app.db.postgres.connection import get_session_maker
    from app.services.cloud.lifecycle import CloudLifecycle

    async with get_session_maker()() as s:
        summary = await CloudLifecycle(s).tick()
        await s.commit()
        return summary


async def agent_joins(user_data: str) -> uuid.UUID:
    """Play the VM: take the token from the cloud-init script and join with a real CSR."""
    from app.db.postgres.connection import get_session_maker
    from app.services.nodes.service import JoinRequest, NodeJoinService
    from app.services.pki import ca as cax

    token = re.search(r"^JOIN_TOKEN=(\S+)$", user_data, re.M).group(1)
    name = re.search(r"^NODE_NAME=(\S+)$", user_data, re.M).group(1).strip("'")
    labels = re.search(r"^LABELS=\((.*)\)$", user_data, re.M).group(1)
    assert "gpu-deck/cloud-provider=aws" in labels
    async with get_session_maker()() as s:
        result = await NodeJoinService(s).join(
            JoinRequest(
                join_token=token,
                csr_pem=cax.build_csr(cax.generate_private_key("ecdsa_p256")),
                hostname="ip-10-0-0-7",
                os="linux",
                arch="x86_64",
                agent_version="1.0.0",
                name=name,
                ip_addresses=["10.0.0.7"],
            ),
            client_ip="54.0.0.7",
        )
        await s.commit()
    return uuid.UUID(result["node_id"])


async def vm_downloads_agent(user_data: str) -> None:
    """Replay the script's download step against the agent download endpoint."""
    import hashlib

    from app.api.v1.routes import agent_downloads
    from app.middlewares.session_verification import get_optional_admin
    from app.services.nodes.agent_downloads import list_artifacts
    from fastapi import FastAPI

    artifacts = list_artifacts()
    if not artifacts:
        pytest.skip("no agent build in apps/node-agent/dist/agent")
    target = artifacts[0].target
    token = re.search(r"^JOIN_TOKEN=(\S+)$", user_data, re.M).group(1)
    version = re.search(r"^AGENT_VERSION=(\S+)$", user_data, re.M).group(1)
    assert 'URL="$DOWNLOAD_BASE/agent/v1/downloads/$AGENT_VERSION/$TARGET"' in user_data
    app = FastAPI()
    app.include_router(agent_downloads.router, prefix=API)
    app.dependency_overrides[get_optional_admin] = lambda: None
    url = f"{API}/agent/downloads/{version}/{target}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://hub"
    ) as c:
        r = await c.get(url, headers={"X-GPUDeck-Join-Token": token})
        assert r.status_code == 200, r.text
        assert r.headers["x-checksum-sha256"] == hashlib.sha256(r.content).hexdigest()
        r = await c.get(f"{url}/sha256", headers={"X-GPUDeck-Join-Token": token})
        assert (
            r.status_code == 200
            and r.text.split()[0]
            == hashlib.sha256(
                (await c.get(url, headers={"X-GPUDeck-Join-Token": token})).content
            ).hexdigest()
        )
        assert (await c.get(url)).status_code in (401, 403)


async def row(env, row_id):
    return (await env.sql("SELECT * FROM cloud_instances WHERE id = $1", uuid.UUID(str(row_id))))[0]


# ==========================================
# Tests
# ==========================================


async def test_catalog_routes(world):
    async with client() as c:
        r = await c.get(f"{API}/cloud/providers")
        assert r.status_code == 200
        for item in r.json()["data"]:
            assert_contract("CloudProvider", item)
        r = await c.get(
            f"{API}/cloud/providers/aws/instance-types",
            params={
                "region": "us-east-1",
                "cluster_id": str(world.cluster_id),
                "min_vram_gb": 100,
            },
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert [t["id"] for t in data] == ["p4d.24xlarge"]
        assert_contract("CloudInstanceType", data[0])
        # cached: the second listing does not hit the provider
        calls = len(world.fake.calls)
        r = await c.get(
            f"{API}/cloud/providers/aws/instance-types",
            params={"region": "us-east-1", "credential_id": str(world.credential_id)},
        )
        assert len(r.json()["data"]) == 2 and len(world.fake.calls) == calls
        r = await c.get(
            f"{API}/cloud/providers/aws/regions",
            params={"credential_id": str(world.credential_id)},
        )
        assert_contract("CloudRegion", r.json()["data"][0])
        r = await c.get(
            f"{API}/cloud/providers/aws/quotas",
            params={"region": "us-east-1", "cluster_id": str(world.cluster_id)},
        )
        assert_contract("CloudQuota", r.json()["data"][0])
        r = await c.post(
            f"{API}/cloud/providers/aws/check-credential",
            json={"credential_id": str(world.credential_id)},
        )
        assert r.json()["data"] == {
            "ok": True,
            "provider": "aws",
            "account": "123456789012",
            "detail": None,
        }
        assert_contract("CloudCredentialCheck", r.json()["data"])
        # wrong provider for this cluster / credential
        r = await c.get(
            f"{API}/cloud/providers/gcp/regions",
            params={"cluster_id": str(world.cluster_id)},
        )
        assert r.status_code == 422
        r = await c.get(
            f"{API}/cloud/providers/gcp/regions",
            params={"credential_id": str(world.credential_id)},
        )
        assert r.status_code == 422 and r.json()["error"]["code"] == "CLOUD_CREDENTIAL_INVALID"
    assert world.fake.credentials_seen[0]["access_key_id"] == "AKIAFAKE"  # parsed from the vault
    usage = await world.env.sql("SELECT purpose FROM credential_usage_history")
    assert {u["purpose"] for u in usage} >= {"cloud:aws:catalog", "cloud:aws:check"}


async def test_provision_join_price_and_terminate(world):
    env = world.env
    async with client() as c:
        body = {
            "cluster_id": str(world.cluster_id),
            "instance_type": "g5.xlarge",
            "labels": {"team": "ml"},
        }
        r = await c.post(f"{API}/cloud/nodes", json={**body, "dry_run": True})
        assert r.status_code == 200, r.text
        preview = r.json()["data"]
        assert_contract("CloudProvisionPreview", preview)
        assert preview["estimated_monthly_usd"] == round(1.006 * 730, 2)
        assert preview["options"] == {
            "subnet_id": "subnet-123",
            "security_group_ids": ["sg-1"],
        }
        assert await env.sql("SELECT 1 FROM cloud_instances") == []

        r = await c.post(f"{API}/cloud/nodes", json={**body, "name": "lab-g5-a"})
        assert r.status_code == 201, r.text
        created = r.json()["data"][0]
        assert_contract("CloudNode", created)
        assert created["status"] == "pending" and created["price_per_hour_usd"] == 1.006
        assert created["tags"]["gpu-deck:managed"] == "true"
        assert env.enqueued == [uuid.UUID(created["id"])]
        cid = created["id"]

        r = await c.post(f"{API}/cloud/nodes", json={**body, "count": 2})
        assert r.status_code == 409 and r.json()["error"]["code"] == "CLOUD_MAX_NODES"
        r = await c.post(f"{API}/cloud/nodes", json={**body, "instance_type": "x9.huge"})
        assert r.status_code == 422 and r.json()["error"]["code"] == "CLOUD_INVALID_REQUEST"

    assert await advance(cid) == "provisioning"
    spec = world.fake.specs[0]
    assert spec.client_token == cid and spec.options["subnet_id"] == "subnet-123"
    assert spec.tags["gpu-deck:cloud-instance-id"] == cid
    assert "BEGIN CERTIFICATE" in spec.user_data and "gdj_" in spec.user_data
    token = (await env.sql("SELECT * FROM join_tokens"))[0]
    assert token["node_name_hint"] == "lab-g5-a" and json.loads(token["labels"])["team"] == "ml"
    assert (await row(env, cid))["join_token_id"] == token["id"]
    assert await advance(cid) == "provisioning"  # VM still pending
    world.fake.vms["i-0001"]["state"] = VM_RUNNING
    assert await advance(cid) == "booting"

    await vm_downloads_agent(spec.user_data)
    node_id = await agent_joins(spec.user_data)
    assert await advance(cid) == "running"
    r_ = await row(env, cid)
    assert r_["node_id"] == node_id and str(r_["private_ip"]) == "10.0.0.7"
    node = (await env.sql("SELECT * FROM nodes WHERE id = $1", node_id))[0]
    assert node["cloud_instance_id"] == "i-0001" and node["cloud_instance_type"] == "g5.xlarge"
    meta = json.loads(node["metadata"])
    assert meta["price_plan_suggestion"]["usd_per_gpu_minute"] == round(1.006 / 60, 6)
    assert meta["cloud"]["cloud_instance_id"] == cid

    async with client() as c:
        r = await c.get(f"{API}/cloud/nodes/{cid}")
        got = r.json()["data"]
        assert_contract("CloudNode", got)
        assert got["node_name"] == "lab-g5-a" and got["node_status"] == "pending"
        r = await c.post(f"{API}/cloud/nodes/{cid}/apply-price-plan")
        assert r.status_code == 200, r.text
        plan = (await env.sql("SELECT * FROM price_plans"))[0]
        assert float(plan["usd_per_gpu_minute"]) == pytest.approx(1.006 / 60, abs=1e-6)
        node = (await env.sql("SELECT * FROM nodes WHERE id = $1", node_id))[0]
        assert node["price_plan_id"] == plan["id"]

        r = await c.get(
            f"{API}/cloud/nodes",
            params={
                "cluster_id": str(world.cluster_id),
                "status": ["running", "booting"],
            },
        )
        assert r.json()["meta"]["total"] == 1

        r = await c.delete(f"{API}/cloud/nodes/{cid}")
        assert r.status_code == 409

        # the node is online: terminate drains it first (no instances -> drained at once)
        await env.sql("UPDATE nodes SET status = 'online' WHERE id = $1", node_id)
        r = await c.post(f"{API}/cloud/nodes/{cid}/terminate", json={"reason": "done"})
        assert r.status_code == 200, r.text
        assert r.json()["data"]["status"] == "terminating"
    node = (await env.sql("SELECT status, drain_reason FROM nodes WHERE id = $1", node_id))[0]
    assert node["status"] == "drained" and node["drain_reason"] == "done"
    assert "terminate:i-0001" in world.fake.calls
    assert await advance(cid) == "terminated"
    node = (await env.sql("SELECT status, deleted_at FROM nodes WHERE id = $1", node_id))[0]
    assert node["status"] == "removed"
    certs = await env.sql("SELECT revoked_at FROM node_certificates WHERE node_id = $1", node_id)
    assert certs and all(c["revoked_at"] is not None for c in certs)
    async with client() as c:
        r = await c.delete(f"{API}/cloud/nodes/{cid}")
        assert r.status_code == 204
        r = await c.get(f"{API}/cloud/nodes/{cid}")
        assert r.status_code == 404
    events = [e["event_type"] for e in env.audit]
    for expected in (
        "cloud.instance_requested",
        "cloud.instance_created",
        "cloud.instance_joined",
        "cloud.price_plan_applied",
        "cloud.instance_terminate_requested",
        "cloud.instance_terminated",
    ):
        assert expected in events, expected
    full_token = re.search(r"^JOIN_TOKEN=(\S+)$", spec.user_data, re.M).group(1)
    for event in env.audit:
        blob = json.dumps(event, default=str)
        assert "fake-secret" not in blob and full_token not in blob, event


async def test_quota_error_is_surfaced_and_token_revoked(world):
    env = world.env
    async with client() as c:
        r = await c.post(
            f"{API}/cloud/nodes",
            json={"cluster_id": str(world.cluster_id), "instance_type": "p4d.24xlarge"},
        )
        cid = r.json()["data"][0]["id"]
    world.fake.fail_next = CloudQuotaError(
        "You have requested more vCPU capacity than your current vCPU limit of 0",
        provider="aws",
        provider_code="VcpuLimitExceeded",
    )
    assert await advance(cid) == "error"
    r_ = await row(env, cid)
    err = json.loads(r_["last_error"])
    assert err["code"] == "CLOUD_QUOTA_EXCEEDED" and err["provider_code"] == "VcpuLimitExceeded"
    assert err["stage"] == "create" and "vCPU limit" in r_["status_message"]
    token = (await env.sql("SELECT revoked_at FROM join_tokens"))[0]
    assert token["revoked_at"] is not None
    assert [a["event"] for a in env.alerts] == ["cloud.instance_failed"]
    assert env.alerts[0]["severity"] == "critical"
    async with client() as c:
        r = await c.post(f"{API}/cloud/nodes/{cid}/terminate")
        assert r.json()["data"]["status"] == "terminated"  # nothing was created


async def _running_node(world, name: str, **extra) -> Dict[str, Any]:
    async with client() as c:
        r = await c.post(
            f"{API}/cloud/nodes",
            json={
                "cluster_id": str(world.cluster_id),
                "instance_type": "g5.xlarge",
                "name": name,
                **extra,
            },
        )
        assert r.status_code == 201, r.text
        cid = r.json()["data"][0]["id"]
    await advance(cid)
    vm_id = (await row(world.env, cid))["provider_instance_id"]
    world.fake.vms[vm_id]["state"] = VM_RUNNING
    await advance(cid)
    node_id = await agent_joins(world.fake.vms[vm_id]["spec"].user_data)
    assert await advance(cid) == "running"
    await world.env.sql("UPDATE nodes SET status = 'online' WHERE id = $1", node_id)
    return {"id": cid, "vm": vm_id, "node_id": node_id}


async def test_stop_start_and_external_changes(world):
    env = world.env
    n = await _running_node(world, "lab-g5-s")
    async with client() as c:
        r = await c.post(f"{API}/cloud/nodes/{n['id']}/stop", json={"force": True})
        assert r.json()["data"]["status"] == "stopping"
        assert await advance(n["id"]) == "stopped"
        await env.sql("UPDATE nodes SET status = 'offline' WHERE id = $1", n["node_id"])
        r = await c.post(f"{API}/cloud/nodes/{n['id']}/start")
        assert r.json()["data"]["status"] == "starting"
    assert await advance(n["id"]) == "starting"  # agent not back yet
    await env.sql("UPDATE nodes SET status = 'online' WHERE id = $1", n["node_id"])
    assert await advance(n["id"]) == "running"
    # stopped outside GPU Deck, then deleted outside GPU Deck
    world.fake.vms[n["vm"]]["state"] = VM_STOPPED
    assert await advance(n["id"]) == "stopped"
    del world.fake.vms[n["vm"]]
    assert await advance(n["id"]) == "terminated"
    assert (await row(env, n["id"]))["status_message"] == "VM was deleted outside GPU Deck"


async def test_idle_auto_terminate_and_boot_timeout(world):
    env = world.env
    n = await _running_node(world, "lab-g5-idle", auto_terminate_idle_minutes=5)
    summary = await tick()
    assert summary["idle_terminated"] == 0
    assert (await row(env, n["id"]))["idle_since"] is not None
    await env.sql(
        "UPDATE cloud_instances SET idle_since = now() - interval '6 minutes' WHERE id = $1",
        uuid.UUID(n["id"]),
    )
    summary = await tick()
    assert summary["idle_terminated"] == 1
    r_ = await row(env, n["id"])
    assert r_["status"] == "draining" and r_["pending_action"] == "terminate"
    await tick()  # node drained (no instances) -> terminate call
    assert (await row(env, n["id"]))["status"] == "terminating"
    await tick()
    assert (await row(env, n["id"]))["status"] == "terminated"
    assert "cloud.instance_idle_terminated" in [a["event"] for a in env.alerts]

    # a VM whose agent never joins goes to error after CLOUD_BOOT_TIMEOUT_MINUTES
    async with client() as c:
        r = await c.post(
            f"{API}/cloud/nodes",
            json={"cluster_id": str(world.cluster_id), "instance_type": "g5.xlarge"},
        )
        cid = r.json()["data"][0]["id"]
    await tick()
    vm_id = (await row(env, cid))["provider_instance_id"]
    world.fake.vms[vm_id]["state"] = VM_RUNNING
    await tick()
    assert (await row(env, cid))["status"] == "booting"
    late = datetime.now(timezone.utc) - timedelta(minutes=settings.CLOUD_BOOT_TIMEOUT_MINUTES + 1)
    await env.sql(
        "UPDATE cloud_instances SET launched_at = $2 WHERE id = $1",
        uuid.UUID(cid),
        late,
    )
    await tick()
    r_ = await row(env, cid)
    assert r_["status"] == "error"
    assert json.loads(r_["last_error"])["code"] == "CLOUD_AGENT_JOIN_TIMEOUT"
