"""
In-memory fakes of every swap port (tests and local experiments that need a swap
manager without Postgres / Redis / agents).

``FakeAgent`` scripts command outcomes per command name::

    agent = FakeAgent()
    agent.script("drain_instance", CommandResult("failed", error_code="drain_timeout"))
    agent.script("load_model", CommandResult("succeeded", {"port": 10240}))
"""

from __future__ import annotations

import copy
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from app.core.logger import get_logger
from app.services.swap.ports import CommandResult, FenceLost, InstanceRecord, Swap

logger = get_logger(__name__)


class MemorySwapStore:
    def __init__(self) -> None:
        self.swaps: Dict[str, Swap] = {}
        self.instances: Dict[str, InstanceRecord] = {}
        self.slices: Dict[str, Dict[str, Any]] = {}
        self.deployment_status: Dict[str, tuple] = {}
        self.audit: List[Dict[str, Any]] = []
        self.finished: List[str] = []
        self.fence_ok = True
        self.saves = 0
        self.cancel_flags: set = set()

    # swaps
    def add_swap(self, swap: Swap) -> Swap:
        self.swaps[str(swap.id)] = copy.deepcopy(swap)
        return swap

    async def get_swap(self, swap_id: uuid.UUID) -> Optional[Swap]:
        swap = self.swaps.get(str(swap_id))
        return copy.deepcopy(swap) if swap else None

    async def save_swap(self, swap: Swap) -> None:
        if not self.fence_ok:
            raise FenceLost("fake fence lost")
        self.saves += 1
        self.swaps[str(swap.id)] = copy.deepcopy(swap)

    async def cancel_requested(self, swap_id: uuid.UUID) -> bool:
        return str(swap_id) in self.cancel_flags

    async def swap_states(self, swap_ids: Sequence[uuid.UUID]) -> Dict[str, str]:
        return {str(i): self.swaps[str(i)].state for i in swap_ids if str(i) in self.swaps}

    # instances
    def add_instance(self, inst: InstanceRecord) -> InstanceRecord:
        self.instances[str(inst.id)] = inst
        return inst

    async def get_instance(self, instance_id: uuid.UUID) -> Optional[InstanceRecord]:
        inst = self.instances.get(str(instance_id))
        return copy.deepcopy(inst) if inst else None

    async def update_instance(self, instance_id: uuid.UUID, **fields: Any) -> None:
        inst = self.instances.get(str(instance_id))
        if inst is None:
            return
        for key, value in fields.items():
            if hasattr(inst, key):
                setattr(inst, key, value)

    async def create_instance(
        self,
        deployment_id: uuid.UUID,
        node_id: uuid.UUID,
        device_ids: Sequence[uuid.UUID],
        traffic_weight: int,
        served_model_name: Optional[str],
        instance_id: Optional[uuid.UUID] = None,
    ) -> InstanceRecord:
        inst = InstanceRecord(
            id=instance_id or uuid.uuid4(),
            deployment_id=deployment_id,
            node_id=node_id,
            device_ids=list(device_ids),
            state="pending",
            traffic_weight=traffic_weight,
            served_model_name=served_model_name,
        )
        self.instances[str(inst.id)] = inst
        return copy.deepcopy(inst)

    async def reserve_slices(self, instance_id, deployment_id, device_ids, vram_bytes, compute_pct):
        self.slices[str(instance_id)] = {
            "device_ids": [str(d) for d in device_ids],
            "vram_bytes": vram_bytes,
            "compute_pct": compute_pct,
            "status": "reserved",
        }

    async def set_slices_status(self, instance_id: uuid.UUID, status: str) -> None:
        if str(instance_id) in self.slices:
            self.slices[str(instance_id)]["status"] = status

    async def set_deployment_status(self, deployment_id, status, message) -> None:
        self.deployment_status[str(deployment_id)] = (status, message)

    async def after_finished(self, swap: Swap) -> None:
        self.finished.append(str(swap.id))

    async def write_audit(
        self,
        event_type,
        resource_type,
        resource_id,
        resource_name,
        before,
        after,
        message,
        metadata=None,
        outcome="success",
    ) -> None:
        self.audit.append(
            {
                "event_type": event_type,
                "resource_type": resource_type,
                "resource_id": str(resource_id) if resource_id else None,
                "outcome": outcome,
            }
        )


