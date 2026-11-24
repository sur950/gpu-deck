"""
Interfaces the swap manager codes against.

The swap manager does not own the agent hub, the deployment / placement services or
the model cache, so every external effect goes through one of the small
protocols below. ``adapters.py`` ships default implementations that talk to
the database, Redis and the agent proxy directly (and pick up the other
those services when they are importable); tests use in-memory fakes.

Dataclasses here mirror ``swap_operations`` / ``instances`` rows so the
executor never touches the ORM.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Protocol, Sequence

from app.core.logger import get_logger

logger = get_logger(__name__)


@dataclass
class CommandResult:
    status: str  # succeeded | failed | rejected | cancelled | timed_out
    result: Optional[Dict[str, Any]] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status == "succeeded"


@dataclass
class InstanceRecord:
    id: uuid.UUID
    deployment_id: uuid.UUID
    node_id: uuid.UUID
    device_ids: List[uuid.UUID]
    state: str
    health: str = "unknown"
    port: Optional[int] = None
    served_model_name: Optional[str] = None
    traffic_weight: int = 100
    engine_process_id: Optional[uuid.UUID] = None
    resources: Dict[str, Any] = field(default_factory=dict)
    ready_at: Optional[datetime] = None
    restart_count: int = 0

    def spec(self) -> Dict[str, Any]:
        """What rollback needs to put the instance back."""
        return {
            "instance_id": str(self.id),
            "deployment_id": str(self.deployment_id),
            "node_id": str(self.node_id),
            "device_ids": [str(d) for d in self.device_ids],
            "port": self.port,
            "served_model_name": self.served_model_name,
            "traffic_weight": self.traffic_weight,
            "engine_process_id": (str(self.engine_process_id) if self.engine_process_id else None),
            "resources": dict(self.resources or {}),
        }


@dataclass
class Swap:
    id: uuid.UUID
    kind: str
    state: str
    trigger: str
    deployment_id: Optional[uuid.UUID] = None
    from_instance_id: Optional[uuid.UUID] = None
    to_instance_id: Optional[uuid.UUID] = None
    node_id: Optional[uuid.UUID] = None
    plan: Dict[str, Any] = field(default_factory=dict)
    steps: List[Dict[str, Any]] = field(default_factory=list)
    current_step: int = 0
    rollback: Dict[str, Any] = field(default_factory=dict)
    drain_timeout_seconds: int = 120
    dry_run: bool = False
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error_message: Optional[str] = None
    rule_run_id: Optional[uuid.UUID] = None
    layout_id: Optional[uuid.UUID] = None
    created_by: Optional[uuid.UUID] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def is_terminal(self) -> bool:
        return self.state in ("completed", "failed", "rolled_back", "cancelled")


def make_step(name: str) -> Dict[str, Any]:
    return {
        "name": name,
        "state": "pending",
        "started_at": None,
        "finished_at": None,
        "command_id": None,
        "error": None,
        "details": None,
    }


class SwapStore(Protocol):
    """Persistence the executor needs (DB in production, dict in tests)."""

    async def get_swap(self, swap_id: uuid.UUID) -> Optional[Swap]: ...

    async def save_swap(self, swap: Swap) -> None: ...

    async def swap_states(self, swap_ids: Sequence[uuid.UUID]) -> Dict[str, str]: ...

    async def get_instance(self, instance_id: uuid.UUID) -> Optional[InstanceRecord]: ...

    async def update_instance(self, instance_id: uuid.UUID, **fields: Any) -> None: ...

    async def create_instance(
        self,
        deployment_id: uuid.UUID,
        node_id: uuid.UUID,
        device_ids: Sequence[uuid.UUID],
        traffic_weight: int,
        served_model_name: Optional[str],
        instance_id: Optional[uuid.UUID] = None,
    ) -> InstanceRecord: ...

    async def reserve_slices(
        self,
        instance_id: uuid.UUID,
        deployment_id: uuid.UUID,
        device_ids: Sequence[uuid.UUID],
        vram_bytes: int,
        compute_pct: int,
    ) -> None: ...

    async def set_slices_status(self, instance_id: uuid.UUID, status: str) -> None: ...

    async def set_deployment_status(
        self, deployment_id: uuid.UUID, status: str, message: Optional[str]
    ) -> None: ...

    async def after_finished(self, swap: Swap) -> None:
        """Called once a swap reaches a terminal state (sync deployment replicas/status)."""
        ...

    async def write_audit(
        self,
        event_type: str,
        resource_type: str,
        resource_id: Optional[uuid.UUID],
        resource_name: Optional[str],
        before: Optional[Dict[str, Any]],
        after: Optional[Dict[str, Any]],
        message: Optional[str],
        metadata: Optional[Dict[str, Any]] = None,
        outcome: str = "success",
    ) -> None: ...


class CommandPort(Protocol):
    """Send a command to a node agent and wait for its terminal result."""

    async def send(
        self,
        node_id: uuid.UUID,
        command: str,
        args: Dict[str, Any],
        timeout_seconds: int,
        swap_operation_id: Optional[uuid.UUID] = None,
        instance_id: Optional[uuid.UUID] = None,
    ) -> uuid.UUID: ...

    async def wait(self, command_id: uuid.UUID, timeout_seconds: float) -> CommandResult: ...

    async def cancel(self, command_id: uuid.UUID) -> None: ...


class RoutingPort(Protocol):
    """Tell the API layer whether an instance may receive new requests."""

    async def set_accepting(self, instance_id: uuid.UUID, accepting: bool) -> None: ...

    async def set_weight(self, instance_id: uuid.UUID, weight: int) -> None: ...

    async def refresh(self) -> None: ...


class LoadArgsPort(Protocol):
    async def build_load_args(
        self, instance: InstanceRecord, slice_spec: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]: ...


class DeploymentPort(Protocol):
    """
    the deployments instance lifecycle (``DeploymentService.place_instance / start_instance /
    stop_instance``). When given, the executor creates, loads and unloads
    instances through it so placement bookkeeping (slices, spec_hash, served
    model name, router-mode engine processes) stays in one place.
    """

    async def place_instance(
        self,
        deployment_id: uuid.UUID,
        node_id: Optional[uuid.UUID],
        device_ids: Sequence[uuid.UUID],
        slice_spec: Optional[Dict[str, Any]],
        traffic_weight: int,
    ) -> InstanceRecord: ...

    async def start_instance(
        self, instance_id: uuid.UUID, swap_operation_id: Optional[uuid.UUID]
    ) -> uuid.UUID: ...

    async def stop_instance(
        self,
        instance_id: uuid.UUID,
        force: bool,
        drain_timeout_seconds: int,
        swap_operation_id: Optional[uuid.UUID],
    ) -> Optional[uuid.UUID]: ...


class HealthProbePort(Protocol):
    async def probe(self, instance: InstanceRecord) -> CommandResult: ...


class LockPort(Protocol):
    async def acquire(self, keys: Sequence[str], owner: str, ttl_seconds: int) -> bool: ...

    async def release(self, keys: Sequence[str], owner: str) -> None: ...


class PrefetchPort(Protocol):
    async def prefetch(
        self,
        model_version_id: uuid.UUID,
        node_id: uuid.UUID,
        pin: bool = False,
        pin_reason: Optional[str] = None,
    ) -> Optional[uuid.UUID]: ...


class AlertsPort(Protocol):
    async def emit(
        self,
        event_type: str,
        severity: str,
        title: str,
        message: Optional[str] = None,
        *,
        resource_type: Optional[str] = None,
        resource_id: Optional[uuid.UUID] = None,
        resource_name: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        dedupe_key: Optional[str] = None,
    ) -> Optional[uuid.UUID]: ...


class FenceLost(Exception):
    """The jobs-leader lease (fencing token) is no longer ours: stop touching the swap."""


class StepFailure(Exception):
    """A swap step failed; the executor rolls back."""

    def __init__(self, message: str, code: Optional[str] = None, details: Optional[Dict] = None):
        super().__init__(message)
        self.code = code or "step_failed"
        self.details = details or {}
