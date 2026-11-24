"""
Integration tests against real Postgres (and Redis for the vault cache).

Everything runs in a throw-away schema created inside the dev database
(``b4t_<random>``, every migration applied in chain order, dropped at the end), so
``gpu_deck_dev`` itself is never touched. Covers: leader election with two
contenders + fencing, the rule engine (dry run, cooldown, fire, swap capacity,
no_match throttling, leader-fenced tick), the swap executor on the DB store,
layout preview/apply, and alert emit -> delivery -> retry to a local HTTP server.
"""

import asyncio
import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import pytest_asyncio
from app.core.settings import settings
from app.services.audit import audit
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.integration

SCHEMA = f"b4t_{uuid.uuid4().hex[:10]}"
MIGRATIONS = Path(__file__).resolve().parents[2] / "app/db/postgres/alembic/migrations"
LLAMA_ENGINE = "00000000-0000-4000-8000-000000000001"
GIB = 1024**3


@pytest_asyncio.fixture(scope="module")
async def factory():
    engine = create_async_engine(
        settings.get_database_url(),
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
    )
    async with engine.connect() as conn:
        raw = (await conn.get_raw_connection()).driver_connection
        await raw.execute(f"CREATE SCHEMA {SCHEMA}")
        await raw.execute(f"SET search_path TO {SCHEMA}, public")
        from tests.schema_helpers import apply_migrations

        # Every migration in chain order (later ones add columns the services read).
        await apply_migrations(raw)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        async with engine.connect() as conn:
            raw = (await conn.get_raw_connection()).driver_connection
            await raw.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        await engine.dispose()


@pytest.fixture(autouse=True)
def captured_audit():
    with audit.capture() as events:
        yield events


class World:
    pass


@pytest_asyncio.fixture
async def world(factory):
    w = World()
    w.factory = factory
    now = datetime.now(timezone.utc)
    ids = {
        k: uuid.uuid4()
        for k in (
            "cluster",
            "node",
            "d1",
            "d2",
            "m1",
            "v1",
            "m2",
            "v2",
            "ev",
            "dep_idle",
            "dep_b",
            "inst",
        )
    }
    w.__dict__.update(ids)
    async with factory() as s:
        await s.execute(
            text(
                "TRUNCATE clusters, models, engine_versions, rules, rule_runs, layouts,"
                " swap_operations,"
                " alert_channels, alert_events, ha_leases, credentials CASCADE"
            )
        )
        q = [
            ("INSERT INTO clusters (id, name) VALUES (:cluster, 'c1')", {}),
            (
                "INSERT INTO nodes (id, cluster_id, name, status, os, arch) VALUES "
                "(:node, :cluster, 'n1', 'online', 'macos', 'aarch64')",
                {},
            ),
            (
                "INSERT INTO devices (id, node_id, vendor, index, name, vram_total_bytes, health)"
                " VALUES"
                " (:d1, :node, 'apple', 0, 'gpu0', :vram, 'healthy'),"
                " (:d2, :node, 'apple', 1, 'gpu1', :vram, 'healthy')",
                {"vram": 24 * GIB},
            ),
            (
                "INSERT INTO models (id, name, source, repo_id, type, priority) VALUES "
                "(:m1, 'idle-model', 'local_path', '/m1', 'chat', 'normal'),"
                "(:m2, 'b-model', 'local_path', '/m2', 'chat', 'normal')",
                {},
            ),
            (
                "INSERT INTO model_versions"
                " (id, model_id, version, format, status, total_size_bytes)"
                " VALUES (:v1, :m1, '1', 'gguf', 'ready', :size),"
                " (:v2, :m2, '1', 'gguf', 'ready', :size)",
                {"size": 4 * GIB},
            ),
            (
                "INSERT INTO engine_versions (id, engine_id, version) VALUES (:ev, :engine, 'v1')",
                {"engine": uuid.UUID(LLAMA_ENGINE)},
            ),
            (
                "INSERT INTO deployments (id, name, model_id, model_version_id, engine_version_id,"
                " cluster_id, replicas, min_replicas, max_replicas, status) VALUES"
                " (:dep_idle, 'idle-dep', :m1, :v1, :ev, :cluster, 1, 0, 2, 'running'),"
                " (:dep_b, 'b-dep', :m2, :v2, :ev, :cluster, 0, 0, 2, 'stopped')",
                {},
            ),
            (
                "INSERT INTO instances"
                " (id, deployment_id, node_id, device_ids, port, state, health,"
                " ready_at, served_model_name) VALUES (:inst, :dep_idle, :node, ARRAY[:d1]::uuid[],"
                " 10240, 'ready', 'healthy', :ready_at, 'idle-model')",
                {"ready_at": now - timedelta(hours=2)},
            ),
        ]
        for sql, extra in q:
            await s.execute(text(sql), {**ids, **extra})
        await s.commit()
    yield w


