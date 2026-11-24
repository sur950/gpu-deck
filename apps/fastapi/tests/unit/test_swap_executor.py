"""
Unit tests: swap state machine with in-memory fakes — happy paths, drain
timeout, rollback, resume after restart, fencing, locks, cancel, dependencies,
plus the planner's step ordering.
"""

import uuid
from datetime import datetime, timedelta, timezone

from app.services.swap.executor import SwapExecutor
from app.services.swap.fakes import (
    FakeAgent,
    FakeHealth,
    FakeLoadArgs,
    FakeRouting,
    MemoryLocks,
    MemorySwapStore,
    RecordingAlerts,
)
from app.services.swap.planner import build_plan
from app.services.swap.ports import CommandResult, InstanceRecord, Swap

DEP = uuid.uuid4()
NODE = uuid.uuid4()
DEV_A, DEV_B = uuid.uuid4(), uuid.uuid4()


class Harness:
    def __init__(self, deployments=None, fence=None, health=None):
        self.store = MemorySwapStore()
        self.agent = FakeAgent()
        self.routing = FakeRouting()
        self.alerts = RecordingAlerts()
        self.locks = MemoryLocks()
        self.health = health or FakeHealth()
        self.executor = SwapExecutor(
            store=self.store,
            commands=self.agent,
            routing=self.routing,
            load_args=FakeLoadArgs(),
            health=self.health,
            locks=self.locks,
            alerts=self.alerts,
            fence=fence,
            deployments=deployments,
        )
        self.old = self.store.add_instance(
            InstanceRecord(
                id=uuid.uuid4(),
                deployment_id=DEP,
                node_id=NODE,
                device_ids=[DEV_A],
                state="ready",
                health="healthy",
                port=10240,
                served_model_name="m",
                traffic_weight=100,
            )
        )

    def swap(self, spec, **extra):
        built = build_plan(spec, [DEV_A] if spec.get("instance_id") else [])
        swap = Swap(
            id=uuid.uuid4(),
            kind=built["kind"],
            state="pending",
            trigger="rule",
            deployment_id=DEP,
            from_instance_id=(
                uuid.UUID(built["from_instance_id"]) if built["from_instance_id"] else None
            ),
            node_id=NODE,
            plan=built["plan"],
            steps=built["steps"],
            drain_timeout_seconds=60,
            **extra,
        )
        return self.store.add_swap(swap)

    async def drive(self, swap):
        return await self.executor.drive(swap.id)

    def step_names(self, swap_id):
        return [s["name"] for s in self.store.swaps[str(swap_id)].steps]


def unload_spec(h, **extra):
    return {"kind": "unload", "instance_id": h.old.id, **extra}


async def test_unload_happy_path_drains_before_unload():
    h = Harness()
    swap = h.swap(unload_spec(h))
    done = await h.drive(swap)
    assert done.state == "completed"
    assert h.agent.commands() == ["drain_instance", "unload_model"]
    assert h.routing.calls[0] == ("accepting", str(h.old.id), False)
    assert h.store.instances[str(h.old.id)].state == "stopped"
    assert [s["state"] for s in done.steps] == ["completed"] * 4
    assert h.store.finished == [str(swap.id)]
    assert {a["event_type"] for a in h.store.audit} >= {
        "instance.unloaded",
        "swap.completed",
    }
    assert h.locks.held == {}


async def test_load_happy_path_creates_instance_and_routes_on():
    h = Harness()
    swap = h.swap(
        {
            "kind": "load",
            "deployment_id": DEP,
            "node_id": NODE,
            "device_ids": [DEV_B],
            "slice": {"vram_bytes": 1024, "compute_pct": 50},
            "traffic_weight": 70,
        }
    )
    done = await h.drive(swap)
    assert done.state == "completed"
    new = h.store.instances[str(done.to_instance_id)]
    assert new.state == "ready" and new.device_ids == [DEV_B] and new.port is not None
    assert h.store.slices[str(new.id)]["status"] == "active"
    assert ("accepting", str(new.id), True) in h.routing.calls
    assert ("weight", str(new.id), 70) in h.routing.calls
    assert h.agent.commands() == ["load_model"]


async def test_move_on_another_device_loads_first():
    h = Harness()
    swap = h.swap(
        {
            "kind": "move",
            "instance_id": h.old.id,
            "deployment_id": DEP,
            "node_id": NODE,
            "device_ids": [DEV_B],
        }
    )
    assert swap.plan["order"] == "load_first"
    done = await h.drive(swap)
    assert done.state == "completed"
    assert h.agent.commands() == ["load_model", "drain_instance", "unload_model"]
    assert h.store.instances[str(h.old.id)].state == "stopped"


