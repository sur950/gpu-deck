"""
Agent update schemas (agent protocol §14): node update status, update runs, cluster rolling
updates and the per-cluster policy. Shapes follow ``libs/contracts/openapi/admin.yaml``
components of the same names.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

AgentUpdateRunState = Literal[
    "pending",
    "staging",
    "staged",
    "swapping",
    "restarting",
    "readopting",
    "health_check",
    "succeeded",
    "rolled_back",
    "failed",
    "cancelled",
]
AgentRolloutState = Literal["running", "succeeded", "failed", "cancelled"]
NodeAgentUpdateMode = Literal["helper", "self", "managed", "unsupported", "manual"]
NodeAgentUpdateStatus = Literal[
    "disabled",
    "up_to_date",
    "update_available",
    "staging",
    "staged",
    "updating",
    "managed",
    "manual",
    "unsupported",
]
Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAYS: List[str] = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_VERSION = r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$"


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Response(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AgentUpdateStep(BaseModel):
    phase: str
    at: Optional[datetime] = None
    message: Optional[str] = None


class AgentUpdateRunResponse(_Response):
    id: UUID
    node_id: UUID
    node_name: Optional[str] = None
    cluster_id: Optional[UUID] = None
    rollout_id: Optional[UUID] = None
    from_version: Optional[str] = None
    to_version: str
    state: AgentUpdateRunState
    trigger: str
    steps: List[AgentUpdateStep] = Field(default_factory=list)
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    health_timeout_seconds: int = 120
    deadline_at: Optional[datetime] = None
    created_by: Optional[UUID] = None
    started_at: datetime
    finished_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class NodeAgentUpdate(BaseModel):
    """Update status of one node (also embedded in NodeResponse.agent_update)."""

    current_version: Optional[str] = None
    target_version: Optional[str] = None
    status: NodeAgentUpdateStatus
    mode: NodeAgentUpdateMode
    reason: Optional[str] = None
    update_available: bool = False
    can_update: bool = False
    state: Optional[str] = Field(
        None, description="Reported by the agent: idle | staging | staged | applying"
    )
    staged_version: Optional[str] = None
    staged_at: Optional[datetime] = None
    previous_version: Optional[str] = None
    instructions: Optional[str] = None
    active_run: Optional[AgentUpdateRunResponse] = None
    last_run: Optional[AgentUpdateRunResponse] = None


class AgentUpdateWindow(_Request):
    days: List[Weekday] = Field(min_length=1, max_length=7)
    start: str = Field(description="HH:MM (24 h) in timezone")
    end: str = Field(description="HH:MM; earlier than start = the window ends the next day")
    timezone: str = Field(default="UTC", max_length=64, description="IANA time zone, e.g. UTC")

    @field_validator("start", "end")
    @classmethod
    def _hhmm(cls, value: str) -> str:
        if not _HHMM.fullmatch(value):
            raise ValueError("time must be HH:MM (00:00-23:59)")
        return value

    @field_validator("timezone")
    @classmethod
    def _tz(cls, value: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"unknown time zone {value!r}")
        return value

    @field_validator("days")
    @classmethod
    def _days(cls, value: List[str]) -> List[str]:
        return [d for d in WEEKDAYS if d in set(value)]

    @model_validator(mode="after")
    def _not_empty(self) -> "AgentUpdateWindow":
        if self.start == self.end:
            raise ValueError("window start and end must differ")
        return self


class AgentUpdatePolicy(_Request):
    """``clusters.config.agent_updates``."""

    mode: Literal["manual", "auto"] = "manual"
    window: Optional[AgentUpdateWindow] = None

    @model_validator(mode="after")
    def _auto_needs_window(self) -> "AgentUpdatePolicy":
        if self.mode == "auto" and self.window is None:
            raise ValueError("mode auto needs a maintenance window")
        return self


class AgentRolloutSkipped(BaseModel):
    node_id: UUID
    node_name: Optional[str] = None
    reason: str


class AgentRolloutResponse(_Response):
    id: UUID
    cluster_id: UUID
    target_version: str
    trigger: str
    state: AgentRolloutState
    node_ids: List[UUID] = Field(default_factory=list)
    current_node_id: Optional[UUID] = None
    completed_count: int = 0
    skipped: List[AgentRolloutSkipped] = Field(default_factory=list)
    failed_node_id: Optional[UUID] = None
    message: Optional[str] = None
    created_by: Optional[UUID] = None
    started_at: datetime
    finished_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class AgentUpdateCounts(BaseModel):
    total: int = 0
    up_to_date: int = 0
    update_available: int = 0
    staged: int = 0
    updating: int = 0
    manual: int = 0
    managed: int = 0
    unsupported: int = 0
    offline: int = 0


class ClusterAgentUpdate(BaseModel):
    cluster_id: UUID
    policy: AgentUpdatePolicy
    in_window: bool = False
    next_window_at: Optional[datetime] = None
    target_version: Optional[str] = None
    rollout: Optional[AgentRolloutResponse] = None
    counts: AgentUpdateCounts


class AgentUpdateOverview(BaseModel):
    enabled: bool
    reason: Optional[str] = None
    target_version: Optional[str] = None
    release_keys: int = 0
    prestage: bool = True
    health_timeout_seconds: int = 120
    targets: List[str] = Field(default_factory=list)
    source: Optional[str] = None
    available_versions: List[str] = Field(default_factory=list)
    counts: AgentUpdateCounts


class AgentUpdateRequest(_Request):
    version: Optional[str] = Field(
        None, pattern=_VERSION, description="Default: the target version"
    )
    health_timeout_seconds: Optional[int] = Field(None, ge=30, le=900)


class AgentRolloutRequest(_Request):
    version: Optional[str] = Field(
        None, pattern=_VERSION, description="Default: the target version"
    )


class AgentStageResponse(BaseModel):
    node_id: UUID
    version: str
    command_id: Optional[UUID] = None
    state: str


def policy_from_config(config: Optional[Dict[str, Any]]) -> AgentUpdatePolicy:
    """The stored policy (an invalid stored value reads as manual)."""
    raw = (config or {}).get("agent_updates") if isinstance(config, dict) else None
    if not isinstance(raw, dict):
        return AgentUpdatePolicy()
    try:
        return AgentUpdatePolicy.model_validate(raw)
    except ValueError:
        return AgentUpdatePolicy()
