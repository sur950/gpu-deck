"""
Placement scheduler core: unit + property tests (pure, no I/O).
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from app.services.scheduler.core import (
    DeviceView,
    InstanceUse,
    NodeView,
    PlacementRequest,
    place,
    plan_evictions,
)
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

GIB = 1024**3


def node(
    name,
    vram_list,
    vendor="nvidia",
    status="online",
    labels=None,
    used=None,
    numa=None,
    cluster=None,
):
    nid = uuid.uuid5(uuid.NAMESPACE_DNS, name)
    devs = [
        DeviceView(
            id=uuid.uuid5(nid, str(i)),
            node_id=nid,
            index=i,
            vendor=vendor,
            vram_total_bytes=int(v * GIB),
            name=f"{name}-gpu{i}",
            reserved_bytes=int((used or [0] * len(vram_list))[i] * GIB),
            numa_node=(numa[i] if numa else None),
        )
        for i, v in enumerate(vram_list)
    ]
    return NodeView(
        id=nid,
        name=name,
        status=status,
        labels=labels or {},
        devices=devs,
        cluster_id=cluster,
    )


def req(need_gib=4, **kw):
    kw.setdefault("headroom_pct", 0)
    return PlacementRequest(need_bytes_per_device=int(need_gib * GIB), **kw)


# --------------------------------------------------------------- basics


def test_single_replica_fits_on_best_device_spread():
    nodes = [node("a", [24, 80]), node("b", [48])]
    plan = place(nodes, req(10))
    assert plan.feasible
    a = plan.assignments[0]
    assert a.node_id == nodes[0].id
    assert a.device_ids == [nodes[0].devices[1].id]  # most free device


def test_pack_prefers_fullest_device_that_fits():
    nodes = [node("a", [24, 80]), node("b", [16])]
    plan = place(nodes, req(10, strategy="pack"))
    assert plan.feasible
    assert plan.assignments[0].device_ids == [nodes[1].devices[0].id]


def test_spread_puts_replicas_on_different_nodes():
    nodes = [node("a", [80]), node("b", [80]), node("c", [80])]
    plan = place(nodes, req(10, replicas=3))
    assert plan.feasible
    assert len({a.node_id for a in plan.assignments}) == 3


def test_pack_fills_one_node_first():
    nodes = [node("a", [80]), node("b", [80])]
    plan = place(nodes, req(10, replicas=3, strategy="pack"))
    assert plan.feasible
    assert len({a.node_id for a in plan.assignments}) == 1


def test_infeasible_reports_reasons():
    nodes = [node("a", [8]), node("b", [80], status="offline")]
    plan = place(nodes, req(10))
    assert not plan.feasible
    reasons = " ".join(r.reason for r in plan.rejected)
    assert "offline" in reasons and "needs" in reasons
    assert "does not fit" in plan.reason


def test_headroom_is_respected():
    nodes = [node("a", [10])]
    assert place(nodes, req(9.5, headroom_pct=0)).feasible
    assert not place(nodes, req(9.5, headroom_pct=10)).feasible


def test_observed_usage_counts_when_higher_than_reserved():
    n = node("a", [24])
    n.devices[0].observed_used_bytes = 20 * GIB
    assert not place([n], req(8)).feasible
    n.devices[0].observed_used_bytes = 2 * GIB
    assert place([n], req(8)).feasible


def test_unhealthy_devices_and_vendor_filters():
    n = node("a", [80, 80])
    n.devices[0].health = "unhealthy"
    plan = place([n], req(10))
    assert plan.assignments[0].device_ids == [n.devices[1].id]
    apple = node("mac", [48], vendor="apple")
    assert not place([apple], req(4, vendors=["nvidia", "amd"])).feasible
    assert place([apple, n], req(4, vendor_filter=["apple"])).assignments[0].node_id == apple.id


def test_label_selector_and_node_ids():
    a = node("a", [80], labels={"zone": "x", "tier": "h100"})
    b = node("b", [80], labels={"zone": "y"})
    plan = place([a, b], req(4, node_selector={"zone": "y"}))
    assert plan.assignments[0].node_id == b.id
    plan = place([a, b], req(4, node_selector={"zone": ["x", "z"]}))
    assert plan.assignments[0].node_id == a.id
    plan = place([a, b], req(4, node_selector={"node_ids": [str(b.id)]}))
    assert plan.assignments[0].node_id == b.id
    assert not place([a, b], req(4, node_selector={"tier": "a100"})).feasible


def test_cluster_and_os_filters():
    c1, c2 = uuid.uuid4(), uuid.uuid4()
    a = node("a", [80], cluster=c1)
    b = node("b", [80], cluster=c2)
    b.os = "macos"
    assert place([a, b], req(4, cluster_id=c2)).assignments[0].node_id == b.id
    assert not place([b], req(4, supported_os=["linux"])).feasible


def test_tensor_parallel_takes_devices_on_one_node():
    a = node("a", [24, 24])
    b = node("b", [24, 24, 24, 24])
    plan = place([a, b], req(20, gpu_count=4, tp_size=4))
    assert plan.feasible
    assignment = plan.assignments[0]
    assert assignment.node_id == b.id and len(assignment.device_ids) == 4


def test_tp_prefers_same_numa_group():
    n = node("a", [80, 80, 80, 80], numa=[0, 1, 0, 1])
    plan = place([n], req(10, gpu_count=2, tp_size=2))
    picked = [d for d in n.devices if d.id in plan.assignments[0].device_ids]
    assert len({d.numa_node for d in picked}) == 1


def test_gpu_count_must_match_tp_pp():
    plan = place([node("a", [80, 80])], req(4, gpu_count=2, tp_size=1, pp_size=1))
    assert not plan.feasible and "tp_size" in plan.reason


def test_pipeline_parallel_spans_nodes_when_allowed():
    a = node("a", [24, 24])
    b = node("b", [24, 24])
    r = req(20, gpu_count=4, tp_size=2, pp_size=2)
    assert not place([a, b], r).feasible
    r.allow_cross_node_pp = True
    plan = place([a, b], r)
    assert plan.feasible
    shards = plan.assignments[0].shards
    assert len(shards) == 2 and {s.node_id for s in shards} == {a.id, b.id}
    assert [s.pp_rank for s in shards] == [0, 1]
    assert len(plan.assignments[0].all_device_ids()) == 4


def test_slices_share_device_by_vram_and_compute():
    n = node("a", [80])
    plan = place([n], req(1, replicas=4, slice_vram_bytes=16 * GIB, slice_compute_pct=25))
    assert plan.feasible and len(plan.assignments) == 4
    assert plan.assignments[0].slice == {"vram_bytes": 16 * GIB, "compute_pct": 25}
    plan = place([n], req(1, replicas=5, slice_vram_bytes=8 * GIB, slice_compute_pct=25))
    assert not plan.feasible and "compute slices full" in plan.reason


def test_slices_need_single_gpu():
    plan = place([node("a", [80, 80])], req(1, gpu_count=2, tp_size=2, slice_vram_bytes=GIB))
    assert not plan.feasible


def test_pinned_node_and_devices():
    a = node("a", [80, 80])
    b = node("b", [80])
    plan = place([a, b], req(4, pinned_node_id=a.id, pinned_device_ids=[a.devices[0].id]))
    assert plan.assignments[0].device_ids == [a.devices[0].id]
    full = node("c", [8])
    assert not place([full], req(10, pinned_node_id=full.id)).feasible


def test_input_snapshot_is_not_mutated():
    nodes = [node("a", [80])]
    place(nodes, req(10, replicas=3))
    assert nodes[0].devices[0].reserved_bytes == 0


def test_existing_replicas_push_spread_elsewhere():
    a, b = node("a", [80]), node("b", [80])
    plan = place([a, b], req(4, existing_per_node={a.id: 1}))
    assert plan.assignments[0].node_id == b.id


def test_evictions_pick_lowest_priority_and_never_always_on():
    n = node("a", [24])
    dev = n.devices[0]
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    recent = datetime.now(timezone.utc)
    uses = [
        InstanceUse(uuid.uuid4(), uuid.uuid4(), "always_on", 8 * GIB),
        InstanceUse(uuid.uuid4(), uuid.uuid4(), "evictable", 8 * GIB, last_request_at=recent),
        InstanceUse(uuid.uuid4(), uuid.uuid4(), "evictable", 8 * GIB, last_request_at=old),
    ]
    dev.instances = uses
    dev.reserved_bytes = 24 * GIB
    victims = plan_evictions([n], req(8), requester_priority="normal")
    assert [v.instance_id for v in victims] == [uses[2].instance_id]
    victims = plan_evictions([n], req(16), requester_priority="normal")
    assert {v.instance_id for v in victims} == {
        uses[1].instance_id,
        uses[2].instance_id,
    }
    assert plan_evictions([n], req(20), requester_priority="normal") == []
    assert plan_evictions([n], req(8), requester_priority="evictable") == []


def test_evictions_empty_when_it_already_fits():
    assert plan_evictions([node("a", [80])], req(8)) == []


# ------------------------------------------------------- property tests

device_sizes = st.lists(st.sampled_from([8, 16, 24, 48, 80]), min_size=1, max_size=4)
cluster = st.lists(device_sizes, min_size=1, max_size=4)


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    cluster=cluster,
    need=st.integers(min_value=1, max_value=60),
    replicas=st.integers(min_value=1, max_value=6),
    tp=st.sampled_from([1, 2]),
    strategy=st.sampled_from(["spread", "pack"]),
    headroom=st.sampled_from([0, 10]),
)
def test_property_no_device_is_overbooked(cluster, need, replicas, tp, strategy, headroom):
    nodes = [node(f"n{i}", sizes) for i, sizes in enumerate(cluster)]
    r = req(
        need,
        replicas=replicas,
        gpu_count=tp,
        tp_size=tp,
        strategy=strategy,
        headroom_pct=headroom,
    )
    plan = place(nodes, r)
    booked = {}
    for a in plan.assignments:
        assert len(a.device_ids) == tp
        assert len(set(a.device_ids)) == tp
        node_devs = {d.id: d for n in nodes if n.id == a.node_id for d in n.devices}
        assert set(a.device_ids) <= set(node_devs), "devices must belong to the assigned node"
        for d in a.device_ids:
            booked[d] = booked.get(d, 0) + r.per_device_bytes
    for n in nodes:
        for d in n.devices:
            cap = d.vram_total_bytes - int(d.vram_total_bytes * headroom / 100)
            assert booked.get(d.id, 0) <= cap
    if plan.feasible:
        assert len(plan.assignments) == replicas
    else:
        assert plan.reason


@settings(max_examples=100, deadline=None)
@given(
    cluster=cluster,
    need=st.integers(min_value=1, max_value=40),
    replicas=st.integers(1, 5),
)
def test_property_deterministic(cluster, need, replicas):
    nodes = [node(f"n{i}", sizes) for i, sizes in enumerate(cluster)]
    r = req(need, replicas=replicas)
    p1, p2 = place(nodes, r), place(list(reversed(nodes)), r)
    assert [(a.node_id, a.device_ids) for a in p1.assignments] == [
        (a.node_id, a.device_ids) for a in p2.assignments
    ]


@settings(max_examples=100, deadline=None)
@given(cluster=cluster, need=st.integers(min_value=1, max_value=40))
def test_property_single_replica_feasible_iff_some_device_fits(cluster, need):
    nodes = [node(f"n{i}", sizes) for i, sizes in enumerate(cluster)]
    fits = any(d.vram_total_bytes >= need * GIB for n in nodes for d in n.devices)
    assert place(nodes, req(need)).feasible == fits


@settings(max_examples=80, deadline=None)
@given(
    slices=st.integers(min_value=1, max_value=8),
    pct=st.sampled_from([10, 20, 25, 50]),
    size=st.sampled_from([4, 8, 16]),
)
def test_property_slices_respect_compute_and_vram(slices, pct, size):
    n = node("a", [80])
    plan = place([n], req(1, replicas=slices, slice_vram_bytes=size * GIB, slice_compute_pct=pct))
    expected = slices * pct <= 100 and slices * size <= 80
    assert plan.feasible == expected


@pytest.mark.parametrize("strategy", ["spread", "pack"])
def test_spread_pack_on_equal_nodes_is_stable(strategy):
    nodes = [node("a", [80]), node("b", [80])]
    plan = place(nodes, req(10, replicas=2, strategy=strategy))
    ids = [a.node_id for a in plan.assignments]
    assert (len(set(ids)) == 2) == (strategy == "spread")


def test_device_fraction_engines_do_not_share_a_device():
    n = node("a", [80, 80])
    r = req(10, replicas=2, min_device_fraction=0.9)
    plan = place([n], r)
    assert plan.feasible
    assert {a.device_ids[0] for a in plan.assignments} == {
        n.devices[0].id,
        n.devices[1].id,
    }
    assert not place([n], req(10, replicas=3, min_device_fraction=0.9)).feasible
    # a lowered fraction lets two share one device
    assert place([node("b", [80])], req(10, replicas=2, min_device_fraction=0.45)).feasible