def test_planner_orders_and_locks():
    old = uuid.uuid4()
    same = build_plan({"kind": "move", "instance_id": old, "device_ids": [DEV_A]}, [DEV_A])
    assert same["plan"]["order"] == "unload_first"
    assert [s["name"] for s in same["steps"]] == [
        "plan",
        "route_off",
        "drain",
        "unload",
        "load",
        "health",
        "route_on",
    ]
    other = build_plan({"kind": "move", "instance_id": old, "device_ids": [DEV_B]}, [DEV_A])
    assert other["plan"]["order"] == "load_first"
    assert set(other["plan"]["lock_device_ids"]) == {str(DEV_A), str(DEV_B)}
    restart = build_plan({"kind": "restart", "instance_id": old}, [DEV_A])
    assert [s["name"] for s in restart["steps"]] == [
        "plan",
        "route_off",
        "restart",
        "health",
        "route_on",
    ]


async def test_drain_timeout_rolls_back_and_routes_old_instance_on():
    h = Harness()
    h.agent.script(
        "drain_instance",
        CommandResult("failed", error_code="drain_timeout", error_message="2 sessions left"),
    )
    done = await h.drive(h.swap(unload_spec(h)))
    assert done.state == "rolled_back"
    assert "unload_model" not in h.agent.commands()
    assert h.store.instances[str(h.old.id)].state == "ready"
    assert h.routing.accepting[str(h.old.id)] is True
    assert done.rollback["attempted"] is True and done.rollback["result"] == "route_on"
    assert h.alerts.events[-1]["event_type"] == "swap.rolled_back"


async def test_drain_timeout_with_force_after_timeout_unloads():
    h = Harness()
    h.agent.script("drain_instance", CommandResult("timed_out", error_code="timeout"))
    done = await h.drive(h.swap(unload_spec(h, force_after_timeout=True)))
    assert done.state == "completed"
    unload = [c for c in h.agent.sent if c["command"] == "unload_model"][0]
    assert unload["args"]["force"] is True


async def test_load_failure_after_unload_reloads_previous_instance():
    h = Harness()
    h.agent.script(
        "load_model",
        CommandResult("failed", error_code="oom", error_message="OOM"),
        CommandResult("succeeded", {"port": 10299}),
    )
    swap = h.swap(
        {
            "kind": "move",
            "instance_id": h.old.id,
            "deployment_id": DEP,
            "node_id": NODE,
            "device_ids": [DEV_A],
        }
    )  # same device -> unload first
    done = await h.drive(swap)
    assert done.state == "rolled_back"
    assert done.rollback["result"] == "reloaded_previous_spec"
    old = h.store.instances[str(h.old.id)]
    assert old.state == "ready" and old.port == 10299
    new = h.store.instances[str(done.to_instance_id)]
    assert new.state in ("stopped", "error")


async def test_failed_rollback_marks_deployment_degraded_and_alerts_critical():
    h = Harness()
    h.agent.script(
        "load_model",
        CommandResult("failed", error_message="OOM"),
        CommandResult("failed", error_message="still OOM"),
    )
    done = await h.drive(
        h.swap(
            {
                "kind": "move",
                "instance_id": h.old.id,
                "deployment_id": DEP,
                "node_id": NODE,
                "device_ids": [DEV_A],
            }
        )
    )
    assert done.state == "failed"
    assert h.store.deployment_status[str(DEP)][0] == "degraded"
    assert h.alerts.events[-1]["event_type"] == "swap.failed"
    assert h.alerts.events[-1]["severity"] == "critical"


async def test_health_failure_cleans_up_new_instance():
    h = Harness(health=FakeHealth(CommandResult("failed", error_message="503")))
    done = await h.drive(
        h.swap(
            {
                "kind": "load",
                "deployment_id": DEP,
                "node_id": NODE,
                "device_ids": [DEV_B],
            }
        )
    )
    assert done.state == "failed"
    assert h.store.instances[str(done.to_instance_id)].state == "stopped"
    assert h.agent.commands() == ["load_model", "unload_model"]


