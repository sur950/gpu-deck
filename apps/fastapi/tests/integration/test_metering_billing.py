"""
Integration tests against the real local Postgres and Redis.

Isolation (gpu_deck_dev and the gpu-deck Redis user are shared): a scratch
schema ``b5test_<hex>`` built from 001_init/up.sql, used through a dedicated
engine whose search_path points at it, dropped at the end; Redis keys get a
scratch ``gpu-deck:b5test:<hex>:`` prefix and are deleted at the end. Audit
events go to an in-memory sink; usage enqueues are captured in memory and
written with the real writer.

Covers: usage event writer (plan resolution, token cost, busy share, daily
partition auto-create), hourly rollup with GPU-minute allocation and the
unallocated bucket, api_keys counters, admin routes for usage / summary /
export, price plans CRUD, billing estimate / reports / CSV / regenerate,
Anthropic message batches end to end through the official SDK (create ->
jobs-process runner -> retrieve -> JSONL results) and the admin batch view.
"""

from __future__ import annotations

import csv
import importlib
import io
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List

import anthropic
import asyncpg
import httpx
import httpx2
import pytest
import pytest_asyncio
from app.core.settings import settings

pytestmark = [
    pytest.mark.integration,
    pytest.mark.db,
    pytest.mark.asyncio(loop_scope="session"),
]

UP_SQL = Path(__file__).resolve().parents[2] / "app/db/postgres/alembic/migrations/001_init/up.sql"
# Metering, billing and price plans are v2 (app/api/v1/routes_v2, not mounted in the v1 admin
# API); this module mounts them on its own app so the dormant code keeps working.
METERING_ROUTES = (
    "app.api.v1.routes_v2.metering",
    "app.api.v1.routes_v2.billing",
    "app.api.v1.routes_v2.price_plans",
    "app.api.v1.routes.batches",
)
API = settings.API_V1_PREFIX
KEY_ID = "00000000-0000-4000-8000-000000000001"


def _plain_dsn() -> str:
    return settings.get_database_url().replace("postgresql+asyncpg://", "postgresql://", 1)


def build_admin_app():
    from app.core.exceptions import (
        BaseAPIException,
        base_exception_handler,
        generic_exception_handler,
        http_exception_handler,
        validation_exception_handler,
    )
    from app.middlewares.session_verification import get_current_admin
    from fastapi import FastAPI
    from fastapi.exceptions import RequestValidationError
    from starlette.exceptions import HTTPException as StarletteHTTPException

    app = FastAPI()
    app.add_exception_handler(BaseAPIException, base_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, generic_exception_handler)
    for name in METERING_ROUTES:
        module = importlib.import_module(name)
        app.include_router(module.router, prefix=API)

    class Admin:
        id = None
        username = "admin"

    app.dependency_overrides[get_current_admin] = lambda: Admin()
    return app


