"""
Pydantic schemas for swap operations (mirror of admin.yaml: SwapCreate,
SwapOperation, SwapStep).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from app.core.logger import get_logger
from pydantic import BaseModel, ConfigDict, Field

logger = get_logger(__name__)


class SwapKind(str, Enum):
    LOAD = "load"
    UNLOAD = "unload"
    SWAP = "swap"
    SCALE_UP = "scale_up"
    SCALE_DOWN = "scale_down"
    RESTART = "restart"
    MOVE = "move"
    APPLY_LAYOUT = "apply_layout"


class SwapState(str, Enum):
    PENDING = "pending"
    DRAINING = "draining"
    UNLOADING = "unloading"
    LOADING = "loading"
    HEALTH_CHECK = "health_check"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"
    CANCELLED = "cancelled"


TERMINAL_SWAP_STATES = frozenset(
    {SwapState.COMPLETED, SwapState.FAILED, SwapState.ROLLED_BACK, SwapState.CANCELLED}
)


class SwapTrigger(str, Enum):
    MANUAL = "manual"
    RULE = "rule"
    LAYOUT = "layout"
    AUTO_HEAL = "auto_heal"
    API = "api"
    BENCHMARK = "benchmark"


class SwapCreateKind(str, Enum):
    LOAD = "load"
    UNLOAD = "unload"
    MOVE = "move"
    RESTART = "restart"


class SwapCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: SwapCreateKind
    deployment_id: uuid.UUID
    instance_id: Optional[uuid.UUID] = None
    node_id: Optional[uuid.UUID] = None
    device_ids: List[uuid.UUID] = Field(default_factory=list)
    drain_timeout_seconds: int = Field(default=120, ge=0, le=3600)
    force: bool = False
    dry_run: bool = False


class SwapStepState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class SwapStepOut(BaseModel):
    name: str
    state: SwapStepState
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    command_id: Optional[uuid.UUID] = None
    error: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


class SwapOperationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: SwapKind
    state: SwapState
    trigger: SwapTrigger
    rule_run_id: Optional[uuid.UUID] = None
    rule_name: Optional[str] = None
    layout_id: Optional[uuid.UUID] = None
    deployment_id: Optional[uuid.UUID] = None
    deployment_name: Optional[str] = None
    from_instance_id: Optional[uuid.UUID] = None
    to_instance_id: Optional[uuid.UUID] = None
    node_id: Optional[uuid.UUID] = None
    plan: Dict[str, Any] = Field(default_factory=dict)
    steps: List[SwapStepOut] = Field(default_factory=list)
    current_step: int = 0
    rollback: Dict[str, Any] = Field(default_factory=dict)
    drain_timeout_seconds: int = 120
    dry_run: bool = False
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime
