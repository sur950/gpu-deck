"""
Unit tests: each rule type fires / does not fire on synthetic metrics
(StaticRuleInputs), with the protections from rules.md section 2.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from app.services.rules.evaluators import EvalContext, evaluate
from app.services.rules.inputs import (
    DeploymentSnapshot,
    DeviceSnapshot,
    InstanceSnapshot,
    MinuteBucket,
    StaticRuleInputs,
)
from app.services.rules.validation import validate_params

NOW = datetime(2026, 11, 17, 12, 0, 30, tzinfo=timezone.utc)
GIB = 1024**3
NODE = uuid.uuid4()


def device(free_gib=24, total_gib=24, node=NODE, dev_id=None, status="online"):
    return DeviceSnapshot(
        id=dev_id or uuid.uuid4(),
        node_id=node,
        node_name="n1",
        node_status=status,
        vendor="nvidia",
        index=0,
        name="gpu",
        vram_total_bytes=total_gib * GIB,
        reserved_bytes=(total_gib - free_gib) * GIB,
        health="healthy",
    )


def instance(
    dep_id,
    dev,
    ready_minutes_ago=120,
    active=0,
    waiting=0,
    health="healthy",
    state="ready",
    weight=100,
):
    return InstanceSnapshot(
        id=uuid.uuid4(),
        deployment_id=dep_id,
        node_id=dev.node_id,
        device_ids=[dev.id],
        state=state,
        health=health,
        ready_at=NOW - timedelta(minutes=ready_minutes_ago),
        active_sessions=active,
        waiting_sessions=waiting,
        traffic_weight=weight,
    )


def deployment(
    name="chat",
    priority="normal",
    idle_minutes=None,
    replicas=1,
    min_replicas=0,
    max_replicas=4,
    status="running",
    size_gib=4,
    slice_gib=None,
    status_message=None,
):
    dep_id = uuid.uuid4()
    return DeploymentSnapshot(
        id=dep_id,
        name=name,
        model_id=uuid.uuid4(),
        model_name=name,
        model_type="chat",
        priority=priority,
        status=status,
        desired_running=True,
        replicas=replicas,
        min_replicas=min_replicas,
        max_replicas=max_replicas,
        last_request_at=(
            (NOW - timedelta(minutes=idle_minutes)) if idle_minutes is not None else None
        ),
        cluster_id=None,
        engine_name="llama_cpp",
        placement_strategy="spread",
        model_version_id=uuid.uuid4(),
        model_size_bytes=size_gib * GIB,
        slice_vram_bytes=slice_gib * GIB if slice_gib else None,
        status_message=status_message,
        created_at=NOW - timedelta(days=1),
    )


def buckets(minutes, **values):
    end = NOW.replace(second=0, microsecond=0)
    return [MinuteBucket(ts=end - timedelta(minutes=m + 1), **values) for m in range(minutes)]


async def run(rule_type, params, inputs, target=None, trigger="interval"):
    return await evaluate(
        rule_type,
        validate_params(rule_type, params),
        target or {},
        inputs,
        EvalContext(trigger=trigger),
    )


# ---------------------------------------------------------------- idle_unload


async def test_idle_unload_fires_after_n_minutes():
    dev = device()
    dep = deployment(idle_minutes=45)
    dep.instances = [instance(dep.id, dev)]
    ev = await run("idle_unload", {"idle_minutes": 30}, StaticRuleInputs(NOW, [dep], [dev]))
    assert len(ev.actionable) == 1
    action = ev.actionable[0].actions[0]
    assert action.action == "unload" and action.target_id == dep.instances[0].id
    assert ev.actionable[0].observed["idle_minutes"] == pytest.approx(45.0, abs=0.1)


async def test_idle_unload_does_not_fire_before_n_minutes_or_with_sessions():
    dev = device()
    fresh = deployment(idle_minutes=10)
    fresh.instances = [instance(fresh.id, dev)]
    busy = deployment(name="busy", idle_minutes=90)
    busy.instances = [instance(busy.id, dev, active=1)]
    ev = await run("idle_unload", {"idle_minutes": 30}, StaticRuleInputs(NOW, [fresh, busy], [dev]))
    assert ev.matches == []


async def test_idle_unload_never_touches_always_on():
    dev = device()
    dep = deployment(priority="always_on", idle_minutes=600)
    dep.instances = [instance(dep.id, dev)]
    ev = await run("idle_unload", {"idle_minutes": 30}, StaticRuleInputs(NOW, [dep], [dev]))
    assert ev.actionable == []
    assert ev.matches[0].skipped_reason == "always_on"


async def test_idle_unload_grace_keep_and_min_replicas():
    dev = device()
    young = deployment(idle_minutes=60)
    young.instances = [instance(young.id, dev, ready_minutes_ago=5)]
    ev = await run(
        "idle_unload",
        {"idle_minutes": 30, "min_loaded_minutes": 10},
        StaticRuleInputs(NOW, [young], [dev]),
    )
    assert ev.matches[0].skipped_reason == "min_loaded_minutes"

    two = deployment(idle_minutes=60, min_replicas=1)
    two.instances = [instance(two.id, dev, ready_minutes_ago=90), instance(two.id, dev)]
    ev = await run("idle_unload", {"idle_minutes": 30}, StaticRuleInputs(NOW, [two], [dev]))
    assert len(ev.actionable[0].actions) == 1  # deployments.min_replicas keeps one

    ev = await run(
        "idle_unload",
        {"idle_minutes": 30, "keep_replicas": 2},
        StaticRuleInputs(NOW, [two], [dev]),
    )
    assert ev.matches[0].skipped_reason == "keep_replicas"


async def test_idle_unload_respects_time_windows():
    dev = device()
    dep = deployment(idle_minutes=60)
    dep.instances = [instance(dep.id, dev)]
    params = {
        "idle_minutes": 30,
        "respect_time_windows": ["22:00-06:00"],
        "timezone": "UTC",
    }
    ev = await run("idle_unload", params, StaticRuleInputs(NOW, [dep], [dev]))  # 12:00 UTC
    assert ev.matches[0].skipped_reason == "outside_window"


async def test_selector_limits_targets():
    dev = device()
    a = deployment(name="chat-a", idle_minutes=60)
    a.instances = [instance(a.id, dev)]
    b = deployment(name="embed-b", idle_minutes=60)
    b.instances = [instance(b.id, dev)]
    ev = await run(
        "idle_unload",
        {"idle_minutes": 30},
        StaticRuleInputs(NOW, [a, b], [dev]),
        target={"name_glob": "chat-*"},
    )
    assert [m.deployment_id for m in ev.matches] == [a.id]


# ---------------------------------------------------------------- low_traffic_shrink


async def test_low_traffic_shrink_fires_when_every_minute_is_below():
    dev = device()
    dep = deployment(min_replicas=0)
    dep.instances = [instance(dep.id, dev), instance(dep.id, dev)]
    inputs = StaticRuleInputs(
        NOW, [dep], [dev], deployment_minutes={dep.id: buckets(5, requests=1)}
    )
    ev = await run(
        "low_traffic_shrink",
        {"requests_per_minute_below": 2, "for_minutes": 5, "min_replicas": 1},
        inputs,
    )
    actions = ev.actionable[0].actions
    assert [a.action for a in actions] == ["scale_down"]


async def test_low_traffic_shrink_does_not_fire_on_one_busy_minute_or_missing_bucket():
    dev = device()
    dep = deployment()
    dep.instances = [instance(dep.id, dev), instance(dep.id, dev)]
    data = buckets(5, requests=1)
    data[2].requests = 10
    ev = await run(
        "low_traffic_shrink",
        {"requests_per_minute_below": 2, "for_minutes": 5},
        StaticRuleInputs(NOW, [dep], [dev], deployment_minutes={dep.id: data}),
    )
    assert ev.matches == []
    ev = await run(
        "low_traffic_shrink",
        {"requests_per_minute_below": 2, "for_minutes": 5},
        StaticRuleInputs(NOW, [dep], [dev], deployment_minutes={dep.id: buckets(4)}),
    )
    assert ev.matches == []


async def test_low_traffic_shrink_always_on_floor_is_one():
    dev = device()
    dep = deployment(priority="always_on")
    dep.instances = [instance(dep.id, dev)]
    ev = await run(
        "low_traffic_shrink",
        {"requests_per_minute_below": 2, "for_minutes": 3, "min_replicas": 0},
        StaticRuleInputs(NOW, [dep], [dev], deployment_minutes={dep.id: buckets(3)}),
    )
    assert ev.matches[0].skipped_reason == "min_replicas"


# ---------------------------------------------------------------- high_load_grow


async def test_high_load_grow_adds_a_replica_on_a_free_gpu():
    busy_dev, free_dev = device(free_gib=1), device(free_gib=20)
    dep = deployment(size_gib=4)
    dep.instances = [instance(dep.id, busy_dev, waiting=9)]
    inputs = StaticRuleInputs(
        NOW,
        [dep],
        [busy_dev, free_dev],
        deployment_minutes={dep.id: buckets(2, waiting_avg=8.0)},
    )
    ev = await run(
        "high_load_grow",
        {"for_minutes": 2, "max_replicas": 3, "waiting_requests_above": 5},
        inputs,
    )
    action = ev.actionable[0].actions[0]
    assert action.action == "scale_up"
    assert action.spec["device_ids"] == [free_dev.id]


async def test_high_load_grow_needs_every_minute_and_respects_caps():
    dev, free = device(free_gib=1), device(free_gib=20)
    dep = deployment(max_replicas=1)
    dep.instances = [instance(dep.id, dev)]
    data = buckets(3, waiting_avg=8.0)
    data[0].waiting_avg = 1.0
    ev = await run(
        "high_load_grow",
        {"for_minutes": 3, "max_replicas": 4, "waiting_requests_above": 5},
        StaticRuleInputs(NOW, [dep], [dev, free], deployment_minutes={dep.id: data}),
    )
    assert ev.matches == []
    ev = await run(
        "high_load_grow",
        {
            "for_minutes": 3,
            "max_replicas": 4,
            "kv_cache_pct_above": 90,
            "waiting_requests_above": None,
        },
        StaticRuleInputs(
            NOW,
            [dep],
            [dev, free],
            deployment_minutes={dep.id: buckets(3, kv_pct_max=95.0)},
        ),
    )
    assert ev.matches[0].skipped_reason == "max_replicas"  # deployment max_replicas=1


async def test_high_load_grow_no_capacity_without_make_room():
    dev = device(free_gib=1)
    dep = deployment(size_gib=8)
    dep.instances = [instance(dep.id, dev)]
    ev = await run(
        "high_load_grow",
        {"for_minutes": 2, "max_replicas": 3, "waiting_requests_above": 5},
        StaticRuleInputs(
            NOW, [dep], [dev], deployment_minutes={dep.id: buckets(2, waiting_avg=9.0)}
        ),
    )
    assert ev.matches[0].skipped_reason == "no_capacity"


# ---------------------------------------------------------------- make_room_by_priority


def _room_setup(victim_priority="evictable", victim_idle=30, requester_priority="normal"):
    dev = device(free_gib=2, total_gib=24)
    victim = deployment(
        name="victim", priority=victim_priority, idle_minutes=victim_idle, slice_gib=12
    )
    victim.instances = [instance(victim.id, dev)]
    requester = deployment(
        name="requester",
        priority=requester_priority,
        status="pending",
        size_gib=8,
        status_message="insufficient_vram on every GPU",
    )
    return dev, victim, requester


async def test_make_room_evicts_lowest_priority_idle_model():
    dev, victim, requester = _room_setup()
    keeper = deployment(name="keeper", priority="always_on", idle_minutes=600, slice_gib=10)
    keeper.instances = [instance(keeper.id, dev)]
    ev = await run(
        "make_room_by_priority",
        {},
        StaticRuleInputs(NOW, [victim, keeper, requester], [dev]),
    )
    actions = ev.actionable[0].actions
    assert [a.action for a in actions] == ["evict", "load"]
    assert actions[0].target_id == victim.instances[0].id
    assert actions[1].spec["depends_on_actions"] == [str(victim.instances[0].id)]


async def test_make_room_on_placement_failure_reads_the_scheduler_message():
    """The scheduler calls make_room_for with the deployment pinned; its status message is the
    scheduler's 'does not fit' text (live: rule ran with considered=0 before)."""
    dev, victim, requester = _room_setup()
    requester.status_message = "replica 1 of 1 does not fit: needs 1394 MiB, 655 MiB free"
    ev = await run(
        "make_room_by_priority",
        {},
        StaticRuleInputs(NOW, [victim, requester], [dev]),
        target={"deployment_ids": [str(requester.id)]},
        trigger="placement_failure",
    )
    assert [a.action for a in ev.actionable[0].actions] == ["evict", "load"]