class Env:
    def __init__(self) -> None:
        self.tag = uuid.uuid4().hex[:10]
        self.schema = f"b5test_{self.tag}"
        self.prefix = f"gpu-deck:b5test:{self.tag}:"
        self.audit: List[Dict[str, Any]] = []
        self.usage: List[Dict[str, Any]] = []
        self.ids: Dict[str, str] = {}

    def admin(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://adm")

    async def sql(self, query: str, *args):
        conn = await asyncpg.connect(_plain_dsn(), server_settings={"search_path": self.schema})
        try:
            return await conn.fetch(query, *args)
        finally:
            await conn.close()

    async def write_usage(self):
        """Run the metering task impl for every captured payload."""
        from app.db.postgres.connection import get_session_maker
        from app.services.metering.writer import write_usage_event

        written = 0
        async with get_session_maker()() as session:
            for payload in self.usage:
                await write_usage_event(session, payload)
                written += 1
            await session.commit()
        self.usage.clear()
        return written


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def env():
    from app.db.postgres import connection
    from app.db.redis import client as redis_client_module
    from app.db.redis.cache_service import CacheService
    from app.services.audit import audit
    from app.services.billing.pricing import invalidate_resolver
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    e = Env()
    conn = await asyncpg.connect(_plain_dsn())
    try:
        from tests.schema_helpers import create_scratch_schema

        await create_scratch_schema(conn, e.schema)  # every migration, chain order
        # Fleet rows the billing joins need (no FK chain beyond clusters -> nodes).
        e.ids = {
            "cluster": str(uuid.uuid4()),
            "node": str(uuid.uuid4()),
            "node2": str(uuid.uuid4()),
            "instance": str(uuid.uuid4()),
            "deployment": str(uuid.uuid4()),
            "model": str(uuid.uuid4()),
            "key2": str(uuid.uuid4()),
        }
        await conn.execute(
            "INSERT INTO clusters (id, name) VALUES ($1, 'metering-cluster')",
            uuid.UUID(e.ids["cluster"]),
        )
        for nid, name in ((e.ids["node"], "mac-1"), (e.ids["node2"], "l40s-1")):
            await conn.execute(
                "INSERT INTO nodes (id, cluster_id, name, os, arch) "
                "VALUES ($1, $2, $3, 'macos', 'aarch64')",
                uuid.UUID(nid),
                uuid.UUID(e.ids["cluster"]),
                name,
            )
        for kid, name in ((KEY_ID, "team-a"), (e.ids["key2"], "team-b")):
            await conn.execute(
                "INSERT INTO api_keys (id, name, key_prefix, key_hash) VALUES ($1, $2, $3, $4)",
                uuid.UUID(kid),
                name,
                kid[:12],
                uuid.uuid4().hex * 2,
            )
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
    invalidate_resolver()
    e.app = build_admin_app()
    try:
        yield e
    finally:
        audit.set_sink(None)
        invalidate_resolver()
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


# ---------------------------------------------------------------------------
# Price plans
# ---------------------------------------------------------------------------


async def test_price_plans_crud(env):
    async with env.admin() as c:
        r = await c.post(
            f"{API}/price-plans",
            json={
                "name": "Cloud L40S",
                "usd_per_gpu_minute": 0.05,
                "usd_per_1k_input_tokens": 0.5,
                "usd_per_1k_output_tokens": 1.5,
                "usd_per_1k_cached_tokens": 0.05,
            },
        )
        assert r.status_code == 201, r.text
        plan = r.json()["data"]
        assert plan["currency"] == "USD" and plan["is_default"] is False
        env.ids["plan"] = plan["id"]
        dup = await c.post(f"{API}/price-plans", json={"name": "cloud l40s"})
        assert dup.status_code == 409
        d1 = (
            await c.post(f"{API}/price-plans", json={"name": "Default A", "is_default": True})
        ).json()
        d2 = (
            await c.post(f"{API}/price-plans", json={"name": "Default B", "is_default": True})
        ).json()
        a = (await c.get(f"{API}/price-plans/{d1['data']['id']}")).json()["data"]
        assert a["is_default"] is False and d2["data"]["is_default"] is True  # single default
        r = await c.patch(f"{API}/price-plans/{plan['id']}", json={"description": "per GPU"})
        assert r.json()["data"]["description"] == "per GPU"
        listing = (await c.get(f"{API}/price-plans", params={"search": "default"})).json()
        assert listing["meta"]["total"] == 2
        bad = await c.post(f"{API}/price-plans", json={"name": "neg", "usd_per_gpu_minute": -1})
        assert bad.status_code == 422
        # attach Default A to the cluster, delete it -> detached
        await env.sql(
            "UPDATE clusters SET price_plan_id = $1 WHERE id = $2",
            uuid.UUID(d1["data"]["id"]),
            uuid.UUID(env.ids["cluster"]),
        )
        r = await c.delete(f"{API}/price-plans/{d1['data']['id']}")
        assert r.status_code == 204
        rows = await env.sql("SELECT price_plan_id FROM clusters")
        assert rows[0]["price_plan_id"] is None
        assert (await c.get(f"{API}/price-plans/{d1['data']['id']}")).status_code == 404
        await c.delete(f"{API}/price-plans/{d2['data']['id']}")
    assert {e["event_type"] for e in env.audit} >= {
        "price_plan.create",
        "price_plan.update",
        "price_plan.delete",
    }
    # node 1 uses the cloud plan; node 2 has no plan (on-prem, cost 0)
    await env.sql(
        "UPDATE nodes SET price_plan_id = $1 WHERE id = $2",
        uuid.UUID(env.ids["plan"]),
        uuid.UUID(env.ids["node"]),
    )


# ---------------------------------------------------------------------------
# Usage events -> rollups -> admin views
# ---------------------------------------------------------------------------


def _payload(env, *, key=KEY_ID, ts, inp=1000, out=500, cached=200, latency=2000, conc=0, **kw):
    base = {
        "ts": ts.isoformat(),
        "request_id": str(uuid.uuid4()),
        "api_key_id": key,
        "model_id": env.ids["model"],
        "model_name": "qwen3-test",
        "deployment_id": env.ids["deployment"],
        "instance_id": env.ids["instance"],
        "node_id": env.ids["node"],
        "cluster_id": None,
        "format": "openai",
        "endpoint": "chat/completions",
        "stream": False,
        "input_tokens": inp,
        "output_tokens": out,
        "cached_tokens": cached,
        "reasoning_tokens": 0,
        "media_units": 0,
        "latency_ms": latency,
        "ttft_ms": 100,
        "gpu_count": 1,
        "slice_compute_pct": None,
        "concurrency_at_start": conc,
        "status": "success",
        "http_status": 200,
        "error_code": None,
        "ip_address": "10.0.0.9",
        "metadata": None,
    }
    base.update(kw)
    return base


async def test_usage_writer_rollup_and_allocation(env):
    from app.db.postgres.connection import get_session_maker
    from app.services.billing.pricing import invalidate_resolver
    from app.services.metering.rollup import rollup_hour

    invalidate_resolver()
    hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(
        hours=1
    )
    env.ids["hour"] = hour.isoformat()
    m0, m1 = hour + timedelta(minutes=5), hour + timedelta(minutes=6)
    env.usage.extend(
        [
            _payload(env, ts=m0 + timedelta(seconds=1), inp=1000, out=500, cached=200, conc=1),
            _payload(
                env,
                ts=m0 + timedelta(seconds=9),
                key=env.ids["key2"],
                inp=300,
                out=200,
                cached=0,
            ),
            _payload(
                env,
                ts=m1,
                inp=0,
                out=0,
                cached=0,
                status="error",
                http_status=502,
                error_code="upstream_error",
                format="anthropic",
                endpoint="messages",
                ip_address="not-an-ip",
            ),
            _payload(
                env,
                ts=m1,
                node_id=env.ids["node2"],
                instance_id=None,
                inp=100,
                out=100,
                cached=0,
            ),
        ]
    )
    assert await env.write_usage() == 4  # partitions for that day are created on demand

    rows = await env.sql("SELECT * FROM usage_events ORDER BY ts, id")
    first = rows[0]
    # (1000-200) x 0.5/1k + 500 x 1.5/1k + 200 x 0.05/1k
    assert first["cost_usd"] == Decimal("1.16000000")
    assert first["gpu_seconds"] == Decimal("1.000")  # 2 s x 1 GPU / 2 concurrent
    assert str(first["price_plan_id"]) == env.ids["plan"]
    assert str(first["cluster_id"]) == env.ids["cluster"]
    assert rows[2]["ip_address"] is None and rows[2]["status"] == "error"
    assert rows[3]["cost_usd"] == 0 and rows[3]["price_plan_id"] is None  # on-prem node

    # Engine metrics say the instance was loaded for minutes 5..9 (5 minutes).
    for minute in range(5, 10):
        await env.sql(
            "INSERT INTO metric_rollups_engine_1m (ts, node_id, instance_id, deployment_id, "
            "sample_count) VALUES ($1, $2, $3, $4, 30)",
            hour + timedelta(minutes=minute),
            uuid.UUID(env.ids["node"]),
            uuid.UUID(env.ids["instance"]),
            uuid.UUID(env.ids["deployment"]),
        )
    async with get_session_maker()() as session:
        written = await rollup_hour(session, hour)
        await session.commit()
        again = await rollup_hour(session, hour)  # idempotent
        await session.commit()
    assert written == again
    roll = await env.sql(
        "SELECT * FROM usage_rollups_hourly WHERE bucket = $1 AND node_id = $2",
        hour,
        uuid.UUID(env.ids["node"]),
    )
    by_key = {str(r["api_key_id"]) if r["api_key_id"] else None: r for r in roll}
    # minute 5: team-a 1500 tokens, team-b 500 -> 0.75 / 0.25 GPU-min; minute 6 only an
    # error with 0 tokens (1 request) -> team-a; minutes 7-9 idle -> unallocated
    assert by_key[KEY_ID]["gpu_seconds"] == Decimal("105.000")
    assert by_key[env.ids["key2"]]["gpu_seconds"] == Decimal("15.000")
    assert by_key[None]["gpu_seconds"] == Decimal("180.000")
    assert by_key[None]["request_count"] == 0
    # unallocated cost = 3 GPU-min x 0.05
    assert by_key[None]["cost_usd"] == Decimal("0.150000")
    assert by_key[KEY_ID]["request_count"] == 2 and by_key[KEY_ID]["error_count"] == 1
    keys = await env.sql("SELECT id, total_requests, last_used_at FROM api_keys ORDER BY name")
    assert keys[0]["total_requests"] == 3 and keys[0]["last_used_at"] is not None


async def test_usage_admin_routes(env):
    hour = datetime.fromisoformat(env.ids["hour"])
    params = {"from": hour.isoformat(), "to": (hour + timedelta(hours=1)).isoformat()}
    async with env.admin() as c:
        r = await c.get(f"{API}/usage", params={**params, "page_size": 2, "sort": "-ts"})
        body = r.json()
        assert r.status_code == 200 and body["meta"]["total"] == 4 and len(body["data"]) == 2
        assert body["data"][0]["api_key_name"] in ("team-a", None)
        r = await c.get(f"{API}/usage", params={**params, "status": "error"})
        assert r.json()["meta"]["total"] == 1
        r = await c.get(f"{API}/usage/summary", params={**params, "group_by": ["api_key"]})
        s = r.json()["data"]
        assert set(s) == {"from", "to", "group_by", "rows", "totals"}
        labels = {row["labels"]["api_key"] for row in s["rows"]}
        assert {"team-a", "team-b", "unallocated"} <= labels
        assert s["totals"]["gpu_minutes"] == pytest.approx(5.0)  # billable (rollups)
        r = await c.get(f"{API}/usage/summary", params={**params, "group_by": ["format", "day"]})
        fmts = {row["group"]["format"] for row in r.json()["data"]["rows"]}
        assert fmts == {"openai", "anthropic"}  # events path
        r = await c.get(f"{API}/usage/export", params={**params, "mode": "events"})
        assert r.headers["content-type"].startswith("text/csv")
        assert len(list(csv.DictReader(io.StringIO(r.text)))) == 4
        r = await c.get(
            f"{API}/usage/export",
            params={**params, "mode": "summary", "group_by": ["model"]},
        )
        assert "cost_usd" in r.text.splitlines()[0]


async def test_billing_estimate_and_reports(env, monkeypatch):
    hour = datetime.fromisoformat(env.ids["hour"])
    monkeypatch.setattr(
        "app.workers.tasks.billing_tasks.enqueue_billing_report",
        lambda report_id: False,
    )  # broker "down" -> generated inline
    async with env.admin() as c:
        r = await c.get(
            f"{API}/billing/estimate",
            params={
                "period_start": hour.isoformat(),
                "period_end": (hour + timedelta(hours=2)).isoformat(),
            },
        )
        est = r.json()["data"]
        assert r.status_code == 200 and est["totals"]["cost_usd"] > 0
        assert {row["labels"]["node"] for row in est["by_node"]} == {"mac-1", "l40s-1"}

        payload = {
            "name": "Nov test",
            "period_start": hour.isoformat(),
            "period_end": (hour + timedelta(hours=1)).isoformat(),
            "group_by": ["api_key", "node"],
        }
        r = await c.post(f"{API}/billing/reports", json=payload)
        assert r.status_code == 202, r.text
        report = r.json()["data"]
        assert report["status"] == "ready", report
        items = {(i["group"]["api_key"], i["labels"]["node"]): i for i in report["line_items"]}
        team_a = items[(KEY_ID, "mac-1")]
        assert team_a["price_plan_name"] == "Cloud L40S"
        assert team_a["cost_breakdown"]["gpu_time_usd"] == pytest.approx(1.75 * 0.05)
        assert team_a["cost_breakdown"]["input_tokens_usd"] == pytest.approx(0.4)
        unalloc = items[("", "mac-1")]
        assert unalloc["labels"]["api_key"] == "unallocated"
        assert report["totals"]["gpu_minutes"] == pytest.approx(5.0)

        bad = await c.post(
            f"{API}/billing/reports",
            json={**payload, "period_end": payload["period_start"]},
        )
        assert bad.status_code == 422
        csv_resp = await c.get(f"{API}/billing/reports/{report['id']}/export")
        lines = list(csv.reader(io.StringIO(csv_resp.text)))
        assert lines[0][:2] == ["api_key", "node"] and lines[-1][0] == "TOTAL"

        # price change -> regenerate re-prices
        await c.patch(f"{API}/price-plans/{env.ids['plan']}", json={"usd_per_gpu_minute": 0.1})
        r = await c.post(f"{API}/billing/reports/{report['id']}/regenerate")
        regen = r.json()["data"]
        items = {(i["group"]["api_key"], i["labels"]["node"]): i for i in regen["line_items"]}
        assert items[("", "mac-1")]["cost_breakdown"]["gpu_time_usd"] == pytest.approx(0.3)
        listing = (await c.get(f"{API}/billing/reports", params={"status": "ready"})).json()
        assert listing["meta"]["total"] == 1 and listing["data"][0]["line_items"] == []
        assert (await c.delete(f"{API}/billing/reports/{report['id']}")).status_code == 204
        assert (await c.get(f"{API}/billing/reports/{report['id']}")).status_code == 404


async def test_monthly_report_once(env):
    from app.db.postgres.connection import get_session_maker
    from app.services.billing.service import ensure_monthly_report

    now = datetime.now(timezone.utc).replace(day=1, hour=0, minute=40)
    settings_day = settings.BILLING_MONTHLY_REPORT_DAY
    settings.BILLING_MONTHLY_REPORT_DAY = 1
    try:
        async with get_session_maker()() as session:
            first = await ensure_monthly_report(session, now)
        async with get_session_maker()() as session:
            second = await ensure_monthly_report(session, now)
    finally:
        settings.BILLING_MONTHLY_REPORT_DAY = settings_day
    assert first and second is None
    rows = await env.sql("SELECT name, status FROM billing_reports WHERE id = $1", uuid.UUID(first))
    assert rows[0]["status"] == "ready" and rows[0]["name"].startswith("Monthly ")


# ---------------------------------------------------------------------------
# Anthropic message batches (official SDK) + jobs-process runner + admin view
# ---------------------------------------------------------------------------


async def test_message_batches_end_to_end(env, monkeypatch):
    from app.services.serving import auth, upstream
    from app.services.serving.auth import ApiKeyContext
    from app.services.serving.batches import process_batch
    from app.services.serving.routing import get_routing_table
    from tests.unit.test_serving_golden_clients import FakeEngine, _table

    fake = FakeEngine()
    upstream.set_transport_factory(lambda: httpx.ASGITransport(app=fake.app))
    get_routing_table().load_from_dict(_table())
    auth.set_verifiers_for_tests(lambda key: ApiKeyContext(id=KEY_ID, name="team-a"), None)

    class Allowed:
        allowed = True
        reason = "test"

    async def allow(ip, scope):
        return Allowed()

    monkeypatch.setattr("app.middlewares.ip_rules.check_ip", allow)
    monkeypatch.setattr(
        "app.workers.tasks.metering_tasks.enqueue_usage_event",
        lambda p: env.usage.append(p),
    )
    queued: List[str] = []
    monkeypatch.setattr("app.workers.tasks.batches_tasks.enqueue_batch", queued.append)

    from app.api_layer_main import app as serving_app

    client = anthropic.AsyncAnthropic(
        base_url="http://serve/anthropic",
        api_key="sk-test",
        max_retries=0,
        http_client=httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=serving_app), base_url="http://serve"
        ),
    )
    try:
        batch = await client.messages.batches.create(
            requests=[
                {
                    "custom_id": "ok-1",
                    "params": {
                        "model": "qwen3-test",
                        "max_tokens": 16,
                        "messages": [{"role": "user", "content": "hi"}],
                    },
                },
                {
                    "custom_id": "ok-2",
                    "params": {
                        "model": "qwen3-test",
                        "max_tokens": 16,
                        "messages": [{"role": "user", "content": "yo"}],
                    },
                },
                {
                    "custom_id": "bad",
                    "params": {
                        "model": "nope",
                        "max_tokens": 16,
                        "messages": [{"role": "user", "content": "x"}],
                    },
                },
            ]
        )
        assert batch.id.startswith("msgbatch_") and batch.processing_status == "in_progress"
        assert batch.request_counts.processing == 3 and len(queued) == 1

        with pytest.raises(anthropic.BadRequestError):
            await client.messages.batches.create(
                requests=[{"custom_id": "a", "params": {"model": "m", "messages": []}}]
            )

        stats = await process_batch(queued[0])  # what the Celery task runs
        assert stats == {"succeeded": 2, "errored": 1, "requeued": 0}

        done = await client.messages.batches.retrieve(batch.id)
        assert done.processing_status == "ended"
        assert done.request_counts.succeeded == 2 and done.request_counts.errored == 1
        results = {
            r.custom_id: r.result async for r in await client.messages.batches.results(batch.id)
        }
        assert results["ok-1"].type == "succeeded"
        assert results["ok-1"].message.content[0].text == "Hello from the fake engine."
        assert results["bad"].type == "errored"
        assert results["bad"].error.error.type == "not_found_error"
        batch_usage = [u for u in env.usage if u["endpoint"] == "messages/batches"]
        assert len(batch_usage) == 3 and batch_usage[0]["metadata"]["batch_id"] == batch.id
        assert await env.write_usage() >= 3

        page = await client.messages.batches.list(limit=10)
        assert batch.id in [b.id for b in page.data]

        # cancel a fresh batch before it runs; admin view sees it
        second = await client.messages.batches.create(
            requests=[
                {
                    "custom_id": "c",
                    "params": {
                        "model": "qwen3-test",
                        "max_tokens": 4,
                        "messages": [{"role": "user", "content": "z"}],
                    },
                }
            ]
        )
        async with env.admin() as c:
            listing = (await c.get(f"{API}/batches", params={"status": "in_progress"})).json()
            assert [b["external_id"] for b in listing["data"]] == [second.id]
            r = await c.post(f"{API}/batches/{second.id}/cancel")
            assert r.status_code == 200 and r.json()["data"]["status"] == "ended"
            assert r.json()["data"]["request_counts"]["canceled"] == 1
            one = (await c.get(f"{API}/batches/{batch.id}")).json()["data"]
            assert one["total_items"] == 3
        deleted = await client.messages.batches.delete(second.id)
        assert deleted.id == second.id
    finally:
        await client.close()
        upstream.set_transport_factory(None)
        auth.set_verifiers_for_tests(None, None)
        auth._resolved = False
