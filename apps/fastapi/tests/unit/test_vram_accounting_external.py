"""
Unit tests (no services): memory used outside GPU Deck counts in placement,
direct slices, rule placements and the device view; external deployments are
guarded (409 EXTERNAL_DEPLOYMENT); the hub advertises the protocol features
the newer agents gate on.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from app.core.exceptions import ExternalDeploymentError
from app.services.scheduler.core import DeviceView, NodeView, PlacementRequest, place
from app.services.scheduler.usage import free_bytes, occupied_bytes, usage_from_sample
from hypothesis import given
from hypothesis import strategies as st

GB = 1_000_000_000


def _node(device: DeviceView) -> NodeView:
    return NodeView(id=device.node_id, name="droplet", devices=[device])


def _h200(**kw) -> DeviceView:
    node_id = uuid.uuid4()
    return DeviceView(
        id=uuid.uuid4(),
        node_id=node_id,
        index=0,
        vendor="nvidia",
        vram_total_bytes=140 * GB,
        **kw,
    )


def test_external_process_memory_blocks_placement():
    """An external server uses 120 GB of a 140 GB GPU: a 30 GB deployment does not fit."""
    dev = _h200(reserved_bytes=0, observed_used_bytes=120 * GB, external_used_bytes=120 * GB)
    req = PlacementRequest(need_bytes_per_device=30 * GB, headroom_pct=10)
    plan = place([_node(dev)], req)
    assert not plan.feasible
    # Only the total is known (no per-process list): still blocked.
    dev = _h200(observed_used_bytes=120 * GB)
    assert not place([_node(dev)], req).feasible
    # A 5 GB slice fits (140 - 14 headroom - 120 = 6 GB free).
    small = PlacementRequest(need_bytes_per_device=5 * GB, headroom_pct=10)
    assert place([_node(dev)], small).feasible
    # Without a fresh sample only reservations count (the view flags it as stale).
    assert place([_node(_h200())], req).feasible


def test_reservations_and_external_use_add_up():
    # GPU Deck reserved 20 GB but uses 10 GB; an external process uses 100 GB.
    assert occupied_bytes(20 * GB, 110 * GB, 100 * GB) == 120 * GB
    # GPU Deck uses more than it reserved: the observed use counts.
    assert occupied_bytes(20 * GB, 130 * GB, 100 * GB) == 130 * GB
    # Old agents (total only): max(reserved, used).
    assert occupied_bytes(20 * GB, 110 * GB, None) == 110 * GB
    assert occupied_bytes(20 * GB, None, None) == 20 * GB
    assert free_bytes(140 * GB, 0, 120 * GB, 120 * GB, 10) == 6 * GB


@given(
    total=st.integers(min_value=1, max_value=200 * GB),
    reserved=st.integers(min_value=0, max_value=200 * GB),
    used=st.one_of(st.none(), st.integers(min_value=0, max_value=200 * GB)),
    external=st.one_of(st.none(), st.integers(min_value=0, max_value=200 * GB)),
    headroom=st.integers(min_value=0, max_value=50),
)
def test_free_never_plans_into_memory_in_use(total, reserved, used, external, headroom):
    free = free_bytes(total, reserved, used, external, headroom)
    taken = occupied_bytes(reserved, used, external)
    assert taken >= reserved, "GPU Deck reservations always count"
    if used is not None:
        assert taken >= used, "never less than what the GPU reports in use"
    assert free == total - int(total * headroom / 100) - taken
    # A request fits iff it fits into ``free``.
    dev = DeviceView(
        id=uuid.uuid4(),
        node_id=uuid.uuid4(),
        index=0,
        vendor="nvidia",
        vram_total_bytes=total,
        reserved_bytes=reserved,
        observed_used_bytes=used,
        external_used_bytes=external,
    )
    assert dev.free_bytes(headroom) == free


def test_usage_from_sample_tags_and_freshness():
    managed = str(uuid.uuid4())
    observed = str(uuid.uuid4())
    sample = {
        "ts": "2026-11-23T10:00:00Z",
        "memory_used_bytes": 121 * GB,
        "processes": [
            {"pid": 1, "used_memory_bytes": 10 * GB, "instance_id": managed},
            {"pid": 2, "used_memory_bytes": 110 * GB, "instance_id": observed},
            {"pid": 3, "used_memory_bytes": 1 * GB, "instance_id": None},
        ],
    }
    u = usage_from_sample(sample, {managed}, fresh=True)
    assert u.used_bytes == 121 * GB
    assert u.external_bytes == 111 * GB, "observed + unknown processes are outside GPU Deck"
    stale = usage_from_sample(sample, {managed}, fresh=False)
    assert stale.used_bytes is None and stale.external_bytes is None
    assert len(stale.processes) == 3, "the process table still shows"
    no_list = usage_from_sample({"memory_used_bytes": 5}, set(), fresh=True)
    assert no_list.external_bytes is None


def test_rules_device_snapshot_counts_external_memory():
    from app.services.rules.inputs import DeviceSnapshot

    d = DeviceSnapshot(
        id=uuid.uuid4(),
        node_id=uuid.uuid4(),
        node_name="droplet",
        node_status="online",
        vendor="nvidia",
        index=0,
        name="H200",
        vram_total_bytes=140 * GB,
        reserved_bytes=10 * GB,
        observed_used_bytes=130 * GB,
        external_used_bytes=120 * GB,
    )
    assert d.used_bytes == 130 * GB
    assert d.free_bytes == 10 * GB


def test_ensure_managed_guards_external_rows():
    from app.services.deployments.external import (
        ensure_managed,
        is_external,
        observe_ids,
    )

    ext = SimpleNamespace(origin="external", name="main@droplet", id=uuid.uuid4())
    with pytest.raises(ExternalDeploymentError) as exc:
        ensure_managed(ext)
    assert exc.value.status_code == 409 and exc.value.error_code == "EXTERNAL_DEPLOYMENT"
    ensure_managed(SimpleNamespace(origin="managed"))
    assert is_external(SimpleNamespace(origin=SimpleNamespace(value="external")))
    node = uuid.UUID("6f1c2b2e-0b2b-4d8e-9e0e-1b2c3d4e5f60")
    inst, dep = observe_ids(node, "main")
    # Same values as the agent (crates/gpudeck-agent/src/observe) and the protocol example.
    assert str(inst) == "4be123d1-7ce4-5141-bc91-0147ebbcfd96"
    assert str(dep) == "35ebcad7-77e0-5851-b72f-15ef8f42d19a"


def test_hello_ack_advertises_optional_features():
    from app.services.agent_hub.protocol import HUB_FEATURES, validate_payload

    assert set(HUB_FEATURES) == {
        "observed_engines",
        "gpu_processes",
        "secure_transport",
        "agent_updates",
    }
    validate_payload(
        "hello_ack",
        {
            "accepted": True,
            "server_time": "2026-11-23T10:00:00Z",
            "node_id": str(uuid.uuid4()),
            "intervals": {
                "heartbeat_seconds": 10,
                "metrics_seconds": 2,
                "inventory_seconds": 60,
            },
            "config_version": 0,
            "features": HUB_FEATURES,
        },
    )


@pytest.mark.asyncio
async def test_device_view_shows_processes_and_true_free_vram():
    """GPU detail: free = total - headroom - max(reserved, GPU Deck use) - used outside."""
    from app.services.nodes import service as nodes_service

    dev_id, node_id = uuid.uuid4(), uuid.uuid4()
    ext_inst, ext_dep = uuid.uuid4(), uuid.uuid4()
    row = SimpleNamespace(
        id=dev_id,
        node_id=node_id,
        vendor="nvidia",
        index=0,
        uuid="GPU-1",
        name="NVIDIA H200",
        vram_total_bytes=140 * GB,
        is_unified_memory=False,
        driver_version=None,
        runtime_version=None,
        compute_capability=None,
        pci_bus_id=None,
        numa_node=None,
        max_power_watts=None,
        core_count=None,
        health="healthy",
        health_reason=None,
        labels={},
        extra={},
        last_seen_at=None,
        created_at="2026-11-23T10:00:00Z",
        updated_at=None,
    )
    sample = {
        "ts": "2026-11-23T10:00:00Z",
        "device_id": str(dev_id),
        "memory_used_bytes": int(120.1 * GB),
        "processes": [
            {
                "pid": 31337,
                "used_memory_bytes": int(119.6 * GB),
                "name": "VLLM::EngineCor",
                "instance_id": str(ext_inst),
            },
            {"pid": 4100, "used_memory_bytes": int(0.5 * GB), "name": "python3"},
        ],
    }

    class Repo:
        session = object()

        async def reserved_vram_for(self, ids):
            return {}

        async def instance_ids_for(self, ids):
            return {dev_id: [ext_inst]}

        async def list_for_devices(self, ids):
            return {}

    async def owners(session, samples):
        return {
            str(ext_inst): {
                "deployment_id": ext_dep,
                "deployment_name": "main@droplet",
                "origin": "external",
            }
        }

    async def get_many(ids, kind):
        return [sample]

    with (
        patch.object(nodes_service.live_cache, "get_many", get_many),
        patch.object(nodes_service.live_cache, "is_fresh", lambda s: True),
        patch.object(nodes_service, "_process_owners", owners),
    ):
        out = await nodes_service.decorate_devices(Repo(), Repo(), [row], {})
    d = out[0]
    assert d.vram_used_bytes == int(120.1 * GB)
    assert d.vram_used_outside_bytes == int(120.1 * GB), "external + unknown processes"
    assert d.vram_free_bytes == 140 * GB - 14 * GB - int(120.1 * GB)
    assert not d.vram_usage_stale
    assert [(p.pid, p.origin, p.deployment_name) for p in d.processes] == [
        (31337, "external", "main@droplet"),
        (4100, None, None),
    ]


def test_activity_from_live_data_not_only_gpu_deck_instances():
    from datetime import datetime, timedelta, timezone

    from app.services.nodes.activity import device_activity, node_activity, node_detached

    now = datetime.now(timezone.utc)
    # A drained node running processes GPU Deck did not start.
    drained = SimpleNamespace(status="drained", last_heartbeat_at=now - timedelta(seconds=5))
    assert not node_detached(drained, now)
    sample = {
        "ts": now.isoformat(),
        "utilization_pct": 71.0,
        "memory_used_bytes": 120 * GB,
        "processes": [
            {"pid": 1, "used_memory_bytes": 80 * GB, "instance_id": str(uuid.uuid4())},
            {"pid": 2, "used_memory_bytes": 30 * GB, "instance_id": str(uuid.uuid4())},
            {"pid": 3, "used_memory_bytes": 9 * GB, "instance_id": None},
            {"pid": 4, "used_memory_bytes": GB // 2, "instance_id": None},
        ],
    }
    act, outside = device_activity(
        detached=False,
        fresh=True,
        managed_instances=0,
        sample=sample,
        managed=set(),
        reserved_bytes=0,
    )
    assert (act, outside) == ("external", 4)
    assert node_activity([act], detached=False) == "external"
    # The tunnel drops: heartbeat stale -> detached (red), processes still counted.
    gone = SimpleNamespace(status="drained", last_heartbeat_at=now - timedelta(minutes=5))
    assert node_detached(gone, now)
    assert device_activity(
        detached=True,
        fresh=False,
        managed_instances=0,
        sample=sample,
        managed=set(),
        reserved_bytes=0,
    ) == ("detached", 4)
    assert node_activity(["external"], detached=True) == "detached"
    assert node_detached(SimpleNamespace(status="offline", last_heartbeat_at=now), now)
    assert not node_detached(SimpleNamespace(status="pending", last_heartbeat_at=None), now)
    # GPU Deck + outside processes = mixed; only GPU Deck = gpu_deck.
    mine = str(uuid.uuid4())
    both = {**sample, "processes": sample["processes"] + [{"pid": 9, "instance_id": mine}]}
    assert (
        device_activity(
            detached=False,
            fresh=True,
            managed_instances=1,
            sample=both,
            managed={mine},
            reserved_bytes=0,
        )[0]
        == "mixed"
    )
    only = {**sample, "processes": [{"pid": 9, "instance_id": mine}]}
    assert (
        device_activity(
            detached=False,
            fresh=True,
            managed_instances=1,
            sample=only,
            managed={mine},
            reserved_bytes=0,
        )[0]
        == "gpu_deck"
    )
    # Old agents (no process list): utilization / memory beyond reservations means busy.
    old = {"ts": now.isoformat(), "utilization_pct": 80.0, "memory_used_bytes": 50 * GB}
    assert device_activity(
        detached=False,
        fresh=True,
        managed_instances=0,
        sample=old,
        managed=set(),
        reserved_bytes=0,
    ) == ("external", 0)
    quiet = {"ts": now.isoformat(), "utilization_pct": 0.0, "memory_used_bytes": GB // 4}
    assert device_activity(
        detached=False,
        fresh=True,
        managed_instances=0,
        sample=quiet,
        managed=set(),
        reserved_bytes=0,
    ) == ("idle", 0)
    assert device_activity(
        detached=False,
        fresh=False,
        managed_instances=0,
        sample=None,
        managed=set(),
        reserved_bytes=0,
    ) == ("stale", 0)