async def _rule(factory, **overrides):
    from app.db.postgres.repositories.rule_repository import RuleRepository
    from app.services.rules.validation import validate_params

    data = {
        "name": f"rule-{uuid.uuid4().hex[:6]}",
        "type": "idle_unload",
        "params": validate_params("idle_unload", {"idle_minutes": 30}),
        "target": {},
        "enabled": True,
        "dry_run": False,
        "cooldown_seconds": 300,
        "evaluation_interval_seconds": 30,
        "priority": 100,
    }
    data.update(overrides)
    async with factory() as s:
        row = await RuleRepository(s).create(**data)
        await s.commit()
    return row.id


async def _run(factory, rule_id, cooldowns, trigger="interval", force_dry_run=False):
    from app.db.postgres.repositories.rule_repository import RuleRepository
    from app.services.rules.engine import RuleEngine
    from app.services.swap.fakes import RecordingAlerts

    alerts = RecordingAlerts()
    async with factory() as s:
        rule = await RuleRepository(s).get_active(rule_id)
        engine = RuleEngine(s, cooldowns=cooldowns, alerts=alerts)
        run = await engine.run(rule, trigger=trigger, force_dry_run=force_dry_run)
        await s.commit()
    await engine.flush_alerts()
    return run, alerts


async def _count(factory, sql, **params):
    async with factory() as s:
        return (await s.execute(text(sql), params)).scalar_one()


# ---------------------------------------------------------------- HA


async def test_leader_election_two_contenders_and_fencing(world):
    from app.services.ha.service import LeaderState, LeaseManager
    from app.services.swap.adapters import DbSwapStore
    from app.services.swap.planner import SwapPlanner
    from app.services.swap.ports import FenceLost

    a = LeaseManager("replica-a", ttl_seconds=2, session_factory=world.factory, state=LeaderState())
    b = LeaseManager("replica-b", ttl_seconds=2, session_factory=world.factory, state=LeaderState())
    results = await asyncio.gather(
        a.acquire_or_renew("jobs-leader"), b.acquire_or_renew("jobs-leader")
    )
    assert sorted(r is not None for r in results) == [False, True]  # exactly one wins
    leader, follower = (a, b) if results[0] is not None else (b, a)
    token = leader.fence("jobs-leader").version
    assert await leader.acquire_or_renew("jobs-leader") == token  # renew keeps the token
    assert await follower.acquire_or_renew("jobs-leader") is None
    assert follower.fence("jobs-leader") is None and leader.is_leader("jobs-leader")

    async with world.factory() as s:
        swap = await SwapPlanner(s).create(
            {"kind": "unload", "instance_id": world.inst}, trigger="manual"
        )
        await s.commit()
    old_fence = leader.fence("jobs-leader")
    old_store = DbSwapStore(world.factory, fence=old_fence)
    loaded = await old_store.get_swap(swap.id)
    await old_store.save_swap(loaded)  # still valid

    await asyncio.sleep(2.2)  # leader stops renewing (paused / partitioned)
    new_token = await follower.acquire_or_renew("jobs-leader")
    assert new_token == token + 1
    assert not await old_fence.check()
    assert await follower.fence("jobs-leader").check()
    with pytest.raises(FenceLost):
        await old_store.save_swap(loaded)  # the stale leader's write is rejected
    await DbSwapStore(world.factory, fence=follower.fence("jobs-leader")).save_swap(loaded)

    assert await follower.release("jobs-leader")
    assert await leader.acquire_or_renew("jobs-leader") == new_token + 1  # immediate takeover


# ---------------------------------------------------------------- rule engine


async def test_dry_run_records_actions_without_swaps_and_starts_cooldown(world, captured_audit):
    from app.services.rules.engine import MemoryCooldowns

    rule_id = await _rule(world.factory, dry_run=True)
    cooldowns = MemoryCooldowns()
    run, alerts = await _run(world.factory, rule_id, cooldowns)
    assert run.outcome == "dry_run" and run.dry_run and not run.fired
    assert run.actions[0]["action"] == "unload" and run.actions[0]["status"] == "dry_run"
    assert run.actions[0]["target_id"] == str(world.inst)
    assert run.decision["targets"][0]["observed"]["idle_minutes"] >= 119
    assert await _count(world.factory, "SELECT count(*) FROM swap_operations") == 0
    assert alerts.events[0]["event_type"] == "rule.dry_run"
    run2, _ = await _run(world.factory, rule_id, cooldowns)
    assert run2.outcome == "cooldown"
    assert not [e for e in captured_audit if e["event_type"] == "rule.fired"]


async def test_dry_run_api_works_for_disabled_rules(world):
    from app.services.rules.engine import MemoryCooldowns

    rule_id = await _rule(world.factory, enabled=False)
    run, _ = await _run(
        world.factory, rule_id, MemoryCooldowns(), trigger="api", force_dry_run=True
    )
    assert run.outcome == "dry_run"
    assert await _count(world.factory, "SELECT count(*) FROM swap_operations") == 0


async def test_fire_creates_swap_audits_and_blocks_while_swap_runs(world, captured_audit):
    from app.services.rules.engine import MemoryCooldowns

    rule_id = await _rule(world.factory, cooldown_seconds=0)
    run, alerts = await _run(world.factory, rule_id, MemoryCooldowns())
    assert run.outcome == "fired" and run.fired
    swap_id = uuid.UUID(run.actions[0]["swap_operation_id"])
    async with world.factory() as s:
        row = (
            await s.execute(
                text(
                    "SELECT kind, state, trigger, rule_run_id, from_instance_id, plan "
                    "FROM swap_operations WHERE id = :id"
                ),
                {"id": swap_id},
            )
        ).one()
        fire_count = (
            await s.execute(text("SELECT fire_count FROM rules WHERE id = :id"), {"id": rule_id})
        ).scalar_one()
    assert (row.kind, row.state, row.trigger) == ("unload", "pending", "rule")
    assert row.rule_run_id == run.id and row.from_instance_id == world.inst
    assert row.plan["lock_device_ids"] == [str(world.d1)]
    assert fire_count == 1
    fired = [e for e in captured_audit if e["event_type"] == "rule.fired"]
    assert fired and fired[0]["actor_type"] == "rule" and fired[0]["actor_id"] == str(rule_id)
    assert alerts.events[0]["event_type"] == "rule.fired"
    run2, _ = await _run(world.factory, rule_id, MemoryCooldowns())
    assert run2.outcome == "skipped"
    assert run2.decision["targets"][0]["reason"] == "swap_in_progress"


async def test_swap_capacity_guard(world, monkeypatch):
    from app.services.rules.engine import MemoryCooldowns

    monkeypatch.setattr(settings, "RULES_MAX_CONCURRENT_SWAPS", 0)
    run, _ = await _run(world.factory, await _rule(world.factory), MemoryCooldowns())
    assert run.outcome == "skipped"
    assert run.decision["targets"][0]["reason"] == "swap_capacity"


async def test_always_on_is_protected(world):
    from app.services.rules.engine import MemoryCooldowns

    async with world.factory() as s:
        await s.execute(
            text("UPDATE deployments SET priority_override = 'always_on' WHERE id = :d"),
            {"d": world.dep_idle},
        )
        await s.commit()
    run, _ = await _run(world.factory, await _rule(world.factory), MemoryCooldowns())
    assert run.outcome == "skipped" and run.decision["targets"][0]["reason"] == "always_on"
    assert await _count(world.factory, "SELECT count(*) FROM swap_operations") == 0


async def test_no_match_is_logged_once_per_interval(world):
    from app.services.rules.engine import MemoryCooldowns
    from app.services.rules.validation import validate_params

    rule_id = await _rule(
        world.factory, params=validate_params("idle_unload", {"idle_minutes": 600})
    )
    first, _ = await _run(world.factory, rule_id, MemoryCooldowns())
    second, _ = await _run(world.factory, rule_id, MemoryCooldowns())
    assert first.outcome == "no_match" and second is None
    assert (
        await _count(
            world.factory,
            "SELECT count(*) FROM rule_runs WHERE rule_id = :r",
            r=rule_id,
        )
        == 1
    )
    assert (
        await _count(
            world.factory,
            "SELECT count(*) FROM rules WHERE id = :r AND last_evaluated_at IS NOT NULL",
            r=rule_id,
        )
        == 1
    )


async def test_evaluator_tick_runs_due_rules_and_respects_the_fence(world):
    from app.services.rules.engine import MemoryCooldowns, RuleEngine
    from app.services.rules.service import evaluate_due_rules

    await _rule(world.factory, dry_run=True)
    cooldowns = MemoryCooldowns()

    def engine_factory(session, now=None):
        return RuleEngine(session, cooldowns=cooldowns, alerts=_NullAlerts(), now=now)

    class Fence:
        def __init__(self, ok):
            self.ok = ok

        async def check(self):
            return self.ok

    assert (
        await evaluate_due_rules(
            fence=Fence(False),
            session_factory=world.factory,
            engine_factory=engine_factory,
        )
        == {}
    )
    counts = await evaluate_due_rules(
        fence=Fence(True), session_factory=world.factory, engine_factory=engine_factory
    )
    assert counts == {"dry_run": 1}
    # not due again before evaluation_interval_seconds
    assert (
        await evaluate_due_rules(session_factory=world.factory, engine_factory=engine_factory) == {}
    )


class _NullAlerts:
    async def emit(self, *args, **kwargs):
        return None


# ---------------------------------------------------------------- swap executor on the DB


async def test_unload_swap_on_db_store_updates_instance_and_deployment(world):
    from app.services.swap.adapters import DbSwapStore
    from app.services.swap.executor import SwapExecutor
    from app.services.swap.fakes import (
        FakeAgent,
        FakeHealth,
        FakeLoadArgs,
        FakeRouting,
        MemoryLocks,
    )
    from app.services.swap.planner import SwapPlanner

    async with world.factory() as s:
        swap = await SwapPlanner(s).create(
            {"kind": "unload", "instance_id": world.inst}, trigger="manual"
        )
        await s.commit()
    agent, routing = FakeAgent(), FakeRouting()
    executor = SwapExecutor(
        DbSwapStore(world.factory),
        agent,
        routing,
        FakeLoadArgs(),
        FakeHealth(),
        MemoryLocks(),
    )
    done = await executor.drive(swap.id)
    assert done.state == "completed" and agent.commands() == [
        "drain_instance",
        "unload_model",
    ]
    async with world.factory() as s:
        inst = (
            await s.execute(
                text("SELECT state, traffic_weight FROM instances WHERE id = :i"),
                {"i": world.inst},
            )
        ).one()
        dep = (
            await s.execute(
                text("SELECT replicas, status FROM deployments WHERE id = :d"),
                {"d": world.dep_idle},
            )
        ).one()
        steps = (
            await s.execute(text("SELECT steps FROM swap_operations WHERE id = :i"), {"i": swap.id})
        ).scalar_one()
    assert (inst.state, inst.traffic_weight) == ("stopped", 0)
    assert (dep.replicas, dep.status) == (0, "stopped")
    assert [st["state"] for st in steps] == ["completed"] * 4
    assert all(st["command_id"] for st in steps if st["name"] in ("drain", "unload"))


# ---------------------------------------------------------------- layouts


async def test_layout_preview_and_apply(world):
    from app.schemas.layouts_schema import LayoutCreate
    from app.services.layouts.service import LayoutService
    from app.services.swap.fakes import RecordingPrefetch

    async with world.factory() as s:
        svc = LayoutService(s, prefetcher=RecordingPrefetch())
        layout = await svc.create_layout(
            LayoutCreate.model_validate(
                {
                    "name": "Night",
                    "items": [
                        {"deployment_id": str(world.dep_idle), "replicas": 0},
                        {
                            "deployment_id": str(world.dep_b),
                            "node_id": str(world.node),
                            "device_ids": [str(world.d1)],
                            "replicas": 1,
                            "traffic_weight": 80,
                        },
                    ],
                }
            ),
            created_by=None,
        )
        await s.commit()
        diff = await LayoutService(s).diff(layout.id)
        actions = {str(e.deployment_id): e.action for e in diff.entries}
        assert actions == {str(world.dep_idle): "unload", str(world.dep_b): "load"}
        assert diff.feasible and diff.entries[1].needs_prefetch_bytes == 4 * GIB
        assert await _count(world.factory, "SELECT count(*) FROM swap_operations") == 0

        prefetch = RecordingPrefetch()
        result = await LayoutService(s, prefetcher=prefetch).apply(
            layout.id, drain_timeout_seconds=30
        )
        await s.commit()
    assert len(result.swap_operation_ids) == 2 and len(prefetch.calls) == 1
    async with world.factory() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT id, kind, plan, layout_id, trigger, drain_timeout_seconds"
                    " FROM swap_operations"
                    " ORDER BY created_at"
                )
            )
        ).all()
        active = (
            await s.execute(text("SELECT is_active FROM layouts WHERE id = :l"), {"l": layout.id})
        ).scalar_one()
    by_kind = {r.kind: r for r in rows}
    assert set(by_kind) == {"unload", "load"}
    assert by_kind["load"].plan["depends_on"] == [str(by_kind["unload"].id)]  # same device
    assert by_kind["load"].plan["target_weight"] == 80
    assert all(r.layout_id == layout.id and r.trigger == "layout" for r in rows)
    assert all(r.drain_timeout_seconds == 30 for r in rows)
    assert active is True


async def test_layout_with_always_on_unlisted_unload_is_protected(world):
    from app.schemas.layouts_schema import LayoutCreate
    from app.services.layouts.service import LayoutService

    async with world.factory() as s:
        await s.execute(
            text("UPDATE deployments SET priority_override = 'always_on' WHERE id = :d"),
            {"d": world.dep_idle},
        )
        svc = LayoutService(s)
        layout = await svc.create_layout(
            LayoutCreate.model_validate(
                {
                    "name": "Only B",
                    "unlisted_policy": "unload",
                    "items": [{"deployment_id": str(world.dep_b), "replicas": 1}],
                }
            ),
            created_by=None,
        )
        diff = await LayoutService(s).diff(layout.id)
        await s.rollback()
    entry = [e for e in diff.entries if e.deployment_id == world.dep_idle][0]
    assert entry.protected and entry.action == "noop"


# ---------------------------------------------------------------- alerts


class Hook(BaseHTTPRequestHandler):
    received = []
    status = 200

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers["Content-Length"]))
        Hook.received.append(
            {"headers": {k.lower(): v for k, v in self.headers.items()}, "body": body}
        )
        self.send_response(Hook.status)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def hook_url():
    Hook.received, Hook.status = [], 200
    server = HTTPServer(("127.0.0.1", 0), Hook)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/alerts"
    server.shutdown()
    server.server_close()


async def test_alert_emit_delivers_signed_webhook_and_retries(world, hook_url, monkeypatch):
    from app.schemas.alerts_schema import AlertChannelCreate
    from app.services.alerts.senders import verify_signature
    from app.services.alerts.service import (
        AlertService,
        deliver_event,
        emit,
        retry_pending,
    )

    async with world.factory() as s:
        channel = await AlertService(s).create_channel(
            AlertChannelCreate(
                name="ops-hook",
                type="webhook",
                config={"url": hook_url},
                secret="hook-secret",
                min_severity="warning",
                event_filter=["swap.*"],
            ),
            created_by=None,
        )
        await s.commit()
    assert channel.credential_id is not None and "secret" not in channel.config

    event_id = await emit(
        "swap.failed",
        "critical",
        "Swap failed",
        "drain timed out",
        resource_type="swap_operation",
        resource_id=uuid.uuid4(),
        dedupe_key="swap.failed:x",
        session_factory=world.factory,
        enqueue=False,
    )
    ignored = await emit(
        "rule.fired", "info", "fired", session_factory=world.factory, enqueue=False
    )
    assert event_id and ignored
    counts = await deliver_event(event_id, session_factory=world.factory)
    assert counts == {"sent": 1}
    req = Hook.received[0]
    assert verify_signature(
        "hook-secret",
        req["headers"]["x-gpudeck-timestamp"],
        req["body"],
        req["headers"]["x-gpudeck-signature"],
    )
    assert json.loads(req["body"])["title"] == "Swap failed"

    # same dedupe key inside the window: occurrences bump, no new delivery
    again = await emit(
        "swap.failed",
        "critical",
        "Swap failed",
        dedupe_key="swap.failed:x",
        session_factory=world.factory,
        enqueue=False,
    )
    assert again == event_id
    assert (
        await _count(
            world.factory,
            "SELECT occurrences FROM alert_events WHERE id = :e",
            e=event_id,
        )
        == 2
    )
    assert await _count(world.factory, "SELECT count(*) FROM alert_deliveries") == 1

    # failing receiver: attempts are retried with backoff until ALERT_MAX_ATTEMPTS
    Hook.status = 503
    monkeypatch.setattr(settings, "ALERT_RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(settings, "ALERT_MAX_ATTEMPTS", 2)
    second = await emit(
        "swap.rolled_back",
        "warning",
        "Rolled back",
        session_factory=world.factory,
        enqueue=False,
    )
    assert await deliver_event(second, session_factory=world.factory) == {"pending": 1}
    assert await retry_pending(session_factory=world.factory) == {"failed": 1}
    async with world.factory() as s:
        row = (
            await s.execute(
                text(
                    "SELECT status, attempts, response_code FROM alert_deliveries"
                    " WHERE alert_event_id = :e"
                ),
                {"e": second},
            )
        ).one()
    assert (row.status, row.attempts, row.response_code) == ("failed", 2, 503)


async def test_alert_channel_test_send_and_validation(world, hook_url):
    from app.core.exceptions import ValidationError
    from app.schemas.alerts_schema import AlertChannelCreate
    from app.services.alerts.service import AlertService

    async with world.factory() as s:
        svc = AlertService(s)
        channel = await svc.create_channel(
            AlertChannelCreate(name="t", type="webhook", config={"url": hook_url}),
            created_by=None,
        )
        result = await svc.test_channel(channel.id)
        assert result.ok and result.response_code == 200
        assert json.loads(Hook.received[0]["body"])["test"] is True
        with pytest.raises(ValidationError):
            await svc.create_channel(
                AlertChannelCreate(name="bad", type="webhook", config={"url": "ftp://x"}),
                created_by=None,
            )
        with pytest.raises(ValidationError):
            await svc.create_channel(
                AlertChannelCreate(name="bad2", type="email", config={"recipients": []}),
                created_by=None,
            )
        await s.rollback()


# ---------------------------------------------------------------- admin API services


async def test_swap_service_create_cancel_retry(world):
    from app.core.exceptions import ConflictError
    from app.schemas.swaps_schema import SwapCreate
    from app.services.swap.service import SwapService

    async with world.factory() as s:
        svc = SwapService(s)
        load = await svc.create_swap(SwapCreate(kind="load", deployment_id=world.dep_b), None)
        assert load.kind.value == "load" and load.deployment_name == "b-dep"
        assert load.plan["load"]["node_id"] == str(world.node)
        unload = await svc.create_swap(
            SwapCreate(kind="unload", deployment_id=world.dep_idle), None
        )
        assert unload.from_instance_id == world.inst
        with pytest.raises(ConflictError):  # one active swap per instance
            await svc.create_swap(SwapCreate(kind="restart", deployment_id=world.dep_idle), None)
        dry = await svc.create_swap(
            SwapCreate(kind="load", deployment_id=world.dep_b, dry_run=True), None
        )
        assert dry.state.value == "completed" and dry.dry_run
        cancelled = await svc.cancel(unload.id)
        assert cancelled.state.value == "cancelled"
        retried = await svc.retry(unload.id, None)
        assert retried.state.value == "pending" and retried.plan["retry_of"] == str(unload.id)
        assert [st.state.value for st in retried.steps] == ["pending"] * 4
        items, total = await svc.list_swaps(page=1, page_size=10, active_only=True)
        assert total == 2 and {i.id for i in items} == {load.id, retried.id}
        await s.rollback()


async def test_rule_service_crud_and_time_schedule_layout_check(world):
    from app.core.exceptions import DuplicateResourceError, ValidationError
    from app.schemas.layouts_schema import LayoutCreate
    from app.schemas.rules_schema import RuleCreate, RuleUpdate
    from app.services.layouts.service import LayoutService
    from app.services.rules.service import RuleService

    async with world.factory() as s:
        svc = RuleService(s)
        with pytest.raises(ValidationError):
            await svc.create_rule(
                RuleCreate(
                    name="night",
                    type="time_schedule",
                    params={"layout_id": str(uuid.uuid4())},
                ),
                None,
            )
        layout = await LayoutService(s).create_layout(
            LayoutCreate.model_validate(
                {"name": "N", "items": [{"deployment_id": str(world.dep_b)}]}
            ),
            None,
        )
        rule = await svc.create_rule(
            RuleCreate(
                name="night",
                type="time_schedule",
                params={
                    "layout_id": str(layout.id),
                    "at": "00:00",
                    "timezone": "Asia/Kolkata",
                },
            ),
            None,
        )
        assert rule.params["prefetch_minutes_before"] == 15
        assert (await LayoutService(s).get_layout(layout.id)).used_by_rule_ids == [rule.id]
        with pytest.raises(DuplicateResourceError):
            await svc.create_rule(
                RuleCreate(name="NIGHT", type="idle_unload", params={"idle_minutes": 5}),
                None,
            )
        updated = await svc.update_rule(rule.id, RuleUpdate(dry_run=True, priority=5))
        assert updated.dry_run and updated.priority == 5
        assert not (await svc.set_enabled(rule.id, False)).enabled
        dry = await svc.evaluate_now(rule.id, dry_run=True)
        assert dry.outcome.value == "dry_run" and dry.actions[0].action == "apply_layout"
        runs, total = await svc.list_rule_runs(rule.id, page=1, page_size=10)
        assert total == 1 and runs[0].rule_name == "night"
        listed = (await svc.get_rule(rule.id)).last_run
        assert listed is not None and listed.outcome.value == "dry_run"
        await s.rollback()


async def test_layout_capture_and_alert_event_lifecycle(world, hook_url):
    from app.schemas.alerts_schema import AlertChannelCreate, AlertChannelUpdate
    from app.schemas.layouts_schema import LayoutCaptureRequest
    from app.services.alerts.service import AlertService, emit
    from app.services.layouts.service import LayoutService

    async with world.factory() as s:
        captured = await LayoutService(s).capture(LayoutCaptureRequest(name="Day"), None)
        assert len(captured.items) == 1
        item = captured.items[0]
        assert (item.deployment_id, item.node_id, item.replicas) == (
            world.dep_idle,
            world.node,
            1,
        )
        assert item.device_ids == [world.d1]
        diff = await LayoutService(s).diff(captured.id)
        assert {e.action.value for e in diff.entries} == {"noop"}

        alerts = AlertService(s)
        channel = await alerts.create_channel(
            AlertChannelCreate(name="hook", type="webhook", config={"url": hook_url}),
            None,
        )
        changed = await alerts.update_channel(
            channel.id, AlertChannelUpdate(min_severity="critical", secret="new-secret")
        )
        assert changed.min_severity.value == "critical" and changed.credential_id is not None
        await s.commit()
    event_id = await emit(
        "node.offline",
        "critical",
        "Node offline",
        session_factory=world.factory,
        enqueue=False,
    )
    async with world.factory() as s:
        alerts = AlertService(s)
        acked = await alerts.set_status(event_id, "acknowledged")
        assert acked.status.value == "acknowledged" and acked.acknowledged_at is not None
        resent = await alerts.resend(event_id, None)
        assert resent.deliveries[0].status.value == "sent"
        assert resent.deliveries[0].channel_name == "hook"
        resolved = await alerts.set_status(event_id, "resolved")
        assert resolved.resolved_at is not None
        events, total = await alerts.list_events(page=1, page_size=10, event_type_prefix="node.")
        assert total == 1 and events[0].id == event_id
        await s.commit()
