"""
Layouts that make two models trade two full GPUs (qwen3 b -> a, tinyllama a -> b, each device
fits one model): planned as two ``move`` swaps, the first failed with NO_CAPACITY (it loads on
its target before unloading its source, and the target still holds the other model) and the
second was cancelled. The planner now splits moves on a cycle into unload + load (all unloads
first, each load depending on the unload that frees its device); an acyclic chain of moves is
ordered so each move runs after the one that frees its target.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from app.core.logger import get_logger
from app.services.layouts.service import LayoutService, PlannedSwap, apply_order
from app.services.rules.inputs import (
    DeploymentSnapshot,
    DeviceSnapshot,
    InstanceSnapshot,
    StaticRuleInputs,
)
from app.services.swap.placement import estimate_vram_need

logger = get_logger(__name__)

GIB = 1024**3
NODE = uuid.uuid4()


def _device(index: int) -> DeviceSnapshot:
    return DeviceSnapshot(
        id=uuid.uuid4(),
        node_id=NODE,
        node_name="gpu-node",
        node_status="online",
        vendor="nvidia",
        index=index,
        name=f"GPU {index}",
        vram_total_bytes=24 * GIB,
        health="healthy",
    )


def _deployment(name: str, device: DeviceSnapshot) -> DeploymentSnapshot:
    dep = DeploymentSnapshot(
        id=uuid.uuid4(),
        name=name,
        model_id=uuid.uuid4(),
        model_name=name,
        model_type="llm",
        priority="normal",
        status="running",
        desired_running=True,
        replicas=1,
        min_replicas=1,
        max_replicas=1,
        last_request_at=None,
        cluster_id=None,
        engine_name="llama_cpp",
        placement_strategy="spread",
        model_version_id=uuid.uuid4(),
        model_size_bytes=12 * GIB,
    )
    dep.instances = [
        InstanceSnapshot(
            id=uuid.uuid4(),
            deployment_id=dep.id,
            node_id=NODE,
            device_ids=[device.id],
            state="ready",
            health="healthy",
        )
    ]
    device.reserved_bytes += estimate_vram_need(dep)
    return dep


def _item(dep: DeploymentSnapshot, device: DeviceSnapshot, position: int):
    return SimpleNamespace(
        deployment_id=dep.id,
        node_id=NODE,
        device_ids=[device.id],
        replicas=1,
        slice_vram_bytes=None,
        slice_compute_pct=None,
        traffic_weight=100,
        position=position,
    )


class _Repo:
    def __init__(self, items):
        self.items = items
        self.applied = None

    async def items_for(self, layout_id):
        return self.items

    async def mark_applied(self, layout_id, when):
        self.applied = when


class _Planner:
    created: list = []

    def __init__(self, session):
        pass

    async def create(self, spec, **kw):
        row = SimpleNamespace(id=uuid.uuid4(), spec=spec, depends_on=list(kw["depends_on"]))
        _Planner.created.append(row)
        return row


class _NoPrefetch:
    async def prefetch(self, *a, **kw):
        return None


@pytest.fixture
def night(monkeypatch):
    """Day: qwen3 on GPU b, tinyllama on GPU a. Layout "night" swaps them."""
    gpu_a, gpu_b = _device(0), _device(1)
    qwen = _deployment("qwen3", gpu_b)
    tiny = _deployment("tinyllama", gpu_a)
    # Each device fits exactly one model: a second copy does not fit next to the first.
    assert gpu_a.free_bytes < 2 * estimate_vram_need(qwen)
    inputs = StaticRuleInputs(deployments=[qwen, tiny], devices=[gpu_a, gpu_b])
    svc = LayoutService(session=SimpleNamespace(), inputs=inputs, prefetcher=_NoPrefetch())
    svc.repo = _Repo([_item(qwen, gpu_a, 0), _item(tiny, gpu_b, 1)])
    layout = SimpleNamespace(id=uuid.uuid4(), name="night", unlisted_policy="keep")

    async def _get(layout_id):
        return layout

    svc._get = _get
    import app.services.audit as audit_mod
    import app.services.swap.planner as planner_mod

    monkeypatch.setattr(planner_mod, "SwapPlanner", _Planner)
    monkeypatch.setattr(audit_mod.audit, "record", lambda *a, **kw: None)
    _Planner.created = []
    return SimpleNamespace(svc=svc, layout=layout, qwen=qwen, tiny=tiny, a=gpu_a, b=gpu_b)


async def test_cyclic_move_is_split_into_unloads_then_loads(night):
    plan = await night.svc.plan(night.layout.id)
    kinds = sorted(s.spec["kind"] for s in plan.swaps)
    assert kinds == ["scale_down", "scale_down", "scale_up", "scale_up"], kinds
    assert plan.diff.feasible
    assert any("trade devices" in w for w in plan.diff.warnings), plan.diff.warnings
    assert {e.action for e in plan.diff.entries} == {"move"}

    await night.svc.apply(night.layout.id)
    created = _Planner.created
    assert [c.spec["kind"] for c in created[:2]] == ["scale_down", "scale_down"]
    unload_of = {c.spec["instance_id"]: c.id for c in created[:2]}
    by_dep = {c.spec["deployment_id"]: c for c in created[2:]}
    # qwen3 loads on GPU a once tinyllama (on GPU a) is unloaded, and the other way round.
    qwen_load, tiny_load = by_dep[night.qwen.id], by_dep[night.tiny.id]
    assert qwen_load.spec["device_ids"] == [night.a.id]
    assert qwen_load.depends_on == [unload_of[night.tiny.instances[0].id]]
    assert tiny_load.spec["device_ids"] == [night.b.id]
    assert tiny_load.depends_on == [unload_of[night.qwen.instances[0].id]]
    assert all(c.depends_on == [] for c in created[:2])
    assert night.svc.repo.applied is not None


def _swap(key, kind, frees=(), uses=()):
    return PlannedSwap(
        key=key,
        spec={"kind": kind},
        deployment_id=uuid.uuid4(),
        frees_devices=list(frees),
        uses_devices=list(uses),
    )


def test_acyclic_moves_run_after_the_move_that_frees_their_target():
    # A moves onto d1, which B frees by moving to the empty d2: B first, then A.
    a = _swap("a", "move", frees=["d0"], uses=["d1"])
    b = _swap("b", "move", frees=["d1"], uses=["d2"])
    load = _swap("c", "load", uses=["d0"])
    unload = _swap("u", "unload", frees=["d9"])
    reweight = _swap("r", "reweight")
    assert [s.key for s in apply_order([a, load, b, reweight, unload])] == [
        "u",
        "r",
        "b",
        "a",
        "c",
    ]
