"""
Integration (real Postgres + Redis, scratch schema): multi-use enrolment tokens,
the hub serving everything a GPU machine needs (install script, CA PEM, agent
build fetched from the release and cached), and the API layer's client
certificate provisioned from the vault without manual steps.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import asyncpg
import httpx
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


class Env:
    def __init__(self) -> None:
        self.tag = uuid.uuid4().hex[:10]
        self.schema = f"enroltest_{self.tag}"
        self.prefix = f"gpu-deck:enroltest:{self.tag}:"
        self.audit: List[Dict[str, Any]] = []
        self.cluster = uuid.uuid4()

    async def sql(self, query: str, *args):
        conn = await asyncpg.connect(_dsn(), server_settings={"search_path": self.schema})
        try:
            return await conn.fetch(query, *args)
        finally:
            await conn.close()

    def session(self):
        from app.db.postgres.connection import get_session_maker

        return get_session_maker()()


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def env(tmp_path_factory):
    from app.db.postgres import connection
    from app.db.redis import client as redis_client_module
    from app.db.redis.cache_service import CacheService
    from app.services.audit import audit
    from app.services.pki.service import reset_ca_cache_for_tests
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
    saved = {
        "engine": connection.engine,
        "prefix": settings.REDIS_KEY_PREFIX,
        "pki_dir": settings.PKI_CA_DIR,
        "cert": settings.API_LAYER_CLIENT_CERT_PATH,
        "key": settings.API_LAYER_CLIENT_KEY_PATH,
    }
    connection.engine = e.engine
    settings.REDIS_KEY_PREFIX = e.prefix
    settings.PKI_CA_DIR = str(tmp_path_factory.mktemp("pki"))
    reset_ca_cache_for_tests()
    await CacheService.reset()
    await redis_client_module.close_redis()
    audit.set_sink(e.audit.append)
    await e.sql("INSERT INTO clusters (id, name) VALUES ($1, 'k8s-pool')", e.cluster)
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
            settings.PKI_CA_DIR = saved["pki_dir"]
            settings.API_LAYER_CLIENT_CERT_PATH = saved["cert"]
            settings.API_LAYER_CLIENT_KEY_PATH = saved["key"]
            reset_ca_cache_for_tests()
            await e.engine.dispose()
            conn = await asyncpg.connect(_dsn())
            try:
                await drop_scratch_schema(conn, e.schema)
            finally:
                await conn.close()


def _csr() -> str:
    from app.services.pki import ca as cax

    key = cax.generate_private_key("ecdsa_p256")
    return cax.build_csr(key, "pending", cax.NODE_ORG)


async def _create_token(env: Env, **kw):
    from app.schemas.fleet_schema import JoinTokenCreate
    from app.services.nodes.service import NodeService

    async with env.session() as s:
        out = await NodeService(s).create_join_token(
            JoinTokenCreate(cluster_id=env.cluster, **kw), actor_id=None
        )
        await s.commit()
    return out


async def _join(env: Env, token: str, hostname: str):
    from app.services.nodes.service import JoinRequest, NodeJoinService

    async with env.session() as s:
        try:
            return await NodeJoinService(s).join(
                JoinRequest(
                    join_token=token,
                    csr_pem=_csr(),
                    hostname=hostname,
                    name=None,
                    os="linux",
                    arch="x86_64",
                    agent_version="1.0.0",
                ),
                client_ip="10.1.0.9",
            )
        finally:
            await s.commit()


async def test_multi_use_token_enrols_until_used_up(env: Env):
    from app.core.exceptions import BaseAPIException

    created = await _create_token(env, max_uses=2, ttl_minutes=60 * 24 * 7, labels={"pool": "a"})
    assert created.max_uses == 2 and created.use_count == 0 and created.hub_url
    first = await _join(env, created.token, "k8s-node-1")
    second = await _join(env, created.token, "k8s-node-2")
    assert first["node_id"] != second["node_id"]
    row = (await env.sql("SELECT * FROM join_tokens WHERE id=$1", created.id))[0]
    assert row["use_count"] == 2 and row["used_at"] is not None
    assert row["used_by_node_id"] == uuid.UUID(second["node_id"])
    with pytest.raises(BaseAPIException):
        await _join(env, created.token, "k8s-node-3")
    joins = [a for a in env.audit if a.get("event_type") == "node.joined"]
    uses = sorted(a["metadata"]["join_token_use"] for a in joins if a.get("metadata"))
    assert uses[-2:] == [1, 2]


async def test_multi_use_token_can_be_revoked_part_way(env: Env):
    from app.core.exceptions import BaseAPIException
    from app.services.nodes.service import NodeService

    created = await _create_token(env, max_uses=10, ttl_minutes=120)
    await _join(env, created.token, "pool-node-a")
    async with env.session() as s:
        await NodeService(s).revoke_join_token(created.id, actor_id=None)
        await s.commit()
    with pytest.raises(BaseAPIException):
        await _join(env, created.token, "pool-node-b")
    revoked = [a for a in env.audit if a.get("event_type") == "node.join_token_revoked"]
    assert revoked and revoked[-1]["metadata"]["use_count"] == 1


async def test_hub_serves_installer_ca_and_release_build(env: Env, tmp_path, monkeypatch):
    from app.db.postgres.connection import get_db
    from app.services.agent_hub import routes as hub_routes
    from app.services.nodes import agent_downloads as dl
    from fastapi import FastAPI

    target = "aarch64-unknown-linux-gnu"
    name = f"gpudeck-agent-{settings.APP_VERSION}-{target}.tar.gz"
    payload = b"agent from the release"

    def release(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("SHA256SUMS.txt"):
            return httpx.Response(200, text=f"{hashlib.sha256(payload).hexdigest()}  {name}\n")
        if request.url.path.endswith(name):
            return httpx.Response(200, content=payload)
        return httpx.Response(404)

    monkeypatch.setattr(dl.download_settings, "AGENT_DIST_DIR", str(tmp_path / "none"))
    monkeypatch.setattr(dl.download_settings, "AGENT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(dl.download_settings, "AGENT_RELEASE_URL", "https://rel.example/d")
    monkeypatch.setattr(dl, "_transport", httpx.MockTransport(release))
    monkeypatch.setattr(dl.download_settings, "AGENT_DOWNLOAD_BASE_URL", "")
    monkeypatch.setattr(settings, "AGENT_HUB_PUBLIC_URL", "https://hub.example:8443")
    monkeypatch.setattr(settings, "AGENT_HUB_TLS_CERT_FILE", None)
    dl._sums_cache.clear()
    created = await _create_token(env, max_uses=3, ttl_minutes=60)
    assert "/agent/v1/install.sh" in created.install_command
    assert created.ca_pem_sha256 and created.ca_pem_sha256 in created.install_command

    async def db():
        async with env.session() as s:
            yield s
            await s.commit()

    app = FastAPI()
    app.include_router(hub_routes.router)
    app.dependency_overrides[get_db] = db
    hdr = {"X-GPUDeck-Join-Token": created.token}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://hub"
    ) as c:
        pem = await c.get("/agent/v1/ca.pem")
        assert pem.status_code == 200
        assert hashlib.sha256(pem.content).hexdigest() == created.ca_pem_sha256
        script = await c.get("/agent/v1/install.sh", headers=hdr)
        assert (
            script.status_code == 200
            and "BASE_URL=https://hub.example:8443/agent/v1" in script.text
        )
        assert pem.text.strip() in script.text
        assert (await c.get(f"/agent/v1/downloads/latest/{target}")).status_code == 401
        sha = await c.get(f"/agent/v1/downloads/latest/{target}/sha256", headers=hdr)
        assert sha.status_code == 200
        assert sha.text.split()[0] == hashlib.sha256(payload).hexdigest()
        got = await c.get(f"/agent/v1/downloads/latest/{target}", headers=hdr)
        assert got.status_code == 200 and got.content == payload
        assert got.headers["x-checksum-sha256"] == hashlib.sha256(payload).hexdigest()
    assert (tmp_path / "cache" / name).read_bytes() == payload


async def test_api_layer_certificate_is_provisioned_from_the_vault(env: Env):
    import ssl

    from app.services.pki import ca as cax
    from app.services.pki.hooks import provision_api_layer_client_cert

    settings.API_LAYER_CLIENT_CERT_PATH = None
    settings.API_LAYER_CLIENT_KEY_PATH = None
    await provision_api_layer_client_cert()
    cert_path = settings.API_LAYER_CLIENT_CERT_PATH
    assert cert_path and cert_path.startswith(settings.PKI_CA_DIR)
    first = open(cert_path).read()
    info = cax.cert_info(cax.cert_from_pem(first))
    assert "api-layer" in info.subject
    ctx = ssl.create_default_context()
    ctx.load_cert_chain(cert_path, settings.API_LAYER_CLIENT_KEY_PATH)
    # A second process (another replica) reuses the same vault-held pair.
    settings.API_LAYER_CLIENT_CERT_PATH = None
    settings.API_LAYER_CLIENT_KEY_PATH = None
    await provision_api_layer_client_cert()
    assert open(settings.API_LAYER_CLIENT_CERT_PATH).read() == first
    rows = await env.sql("SELECT count(*) AS n FROM node_certificates WHERE kind='api_layer'")
    assert rows[0]["n"] == 1
    expires = info.not_after
    assert expires > datetime.now(timezone.utc) + timedelta(days=30)
