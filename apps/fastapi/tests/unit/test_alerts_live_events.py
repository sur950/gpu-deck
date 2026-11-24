"""
Unit tests: live UI events (``/live/events``) for alerts, swaps and rule
runs — payload shapes the web desktop bridge reads, and publishing failures
never reaching the caller.
"""

import asyncio
import uuid
from datetime import datetime, timezone
from enum import Enum
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.services.alerts import live as live_mod
from app.services.alerts import service as alert_service
from app.services.alerts.senders import SendResult
from app.services.rules.engine import RuleEngine
from app.services.swap.ports import CommandResult
from tests.unit.test_swap_executor import Harness, unload_spec


class Recorder:
    def __init__(self, fail: bool = False):
        self.calls = []
        self.fail = fail

    async def __call__(self, event, data):
        self.calls.append((event, data))
        if self.fail:
            raise RuntimeError("redis down")


# ---------------------------------------------------------------------------
# publish_live wrapper
# ---------------------------------------------------------------------------


class _Sev(str, Enum):
    warning = "warning"


async def test_publish_live_serialises_payload(monkeypatch):
    seen = []

    async def fake_publish(event, data, ts=None):
        seen.append((event, data))

    monkeypatch.setattr("app.services.metrics.live.publish_live_event", fake_publish)
    rid = uuid.uuid4()
    now = datetime(2026, 11, 18, tzinfo=timezone.utc)
    await live_mod.publish_live("alert", {"id": rid, "at": now, "severity": _Sev.warning})
    assert seen == [("alert", {"id": str(rid), "at": now.isoformat(), "severity": "warning"})]


async def test_publish_live_swallows_errors_and_timeouts(monkeypatch):
    async def boom(event, data, ts=None):
        raise RuntimeError("redis down")

    monkeypatch.setattr("app.services.metrics.live.publish_live_event", boom)
    await live_mod.publish_live("alert", {"id": "x"})  # no raise

    async def slow(event, data, ts=None):
        await asyncio.sleep(5)

    monkeypatch.setattr("app.services.metrics.live.publish_live_event", slow)
    monkeypatch.setattr(live_mod, "PUBLISH_TIMEOUT_SECONDS", 0.01)
    started = asyncio.get_running_loop().time()
    await live_mod.publish_live("alert", {"id": "x"})
    assert asyncio.get_running_loop().time() - started < 1


# ---------------------------------------------------------------------------
# alerts
# ---------------------------------------------------------------------------


class _Session:
    def __init__(self):
        self.commit = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


async def test_emit_publishes_alert_event(monkeypatch):
    event_id = uuid.uuid4()
    payload = {"id": str(event_id), "title": "Node offline", "status": "open"}
    monkeypatch.setattr(
        alert_service, "_emit_impl", AsyncMock(return_value=(event_id, False, payload))
    )
    rec = Recorder()
    monkeypatch.setattr(alert_service, "publish_live", rec)
    out = await alert_service.emit(
        "node.offline", "warning", "Node offline", session_factory=_Session
    )
    assert out == event_id
    assert rec.calls == [("alert", payload)]


async def test_emit_survives_publish_failure(monkeypatch):
    event_id = uuid.uuid4()
    monkeypatch.setattr(
        alert_service,
        "_emit_impl",
        AsyncMock(return_value=(event_id, False, {"id": "x"})),
    )

    async def boom(event, data, ts=None):
        raise RuntimeError("redis down")

    monkeypatch.setattr("app.services.metrics.live.publish_live_event", boom)
    assert await alert_service.emit("x.y", "info", "t", session_factory=_Session) == event_id


async def test_emit_failure_publishes_nothing(monkeypatch):
    monkeypatch.setattr(alert_service, "_emit_impl", AsyncMock(side_effect=RuntimeError("db")))
    rec = Recorder()
    monkeypatch.setattr(alert_service, "publish_live", rec)
    assert await alert_service.emit("x.y", "info", "t", session_factory=_Session) is None
    assert rec.calls == []


def test_alert_live_payload_shape():
    row = SimpleNamespace(
        id=uuid.uuid4(),
        event_type="swap.failed",
        severity="critical",
        title="Swap failed",
        message="boom",
        resource_type="swap_operation",
        resource_id=uuid.uuid4(),
        resource_name="move",
        status="open",
        occurrences=2,
    )
    data = live_mod.alert_live_payload(row, "acknowledged")
    assert data["id"] == str(row.id) and data["status"] == "acknowledged"
    assert {k: data[k] for k in ("event_type", "severity", "title", "message")} == {
        "event_type": "swap.failed",
        "severity": "critical",
        "title": "Swap failed",
        "message": "boom",
    }
    assert data["resource_name"] == "move" and data["occurrences"] == 2


def _alert_service():
    svc = alert_service.AlertService.__new__(alert_service.AlertService)
    svc.session = MagicMock(commit=AsyncMock())
    svc.channels = MagicMock(release_connection=AsyncMock(), record_test=AsyncMock())
    svc.events = MagicMock(update=AsyncMock())
    return svc


@pytest.mark.parametrize("ok", [True, False])
async def test_test_channel_publishes_test_alert(monkeypatch, ok):
    svc = _alert_service()
    channel = SimpleNamespace(id=uuid.uuid4(), name="ops-slack", type="slack", config={})
    svc._get_channel = AsyncMock(return_value=channel)
    monkeypatch.setattr(alert_service, "resolve_channel_secret", AsyncMock(return_value=None))
    result = SendResult(ok=ok, response_code=200 if ok else 500, error=None if ok else "HTTP 500")
    monkeypatch.setattr(alert_service, "send", AsyncMock(return_value=result))
    monkeypatch.setattr("app.services.audit.audit.record", MagicMock())
    rec = Recorder()
    monkeypatch.setattr(alert_service, "publish_live", rec)
    out = await svc.test_channel(channel.id)
    assert out.ok is ok
    [(event, data)] = rec.calls
    assert event == "alert"
    assert data["kind"] == "test" and data["title"] == "Test alert"
    assert data["severity"] == "info" and data["ok"] is ok
    assert data["id"].startswith(f"test:{channel.id}:")
    assert data["channel_id"] == channel.id and data["channel_name"] == "ops-slack"
    assert data["message"] == (None if ok else "HTTP 500")


@pytest.mark.parametrize("status", ["acknowledged", "resolved"])
async def test_set_status_commits_then_publishes(monkeypatch, status):
    svc = _alert_service()
    row = SimpleNamespace(id=uuid.uuid4(), title="GPU hot", acknowledged_at=None)
    svc._get_event = AsyncMock(return_value=row)
    out = SimpleNamespace(
        id=row.id,
        event_type="device.hot",
        severity="warning",
        title="GPU hot",
        message=None,
        resource_type="device",
        resource_id=None,
        resource_name="gpu0",
        status=status,
        occurrences=1,
    )
    svc.get_event = AsyncMock(return_value=out)
    monkeypatch.setattr("app.services.audit.audit.record", MagicMock())
    order = []
    svc.session.commit = AsyncMock(side_effect=lambda: order.append("commit"))

    async def rec(event, data):
        order.append((event, data["id"], data["status"]))

    monkeypatch.setattr(alert_service, "publish_live", rec)
    assert await svc.set_status(row.id, status) is out
    assert order == ["commit", ("alert", str(row.id), status)]


# ---------------------------------------------------------------------------
# swaps
# ---------------------------------------------------------------------------


def _swap_harness(live):
    h = Harness()
    h.executor.live = live
    return h


async def test_swap_publishes_every_state_and_step_change():
    rec = Recorder()
    h = _swap_harness(rec)
    swap = h.swap(unload_spec(h))
    done = await h.drive(swap)
    assert done.state == "completed"
    assert {e for e, _ in rec.calls} == {"swap"}
    frames = [d for _, d in rec.calls]
    assert [f["step"] for f in frames[:-1]] == ["plan", "route_off", "drain", "unload"]
    assert frames[0]["state"] == "pending" and frames[-1]["state"] == "completed"
    assert [f["current_step"] for f in frames] == [0, 1, 2, 3, 4]
    last = frames[-1]
    assert last["swap_id"] == str(swap.id) and last["kind"] == "unload"
    assert last["trigger"] == "rule" and last["deployment_id"] == swap.deployment_id
    assert last["node_id"] == swap.node_id and last["error_message"] is None
    assert {"deployment_name", "rule_name"} <= set(last)