async def test_resume_after_restart_rewaits_same_command():
    h = Harness()
    swap = h.swap(unload_spec(h))
    # simulate a crash while waiting for the drain: steps 0-1 done, drain running w/ command id
    cid = await h.agent.send(NODE, "drain_instance", {}, 60)
    stored = h.store.swaps[str(swap.id)]
    stored.started_at = datetime.now(timezone.utc)
    stored.state = "draining"
    stored.current_step = 2
    stored.rollback = {
        "routed_off": True,
        "attempted": False,
        "previous_instance_spec": h.old.spec(),
    }
    for step in stored.steps[:2]:
        step["state"] = "completed"
    stored.steps[2].update(state="running", command_id=str(cid))
    done = await h.drive(swap)
    assert done.state == "completed"
    assert h.agent.commands() == ["drain_instance", "unload_model"]  # drain not re-sent
    assert str(cid) in h.agent.wait_calls


async def test_lost_fence_stops_without_failing():
    async def fence():
        return False

    h = Harness(fence=fence)
    swap = h.swap(unload_spec(h))
    done = await h.drive(swap)
    assert done.state == "pending" and h.agent.sent == []


async def test_fenced_store_write_stops_executor():
    h = Harness()
    h.store.fence_ok = False
    done = await h.drive(h.swap(unload_spec(h)))
    assert done.state not in ("failed", "rolled_back")
    assert h.store.swaps[str(done.id)].state == "pending"


async def test_device_lock_serialises_swaps():
    h = Harness()
    h.locks.held[f"swap:lock:device:{DEV_A}"] = "someone-else"
    done = await h.drive(h.swap(unload_spec(h)))
    assert done.state == "pending" and h.agent.sent == []


async def test_cancel_before_unload_resumes_old_instance():
    h = Harness()
    swap = h.swap(unload_spec(h))
    h.store.cancel_flags.add(str(swap.id))
    done = await h.drive(swap)
    assert done.state == "cancelled"
    assert h.store.instances[str(h.old.id)].state == "ready"


async def test_dependencies_wait_then_cancel_on_failure():
    h = Harness()
    first = h.swap(unload_spec(h))
    second = h.swap({"kind": "load", "deployment_id": DEP, "node_id": NODE, "device_ids": [DEV_A]})
    second_stored = h.store.swaps[str(second.id)]
    second_stored.plan["depends_on"] = [str(first.id)]
    assert (await h.drive(second)).state == "pending"
    h.store.swaps[str(first.id)].state = "failed"
    done = await h.drive(second)
    assert done.state == "cancelled" and "dependency" in done.error_message


async def test_dry_run_swap_completes_without_commands():
    h = Harness()
    done = await h.drive(h.swap(unload_spec(h), dry_run=True))
    assert done.state == "completed" and h.agent.sent == []
    assert {s["state"] for s in done.steps} == {"skipped"}


async def test_swap_older_than_max_duration_fails():
    h = Harness()
    swap = h.swap(unload_spec(h), started_at=datetime.now(timezone.utc) - timedelta(hours=2))
    done = await h.drive(swap)
    assert done.state == "failed" and "SWAP_MAX_DURATION" in done.error_message


async def test_restart_bumps_restart_count():
    h = Harness()
    done = await h.drive(h.swap({"kind": "restart", "instance_id": h.old.id}))
    assert done.state == "completed"
    assert h.agent.commands() == ["restart_instance"]
    assert h.store.instances[str(h.old.id)].restart_count == 1


class FakeDeployments:
    """DeploymentPort fake (deployments path): placement + start/stop through the fake agent."""

    def __init__(self, harness):
        self.h = harness
        self.calls = []

    async def place_instance(self, deployment_id, node_id, device_ids, slice_spec, traffic_weight):
        self.calls.append("place")
        return await self.h.store.create_instance(
            deployment_id, node_id, device_ids, traffic_weight, "placed-by-deployments"
        )

    async def start_instance(self, instance_id, swap_operation_id):
        self.calls.append("start")
        return await self.h.agent.send(NODE, "load_model", {"instance_id": str(instance_id)}, 600)

    async def stop_instance(self, instance_id, force, drain_timeout_seconds, swap_operation_id):
        self.calls.append("stop")
        return await self.h.agent.send(NODE, "unload_model", {"instance_id": str(instance_id)}, 60)


async def test_move_through_deployment_port():
    h = Harness()
    deps = FakeDeployments(h)
    h.executor.deployments = deps
    done = await h.drive(
        h.swap(
            {
                "kind": "move",
                "instance_id": h.old.id,
                "deployment_id": DEP,
                "node_id": NODE,
                "device_ids": [DEV_B],
            }
        )
    )
    assert done.state == "completed"
    assert deps.calls == ["place", "start", "stop"]
    assert h.store.instances[str(done.to_instance_id)].served_model_name in (
        "placed-by-deployments",
        "fake-model",
    )