async def test_make_room_never_evicts_always_on_or_equal_priority():
    for victim_priority in ("always_on", "normal"):
        dev, victim, requester = _room_setup(victim_priority=victim_priority)
        ev = await run(
            "make_room_by_priority",
            {"evict_priorities": ["evictable", "normal"]},
            StaticRuleInputs(NOW, [victim, requester], [dev]),
        )
        assert ev.actionable == []
        assert ev.matches[0].skipped_reason == "no_eligible_victim"


async def test_make_room_requires_victim_idle():
    dev, victim, requester = _room_setup(victim_idle=1)
    ev = await run(
        "make_room_by_priority",
        {"victim_idle_minutes": 5},
        StaticRuleInputs(NOW, [victim, requester], [dev]),
    )
    assert ev.matches[0].skipped_reason == "no_eligible_victim"


async def test_make_room_placement_failure_only_mode():
    dev, victim, requester = _room_setup()
    inputs = StaticRuleInputs(NOW, [victim, requester], [dev])
    ev = await run("make_room_by_priority", {"trigger": "placement_failure"}, inputs)
    assert ev.matches == []
    ev = await run(
        "make_room_by_priority",
        {"trigger": "placement_failure"},
        inputs,
        trigger="placement_failure",
    )
    assert len(ev.actionable) == 1