class FakeAgent:
    """CommandPort fake. Unscripted commands succeed; ``load_model`` returns a port."""

    def __init__(self) -> None:
        self.sent: List[Dict[str, Any]] = []
        self.results: Dict[str, CommandResult] = {}
        self._scripts: Dict[str, List[CommandResult]] = defaultdict(list)
        self.cancelled: List[str] = []
        self.wait_calls: List[str] = []

    def script(self, command: str, *results: CommandResult) -> None:
        self._scripts[command].extend(results)

    def commands(self) -> List[str]:
        return [c["command"] for c in self.sent]

    async def send(
        self,
        node_id,
        command,
        args,
        timeout_seconds,
        swap_operation_id=None,
        instance_id=None,
    ) -> uuid.UUID:
        cid = uuid.uuid4()
        self.sent.append(
            {
                "id": str(cid),
                "node_id": str(node_id),
                "command": command,
                "args": args,
                "timeout": timeout_seconds,
            }
        )
        if self._scripts[command]:
            result = self._scripts[command].pop(0)
        elif command == "load_model":
            result = CommandResult(
                "succeeded",
                {"port": 10240 + len(self.sent), "served_model_name": "fake-model"},
            )
        else:
            result = CommandResult("succeeded", {})
        self.results[str(cid)] = result
        return cid

    async def wait(self, command_id: uuid.UUID, timeout_seconds: float) -> CommandResult:
        self.wait_calls.append(str(command_id))
        return self.results.get(str(command_id), CommandResult("timed_out", error_code="timeout"))

    async def cancel(self, command_id: uuid.UUID) -> None:
        self.cancelled.append(str(command_id))


class FakeRouting:
    def __init__(self) -> None:
        self.calls: List[tuple] = []
        self.accepting: Dict[str, bool] = {}
        self.weights: Dict[str, int] = {}

    async def set_accepting(self, instance_id, accepting: bool) -> None:
        self.calls.append(("accepting", str(instance_id), accepting))
        self.accepting[str(instance_id)] = accepting

    async def set_weight(self, instance_id, weight: int) -> None:
        self.calls.append(("weight", str(instance_id), weight))
        self.weights[str(instance_id)] = weight

    async def refresh(self) -> None:
        self.calls.append(("refresh",))


class FakeLoadArgs:
    async def build_load_args(self, instance: InstanceRecord, slice_spec) -> Dict[str, Any]:
        return {
            "instance_id": str(instance.id),
            "deployment_id": str(instance.deployment_id),
            "slice": slice_spec,
            "health_timeout_seconds": 30,
        }


class FakeHealth:
    def __init__(self, *results: CommandResult) -> None:
        self._results = list(results)
        self.calls = 0

    async def probe(self, instance: InstanceRecord) -> CommandResult:
        self.calls += 1
        if self._results:
            return self._results.pop(0)
        return CommandResult("succeeded", {"health": "healthy"})


class MemoryLocks:
    def __init__(self) -> None:
        self.held: Dict[str, str] = {}

    async def acquire(self, keys, owner, ttl_seconds) -> bool:
        if any(self.held.get(k) not in (None, owner) for k in keys):
            return False
        for k in keys:
            self.held[k] = owner
        return True

    async def release(self, keys, owner) -> None:
        for k in keys:
            if self.held.get(k) == owner:
                del self.held[k]


class RecordingAlerts:
    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []

    async def emit(self, event_type, severity, title, message=None, **kwargs) -> uuid.UUID:
        self.events.append(
            {
                "event_type": event_type,
                "severity": severity,
                "title": title,
                "message": message,
                **kwargs,
            }
        )
        return uuid.uuid4()


class RecordingPrefetch:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    async def prefetch(self, model_version_id, node_id, pin=False, pin_reason=None):
        self.calls.append(
            {
                "model_version_id": str(model_version_id),
                "node_id": str(node_id),
                "pin": pin,
                "pin_reason": pin_reason,
            }
        )
        return uuid.uuid4()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