async def test_swap_publishes_names_from_store_labels():
    rec = Recorder()
    h = _swap_harness(rec)
    h.store.swap_labels = lambda swap_id: {
        "deployment_name": "llama",
        "rule_name": "night",
    }
    await h.drive(h.swap(unload_spec(h)))
    assert rec.calls[-1][1]["deployment_name"] == "llama"
    assert rec.calls[-1][1]["rule_name"] == "night"


async def test_swap_publishes_rolled_back_and_failed():
    rec = Recorder()
    h = _swap_harness(rec)
    h.agent.script(
        "drain_instance",
        CommandResult("failed", error_code="drain_timeout", error_message="2 sessions left"),
    )
    done = await h.drive(h.swap(unload_spec(h)))
    states = [d["state"] for _, d in rec.calls]
    assert done.state == "rolled_back"
    assert states[-2:] == ["rolling_back", "rolled_back"]
    assert "2 sessions left" in rec.calls[-1][1]["error_message"]

    rec2 = Recorder()
    h2 = _swap_harness(rec2)
    h2.agent.script("load_model", CommandResult("failed", error_code="oom", error_message="OOM"))
    swap = h2.swap(
        {
            "kind": "load",
            "deployment_id": uuid.uuid4(),
            "node_id": uuid.uuid4(),
            "device_ids": [],
        }
    )
    done2 = await h2.drive(swap)
    assert done2.state == "failed"
    assert rec2.calls[-1][1]["state"] == "failed" and rec2.calls[-1][1]["error_message"]


async def test_swap_dry_run_is_not_announced():
    rec = Recorder()
    h = _swap_harness(rec)
    done = await h.drive(h.swap(unload_spec(h), dry_run=True))
    assert done.state == "completed" and rec.calls == []


async def test_swap_survives_live_failure():
    rec = Recorder(fail=True)
    h = _swap_harness(rec)
    done = await h.drive(h.swap(unload_spec(h)))
    assert done.state == "completed" and len(rec.calls) == 5


# ---------------------------------------------------------------------------
# rule runs
# ---------------------------------------------------------------------------


def _engine(live):
    engine = RuleEngine.__new__(RuleEngine)
    engine._alerts = SimpleNamespace(emit=AsyncMock())
    engine._live = live
    engine.pending_alerts = []
    engine.pending_live = []
    return engine


def _rule_and_run(error=None):
    rule = SimpleNamespace(id=uuid.uuid4(), name="night swap", type="time_schedule")
    run = SimpleNamespace(id=uuid.uuid4(), error_message=error)
    return rule, run


@pytest.mark.parametrize("outcome,dry", [("fired", False), ("dry_run", True), ("error", False)])
async def test_rule_run_published_after_flush(outcome, dry):
    rec = Recorder()
    engine = _engine(rec)
    rule, run = _rule_and_run("boom" if outcome == "error" else None)
    action = SimpleNamespace(action="unload", target_name="llama", target_id="x")
    engine._queue_live(rule, run, outcome, dry, "interval", [action])
    assert rec.calls == []  # nothing before the caller committed
    await engine.flush_alerts()
    [(event, data)] = rec.calls
    assert event == "rule_run"
    assert data["id"] == str(run.id) and data["rule_id"] == str(rule.id)
    assert data["rule_name"] == "night swap" and data["outcome"] == outcome
    assert data["dry_run"] is dry
    assert data["message"] == ("boom" if outcome == "error" else "unload llama")
    assert engine.pending_live == []


@pytest.mark.parametrize("outcome", ["no_match", "skipped", "cooldown"])
async def test_quiet_rule_outcomes_not_published(outcome):
    rec = Recorder()
    engine = _engine(rec)
    rule, run = _rule_and_run()
    engine._queue_live(rule, run, outcome, False, "interval", [])
    await engine.flush_alerts()
    assert rec.calls == []


async def test_rule_run_publish_failure_swallowed():
    rec = Recorder(fail=True)
    engine = _engine(rec)
    rule, run = _rule_and_run()
    engine._queue_live(rule, run, "fired", False, "api", [])
    engine._queue_live(rule, run, "fired", False, "api", [])
    await engine.flush_alerts()
    assert len(rec.calls) == 2