# ---------------------------------------------------------------- auto_heal


async def test_auto_heal_restarts_after_consecutive_health_failures():
    dev = device()
    dep = deployment(priority="always_on")
    inst = instance(dep.id, dev, health="unhealthy")
    dep.instances = [inst]
    params = {"health_failures": 3, "action": "restart_then_move"}
    ev = await run(
        "auto_heal",
        params,
        StaticRuleInputs(NOW, [dep], [dev], health_streaks={inst.id: 2}),
    )
    assert ev.matches == []
    ev = await run(
        "auto_heal",
        params,
        StaticRuleInputs(NOW, [dep], [dev], health_streaks={inst.id: 3}),
    )
    assert ev.actionable[0].actions[0].action == "restart"  # applies to always_on too


async def test_auto_heal_error_rate_needs_min_requests():
    dev = device()
    dep = deployment()
    inst = instance(dep.id, dev)
    dep.instances = [inst]
    params = {
        "health_failures": None,
        "error_rate_pct_above": 20,
        "for_minutes": 3,
        "min_requests_in_window": 30,
        "action": "restart",
    }
    hot = buckets(3, requests=20, errors=10, error_rate_pct=50.0)
    ev = await run(
        "auto_heal",
        params,
        StaticRuleInputs(NOW, [dep], [dev], instance_minutes={inst.id: hot}),
    )
    assert ev.actionable[0].observed["reasons"] == ["error_rate"]
    few = buckets(3, requests=2, errors=1, error_rate_pct=50.0)
    ev = await run(
        "auto_heal",
        params,
        StaticRuleInputs(NOW, [dep], [dev], instance_minutes={inst.id: few}),
    )
    assert ev.matches == []


