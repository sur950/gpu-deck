"""
Integration (real Postgres + Redis, scratch schema): the node security policy (agent protocol
§13) and the key pins behind it.

- the join response carries a policy signed by the CA for exactly that node, with the hub and
  API-layer key pins, the tunnel endpoints and the cluster's ``config.node_security`` CIDRs;
  serials only grow; ``config_push.security`` carries the same;
- service certificates are re-issued for the same key (renewal never changes a pin), the next
  key is pre-published, a planned rotation keeps the previous key pinned for the grace period
  and a revoked certificate moves to the next key;
- a used one-time join token cannot be replayed.
"""

from __future__ import annotations

import base64
import json
import uuid
from typing import Any, Dict, List

import asyncpg
import pytest
import pytest_asyncio
from app.core.settings import settings
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

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
        self.schema = f"nodesec_{self.tag}"
        self.prefix = f"gpu-deck:nodesec:{self.tag}:"
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
        "hub_url": settings.AGENT_HUB_PUBLIC_URL,
        "tunnels": settings.AGENT_TUNNEL_PUBLIC_URLS,
        "hub_file": settings.AGENT_HUB_TLS_CERT_FILE,
    }
    connection.engine = e.engine
    settings.REDIS_KEY_PREFIX = e.prefix
    settings.PKI_CA_DIR = str(tmp_path_factory.mktemp("pki"))
    settings.API_LAYER_CLIENT_CERT_PATH = None
    settings.API_LAYER_CLIENT_KEY_PATH = None
    settings.AGENT_HUB_PUBLIC_URL = "https://cp.example.com:8443"
    settings.AGENT_TUNNEL_PUBLIC_URLS = ""
    settings.AGENT_HUB_TLS_CERT_FILE = None
    reset_ca_cache_for_tests()
    await CacheService.reset()
    await redis_client_module.close_redis()
    audit.set_sink(e.audit.append)
    await e.sql(
        "INSERT INTO clusters (id, name, config) VALUES ($1, 'secure', $2::jsonb)",
        e.cluster,
        json.dumps(
            {
                "node_security": {
                    "proxy_allowed_source_cidrs": ["203.0.113.10/32"],
                    "ssh_default_allowed_cidrs": ["198.51.100.0/24"],
                }
            }
        ),
    )
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
            settings.AGENT_HUB_PUBLIC_URL = saved["hub_url"]
            settings.AGENT_TUNNEL_PUBLIC_URLS = saved["tunnels"]
            settings.AGENT_HUB_TLS_CERT_FILE = saved["hub_file"]
            reset_ca_cache_for_tests()
            await e.engine.dispose()
            conn = await asyncpg.connect(_dsn())
            try:
                await drop_scratch_schema(conn, e.schema)
            finally:
                await conn.close()


async def _token(env: Env) -> str:
    from app.schemas.fleet_schema import JoinTokenCreate
    from app.services.nodes.service import NodeService

    async with env.session() as s:
        out = await NodeService(s).create_join_token(
            JoinTokenCreate(cluster_id=env.cluster), actor_id=None
        )
        await s.commit()
    return out.token


async def _join(env: Env, token: str, hostname: str) -> Dict[str, Any]:
    from app.services.nodes.service import JoinRequest, NodeJoinService
    from app.services.pki import ca as cax

    csr = cax.build_csr(cax.generate_private_key("ecdsa_p256"), "pending", cax.NODE_ORG)
    async with env.session() as s:
        try:
            return await NodeJoinService(s).join(
                JoinRequest(
                    join_token=token,
                    csr_pem=csr,
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


def _verify(signed: Dict[str, str], ca_pem: str) -> Dict[str, Any]:
    from app.services.pki import ca as cax

    body = base64.b64decode(signed["policy"])
    sig = base64.b64decode(signed["signature"])
    assert signed["algorithm"] == "ecdsa-p384-sha384"  # default CA key type
    cax.cert_from_pem(ca_pem).public_key().verify(sig, body, ec.ECDSA(hashes.SHA384()))
    return json.loads(body)


async def _hub_keypair(env: Env, sans: List[str]) -> Dict[str, Any]:
    from app.services.pki.service import HUB_TLS_SECRET_NAME, PkiService

    async with env.session() as s:
        data = await PkiService.from_session(s).ensure_service_keypair(
            HUB_TLS_SECRET_NAME, "agent_hub", sans
        )
        await s.commit()
    return data


async def test_join_returns_a_policy_signed_for_that_node(env: Env):
    from app.services.agent_hub.protocol import validate_payload
    from app.services.pki import ca as cax

    hub = await _hub_keypair(env, ["localhost"])
    token = await _token(env)
    joined = await _join(env, token, "gpu-a")
    doc = _verify(joined["security"], joined["ca_bundle_pem"])
    validate_payload("security_policy", doc)
    assert doc["node_id"] == joined["node_id"]
    hub_spki = cax.spki_sha256(cax.cert_from_pem(hub["cert_pem"]))
    next_spki = cax.spki_sha256_of_key(cax.key_from_pem(hub["next_key_pem"]).public_key())
    assert doc["hub"]["spki_sha256"][:2] == [hub_spki, next_spki]
    assert len(doc["api_layer"]["spki_sha256"]) == 2  # current + pre-published next
    assert doc["api_layer"]["subjects"] == ["CN=api-layer,O=gpudeck-services"]
    assert doc["tunnel"]["endpoints"] == ["cp.example.com:8444"]
    assert doc["direct"]["allowed_source_cidrs"] == ["203.0.113.10/32"]
    assert doc["ssh"]["default_allowed_cidrs"] == ["198.51.100.0/24"]
    # A replayed (used) one-time token is refused.
    from app.core.exceptions import BaseAPIException

    with pytest.raises(BaseAPIException):
        await _join(env, token, "gpu-replay")
    # config_push.security for the same node: same pins, a higher serial.
    from app.db.postgres.repositories.node_repository import NodeRepository
    from app.services.agent_hub.config_builder import ConfigBuilder

    async with env.session() as s:
        node = await NodeRepository(s).get_by_id(uuid.UUID(joined["node_id"]))
        payload = await ConfigBuilder(s).build(node, full=False, sections=["security"])
        await s.commit()
    validate_payload("config_push", payload)
    again = _verify(payload["security"], joined["ca_bundle_pem"])
    assert again["serial"] > doc["serial"]
    assert again["hub"] == doc["hub"] and again["api_layer"] == doc["api_layer"]


async def test_renewal_keeps_the_key_and_rotation_keeps_the_previous_pin(env: Env):
    from app.services.pki import ca as cax
    from app.services.pki.service import HUB_TLS_SECRET_NAME, PkiService

    first = await _hub_keypair(env, ["localhost"])
    # A new SAN forces a new certificate: same key, so no agent pin changes.
    renewed = await _hub_keypair(env, ["localhost", "hub.example.com"])
    assert renewed["cert_pem"] != first["cert_pem"]
    assert renewed["key_pem"] == first["key_pem"]
    assert "hub.example.com" in cax.cert_sans(cax.cert_from_pem(renewed["cert_pem"]))
    old_spki = cax.spki_sha256(cax.cert_from_pem(renewed["cert_pem"]))
    next_spki = cax.spki_sha256_of_key(cax.key_from_pem(renewed["next_key_pem"]).public_key())
    async with env.session() as s:
        pki = PkiService.from_session(s)
        rotated = await pki.rotate_service_key(HUB_TLS_SECRET_NAME, "agent_hub")
        pins = await pki.hub_pins()
        await s.commit()
    new_spki = cax.spki_sha256(cax.cert_from_pem(rotated["cert_pem"]))
    assert new_spki == next_spki, "the pre-published next key became current"
    assert old_spki in pins and new_spki in pins, "previous key pinned during the grace period"
    assert len(pins) == 3
    assert any(a.get("event_type") == "pki.service_key_rotated" for a in env.audit)
    # A revoked certificate is never re-used: the next (already pinned) key takes over.
    cert_row = (
        await env.sql(
            "SELECT id FROM node_certificates WHERE fingerprint_sha256=$1",
            cax.fingerprint_sha256(cax.cert_from_pem(rotated["cert_pem"])),
        )
    )[0]
    await env.sql("UPDATE node_certificates SET revoked_at=now() WHERE id=$1", cert_row["id"])
    promoted = await _hub_keypair(env, ["localhost", "hub.example.com"])
    promoted_spki = cax.spki_sha256(cax.cert_from_pem(promoted["cert_pem"]))
    assert promoted_spki == cax.spki_sha256_of_key(
        cax.key_from_pem(rotated["next_key_pem"]).public_key()
    )


async def test_api_layer_certificate_serves_the_tunnel(env: Env):
    from app.services.pki import ca as cax
    from app.services.pki.hooks import provision_api_layer_client_cert

    await provision_api_layer_client_cert()
    cert = cax.cert_from_pem(open(settings.API_LAYER_CLIENT_CERT_PATH).read())
    assert cax.has_server_auth(cert), "API layer key is also the tunnel server identity"
