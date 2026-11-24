"""
Typed vault credentials end to end against the real local Postgres + Redis:
create one credential of every kind through the API, schema errors, masked
listing with usage counts, system credentials hidden, update merge keeps
secrets, audited reveal / test, and the consumers: cloud clusters only take
the matching cloud kind, alert channels read their SMTP server from an SMTP
credential, catalog models keep their token credential and downloads use it,
delete is refused while anything references the credential.

Isolation: scratch schema ``vaulttest_<hex>`` built from every migration, a
scratch Redis prefix, audit events to an in-memory sink. Responses are
validated against admin.yaml.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import asyncpg
import httpx
import pytest
import pytest_asyncio
import yaml
from app.core.settings import settings
from jsonschema import Draft202012Validator
from referencing import Registry
from referencing.jsonschema import DRAFT202012
from tests.schema_helpers import create_scratch_schema, drop_scratch_schema
from tests.unit.test_vault_typed_units import VALID, ssh_public_key

pytestmark = [
    pytest.mark.integration,
    pytest.mark.db,
    pytest.mark.asyncio(loop_scope="session"),
]

ROOT = Path(__file__).resolve().parents[4]
API = settings.API_V1_PREFIX
_SPEC = yaml.safe_load((ROOT / "libs/contracts/openapi/admin.yaml").read_text())
_REGISTRY = Registry().with_resource("urn:admin", DRAFT202012.create_resource(_SPEC))


def assert_contract(name: str, data: Any) -> None:
    validator = Draft202012Validator(
        {"$ref": f"urn:admin#/components/schemas/{name}"}, registry=_REGISTRY
    )
    errors = list(validator.iter_errors(data))
    assert not errors, [f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in errors[:5]]


def _plain_dsn() -> str:
    return settings.get_database_url().replace("postgresql+asyncpg://", "postgresql://", 1)


class Env:
    def __init__(self) -> None:
        self.tag = uuid.uuid4().hex[:10]
        self.schema = f"vaulttest_{self.tag}"
        self.prefix = f"gpu-deck:vaulttest:{self.tag}:"
        self.audit: List[Dict[str, Any]] = []

    async def sql(self, query: str, *args):
        conn = await asyncpg.connect(_plain_dsn(), server_settings={"search_path": self.schema})
        try:
            return await conn.fetch(query, *args)
        finally:
            await conn.close()

    def events(self, event_type: str) -> List[Dict[str, Any]]:
        return [e for e in self.audit if e["event_type"] == event_type]


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
        "REDIS_KEY_PREFIX": settings.REDIS_KEY_PREFIX,
        "SMTP_HOST": settings.SMTP_HOST,
        "HF_TOKEN_CREDENTIAL": settings.HF_TOKEN_CREDENTIAL,
    }
    connection.engine = e.engine
    settings.REDIS_KEY_PREFIX = e.prefix
    settings.SMTP_HOST = None
    settings.HF_TOKEN_CREDENTIAL = f"hf-default-{e.tag}"  # not created: newest HF token wins
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
            connection.engine = saved.pop("engine")
            for name, value in saved.items():
                setattr(settings, name, value)
            await e.engine.dispose()
            conn = await asyncpg.connect(_plain_dsn())
            await drop_scratch_schema(conn, e.schema)
            await conn.close()


def client() -> httpx.AsyncClient:
    from app.api.v1.routes import alerts, catalog, clusters, models, vault
    from app.core.exceptions import BaseAPIException, base_exception_handler
    from app.core.rate_limiter import limiter
    from app.middlewares.session_verification import get_current_admin
    from fastapi import FastAPI

    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(BaseAPIException, base_exception_handler)
    for module in (vault, clusters, alerts, catalog, models):
        app.include_router(module.router, prefix=API)
    app.dependency_overrides[get_current_admin] = lambda: SimpleNamespace(id=None)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://adm")


async def create(c: httpx.AsyncClient, name: str, provider: str, fields: Dict[str, Any]) -> dict:
    r = await c.post(f"{API}/vault", json={"name": name, "provider": provider, "fields": fields})
    assert r.status_code == 201, r.text
    data = r.json()["data"]
    assert_contract("Credential", data)
    return data


async def test_metadata_lists_every_kind_with_schemas(env):
    async with client() as c:
        r = await c.get(f"{API}/vault/metadata")
    assert r.status_code == 200
    meta = r.json()["data"]
    assert_contract("VaultMetadata", meta)
    ids = {k["id"] for k in meta["kinds"]}
    assert ids == set(VALID)
    aws = next(k for k in meta["kinds"] if k["id"] == "aws")
    assert aws["cloud_provider"] == "aws" and aws["test"] == "cloud"
    assert {f["name"] for f in aws["fields"] if f["secret"]} >= {"secret_access_key"}
    assert meta["pickers"]["cluster.cloud_aws"] == ["aws"]
    assert meta["pickers"]["alert_channel.email"] == ["smtp"]
    assert "ca_key" in meta["types"]


async def test_typed_lifecycle_for_every_kind(env):
    secrets = {
        "aws": VALID["aws"]["secret_access_key"],
        "azure": VALID["azure"]["client_secret"],
        "digitalocean": VALID["digitalocean"]["api_token"],
        "huggingface": VALID["huggingface"]["token"],
        "slack": VALID["slack"]["webhook_url"],
        "smtp": VALID["smtp"]["password"],
        "webhook": VALID["webhook"]["hmac_secret"],
        "container_registry": VALID["container_registry"]["password"],
        "generic": VALID["generic"]["value"],
    }
    async with client() as c:
        made = {}
        for kind_id, fields in VALID.items():
            data = await create(c, f"{kind_id}-{env.tag}", kind_id, fields)
            made[kind_id] = data
            assert data["kind"] == kind_id and data["provider"] == kind_id
            assert data["status"] == "active" and data["system"] is False
            if kind_id in secrets:
                assert secrets[kind_id] not in json.dumps(data)
        assert made["gcp"]["fields"]["project_id"] == "gpu-proj"
        assert "PRIVATE KEY" not in json.dumps(made["gcp"])
        assert made["ssh_key"]["fields"]["fingerprint"].startswith("SHA256:")
        assert made["smtp"]["fields"]["port"] == 587 and made["smtp"]["secret_fields"] == [
            "password"
        ]
        assert made["kubernetes"]["fields"]["current_context"] == "prod-admin"
        assert made["huggingface"]["default_for"]  # newest HF token = default downloads token

        listing = await c.get(f"{API}/vault", params={"provider": "aws,azure", "page_size": 50})
        assert listing.status_code == 200
        assert {i["kind"] for i in listing.json()["data"]} == {"aws", "azure"}
        assert secrets["aws"] not in listing.text

        bad = await c.post(
            f"{API}/vault",
            json={
                "name": "bad-aws",
                "provider": "aws",
                "fields": {"access_key_id": "nope"},
            },
        )
        assert bad.status_code == 422 and bad.json()["error"]["code"] == "CREDENTIAL_INVALID"
        assert "Secret access key is required" in bad.json()["error"]["message"]
        unknown = await c.post(
            f"{API}/vault",
            json={"name": "x", "provider": "nope", "fields": {"value": "v"}},
        )
        assert unknown.status_code == 422
        system = await c.post(
            f"{API}/vault",
            json={"name": "x", "provider": "internal_ca", "value": "PEM"},
        )
        assert system.status_code == 422
        legacy = await c.post(f"{API}/vault", json={"name": f"legacy-{env.tag}", "value": "v1"})
        assert legacy.status_code == 201 and legacy.json()["data"]["kind"] == "generic"

        # Update: public fields replace, secrets left out are kept, the kind cannot change.
        aws_id = made["aws"]["id"]
        upd = await c.patch(
            f"{API}/vault/{aws_id}",
            json={"fields": {"region": "eu-west-1", "secret_access_key": ""}},
        )
        assert upd.status_code == 200, upd.text
        assert upd.json()["data"]["fields"]["region"] == "eu-west-1"
        revealed = await c.post(f"{API}/vault/{aws_id}/reveal")
        assert revealed.status_code == 200
        body = revealed.json()["data"]
        assert_contract("CredentialRevealed", body)
        assert body["fields"]["secret_access_key"] == secrets["aws"]
        assert json.loads(body["value"])["region"] == "eu-west-1"
        assert env.events("credential.revealed")
        assert secrets["aws"] not in json.dumps(env.audit)
        switch = await c.patch(f"{API}/vault/{aws_id}", json={"provider": "azure"})
        assert switch.status_code == 422
        rotate = await c.patch(
            f"{API}/vault/{made['huggingface']['id']}",
            json={"fields": {"token": "hf_rotated_1234"}},
        )
        assert rotate.json()["data"]["masked_value"].endswith("1234")

        test = await c.post(f"{API}/vault/{made['gcp']['id']}/test")
        assert test.status_code == 200
        result = test.json()["data"]
        assert_contract("CredentialTestResult", result)
        assert (
            result["ok"] and result["cloud_check_path"] == "/cloud/providers/gcp/check-credential"
        )
        assert [ch["name"] for ch in result["checks"]] == [
            "decrypt",
            "status",
            "schema",
        ]
        assert env.events("credential.tested")

        usage = await c.get(f"{API}/vault/{aws_id}/usage")
        assert usage.status_code == 200
        assert_contract("CredentialUsage", usage.json()["data"])
        purposes = [h["purpose"] for h in usage.json()["data"]["history"]]
        assert "reveal" in purposes


async def test_system_credentials_are_hidden_and_read_only(env):
    from app.db.postgres.connection import get_session_maker
    from app.services.vault import VaultService

    async with get_session_maker()() as s:
        ca = await VaultService(s).upsert_secret(
            f"internal-ca-{env.tag}", "PEM", type="ca_key", provider="internal_ca"
        )
        await s.commit()
    async with client() as c:
        listing = await c.get(f"{API}/vault", params={"page_size": 500})
        assert ca["id"] not in {i["id"] for i in listing.json()["data"]}
        with_system = await c.get(
            f"{API}/vault", params={"page_size": 500, "include_system": "true"}
        )
        found = [i for i in with_system.json()["data"] if i["id"] == str(ca["id"])]
        assert found and found[0]["system"] is True
        for method, path in (
            ("POST", f"/vault/{ca['id']}/reveal"),
            ("PATCH", f"/vault/{ca['id']}"),
            ("DELETE", f"/vault/{ca['id']}"),
        ):
            r = await c.request(
                method,
                f"{API}{path}",
                json={"value": "x"} if method == "PATCH" else None,
            )
            assert r.status_code == 403, (method, r.text)
            assert r.json()["error"]["code"] == "CREDENTIAL_SYSTEM"


async def test_consumers_cluster_alerts_catalog_and_in_use_guard(env, monkeypatch):
    from app.db.postgres.connection import get_session_maker
    from app.services.alerts.service import channel_send_config
    from app.services.catalog import service as catalog_service
    from app.services.catalog.hf_client import RemoteFile, RemoteRepo
    from app.services.catalog.secrets import resolve_source_token

    seen_tokens: List[Any] = []

    class FakeHF:
        def __init__(self, token):
            self.token = token

        async def repo(self, repo_id, revision=None):
            seen_tokens.append(self.token)
            return RemoteRepo(
                source="huggingface",
                repo_id=repo_id,
                revision=revision or "main",
                files=[RemoteFile("model.gguf", 1024, "gguf", sha256="a" * 64)],
            )

    monkeypatch.setattr(
        catalog_service.CatalogService,
        "_make_client",
        staticmethod(lambda source, token: FakeHF(token)),
    )

    async with client() as c:
        aws = await create(c, f"aws-c-{env.tag}", "aws", VALID["aws"])
        azure = await create(c, f"azure-c-{env.tag}", "azure", VALID["azure"])
        ssh = await create(c, f"ssh-c-{env.tag}", "ssh_key", {"public_key": ssh_public_key()})
        smtp = await create(
            c,
            f"smtp-c-{env.tag}",
            "smtp",
            {
                "host": "smtp.example.com",
                "port": 465,
                "username": "u",
                "password": "pw",
            },
        )
        hf = await create(c, f"hf-c-{env.tag}", "huggingface", {"token": "hf_model_token_1"})

        # Clusters take only the matching cloud kind; config SSH refs must be SSH keys.
        wrong = await c.post(
            f"{API}/clusters",
            json={
                "name": f"aws-{env.tag}",
                "type": "cloud_aws",
                "credential_id": azure["id"],
            },
        )
        assert wrong.status_code == 422 and "AWS access key" in wrong.text
        bad_ssh = await c.post(
            f"{API}/clusters",
            json={
                "name": f"aws-{env.tag}",
                "type": "cloud_aws",
                "credential_id": aws["id"],
                "config": {"ssh_key_credential_id": aws["id"]},
            },
        )
        assert bad_ssh.status_code == 422
        ok = await c.post(
            f"{API}/clusters",
            json={
                "name": f"aws-{env.tag}",
                "type": "cloud_aws",
                "region": "us-east-1",
                "credential_id": aws["id"],
                "config": {"ssh_key_credential_id": ssh["id"]},
            },
        )
        assert ok.status_code == 201, ok.text

        # Email channel: the SMTP server comes from the credential, the config holds recipients.
        mismatch = await c.post(
            f"{API}/alert-channels",
            json={
                "name": f"mail-{env.tag}",
                "type": "email",
                "config": {"recipients": ["ops@example.com"]},
                "credential_id": aws["id"],
            },
        )
        assert mismatch.status_code == 422
        channel = await c.post(
            f"{API}/alert-channels",
            json={
                "name": f"mail-{env.tag}",
                "type": "email",
                "config": {"recipients": ["ops@example.com"]},
                "credential_id": smtp["id"],
            },
        )
        assert channel.status_code == 201, channel.text
        channel_id = uuid.UUID(channel.json()["data"]["id"])

        # Catalog: the model keeps its token credential; listing files used it.
        model = await c.post(
            f"{API}/models",
            json={
                "name": f"gated-{env.tag}",
                "source": "huggingface",
                "repo_id": "org/gated-model",
                "type": "chat",
                "metadata": {"credential_id": hf["id"]},
            },
        )
        assert model.status_code == 201, model.text
        assert seen_tokens == ["hf_model_token_1"]
        wrong_model = await c.post(
            f"{API}/models",
            json={
                "name": f"gated2-{env.tag}",
                "source": "huggingface",
                "repo_id": "org/x",
                "type": "chat",
                "metadata": {"credential_id": smtp["id"]},
            },
        )
        assert wrong_model.status_code == 422

        listing = await c.get(f"{API}/vault", params={"page_size": 500})
        by_id = {i["id"]: i for i in listing.json()["data"]}
        assert by_id[aws["id"]]["used_by"]["clusters"] == 1
        assert by_id[ssh["id"]]["used_by"]["clusters"] == 1
        assert by_id[smtp["id"]]["used_by"]["alert_channels"] == 1
        assert by_id[hf["id"]]["used_by"]["models"] == 1
        usage = (await c.get(f"{API}/vault/{ssh['id']}/usage")).json()["data"]
        assert usage["references"][0]["field"] == "config.ssh_key_credential_id"

        busy = await c.delete(f"{API}/vault/{aws['id']}")
        assert busy.status_code == 409 and busy.json()["error"]["code"] == "CREDENTIAL_IN_USE"
        assert "cluster" in busy.json()["error"]["message"]
        free = await c.delete(f"{API}/vault/{azure['id']}")
        assert free.status_code == 204

    async with get_session_maker()() as s:
        from app.db.postgres.repositories.alert_repository import AlertChannelRepository

        row = await AlertChannelRepository(s).get_active(channel_id)
        config = await channel_send_config(s, row)
        assert config["host"] == "smtp.example.com" and config["port"] == 465
        assert config["use_tls"] is True and config["recipients"] == ["ops@example.com"]
        token = await resolve_source_token(s, "huggingface", uuid.UUID(hf["id"]))
        assert token == "hf_model_token_1"
        await s.commit()