async def test_auto_heal_escalates_to_move_then_gives_up():
    dev, spare = device(), device(free_gib=20)
    dep = deployment()
    inst = instance(dep.id, dev, health="unhealthy")
    dep.instances = [inst]
    inputs = StaticRuleInputs(
        NOW, [dep], [dev, spare], health_streaks={inst.id: 5}, restarts={inst.id: 3}
    )
    ev = await run("auto_heal", {"health_failures": 3, "max_restarts_per_hour": 3}, inputs)
    action = ev.actionable[0].actions[0]
    assert action.action == "move" and action.spec["device_ids"] == [spare.id]
    assert action.spec["order"] == "load_first"
    ev = await run(
        "auto_heal",
        {"health_failures": 3, "max_restarts_per_hour": 3, "action": "restart"},
        inputs,
    )
    assert ev.matches[0].skipped_reason == "giving_up"


async def test_time_schedule_builds_apply_layout_action():
    layout_id = uuid.uuid4()
    ev = await run(
        "time_schedule",
        {"layout_id": str(layout_id), "at": "00:00"},
        StaticRuleInputs(NOW),
    )
    action = ev.actionable[0].actions[0]
    assert action.action == "apply_layout" and action.target_id == layout_id


async def test_make_room_respects_the_requester_node_selector():
    """A requester pinned to a full node must not count free room on another node as
    'placement feasible' (live: make-room never fired for a node-pinned deployment)."""
    full = device(free_gib=2, total_gib=24)
    other = device(free_gib=24, total_gib=24, node=uuid.uuid4())
    victim = deployment(name="victim", priority="evictable", idle_minutes=30, slice_gib=12)
    victim.instances = [instance(victim.id, full)]
    requester = deployment(
        name="requester",
        status="pending",
        size_gib=8,
        status_message="replica 1 of 1 does not fit: needs 9000 MiB, 2000 MiB free",
    )
    requester.node_selector = {"node_ids": [str(NODE)]}
    ev = await run(
        "make_room_by_priority",
        {},
        StaticRuleInputs(NOW, [victim, requester], [full, other]),
    )
    actions = ev.actionable[0].actions
    assert [a.action for a in actions] == ["evict", "load"]
    assert actions[0].target_id == victim.instances[0].id
    requester.node_selector = {}  # unpinned: the free node is enough, nothing to evict
    ev = await run(
        "make_room_by_priority",
        {},
        StaticRuleInputs(NOW, [victim, requester], [full, other]),
    )
    assert ev.actionable == []
