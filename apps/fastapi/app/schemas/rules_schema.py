"""
Pydantic schemas for the rules API (mirror of admin.yaml: RuleCreate, RuleUpdate,
Rule, RuleRun, RuleRunAction, RuleRunSummary, RuleSchemas).

``params`` and ``target`` are opaque objects here; they are validated against
libs/contracts/rules/*.schema.json by app/services/rules/validation.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from app.core.logger import get_logger
from pydantic import BaseModel, ConfigDict, Field

logger = get_logger(__name__)


class RuleType(str, Enum):
    IDLE_UNLOAD = "idle_unload"
    TIME_SCHEDULE = "time_schedule"
    LOW_TRAFFIC_SHRINK = "low_traffic_shrink"
    HIGH_LOAD_GROW = "high_load_grow"
    MAKE_ROOM_BY_PRIORITY = "make_room_by_priority"
    AUTO_HEAL = "auto_heal"


class RuleRunOutcome(str, Enum):
    NO_MATCH = "no_match"
    COOLDOWN = "cooldown"
    FIRED = "fired"
    DRY_RUN = "dry_run"
    SKIPPED = "skipped"
    ERROR = "error"


class RuleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=2048)
    type: RuleType
    params: Dict[str, Any]
    target: Dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    dry_run: bool = False
    cooldown_seconds: int = Field(default=300, ge=0, le=86400)
    evaluation_interval_seconds: int = Field(default=30, ge=5, le=3600)
    priority: int = Field(default=100, ge=0, le=1000)


class RuleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=2048)
    params: Optional[Dict[str, Any]] = None
    target: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None
    dry_run: Optional[bool] = None
    cooldown_seconds: Optional[int] = Field(default=None, ge=0, le=86400)
    evaluation_interval_seconds: Optional[int] = Field(default=None, ge=5, le=3600)
    priority: Optional[int] = Field(default=None, ge=0, le=1000)


class RuleRunSummary(BaseModel):
    id: uuid.UUID
    evaluated_at: datetime
    outcome: RuleRunOutcome
    fired: bool
    dry_run: bool


class RuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: Optional[str] = None
    type: RuleType
    params: Dict[str, Any]
    target: Dict[str, Any]
    enabled: bool
    dry_run: bool
    cooldown_seconds: int
    evaluation_interval_seconds: int
    priority: int
    last_evaluated_at: Optional[datetime] = None
    last_fired_at: Optional[datetime] = None
    fire_count: int = 0
    # Computed on read (not a column): next cron fire for time_schedule, else
    # last_evaluated_at + evaluation_interval_seconds; null while disabled.
    next_evaluation_at: Optional[datetime] = None
    last_run: Optional[RuleRunSummary] = None
    created_at: datetime
    updated_at: datetime


class RuleRunAction(BaseModel):
    action: str
    target_type: str
    target_id: Optional[uuid.UUID] = None
    target_name: Optional[str] = None
    swap_operation_id: Optional[uuid.UUID] = None
    status: str = "planned"
    message: Optional[str] = None


class RuleRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    rule_id: uuid.UUID
    rule_name: Optional[str] = None
    rule_type: RuleType
    evaluated_at: datetime
    fired: bool
    dry_run: bool
    outcome: RuleRunOutcome
    decision: Dict[str, Any]
    actions: List[RuleRunAction]
    error_message: Optional[str] = None
    duration_ms: Optional[int] = None
    created_at: datetime


class RuleSchemasOut(BaseModel):
    rule: Dict[str, Any]
    target_selector: Dict[str, Any]
    layout: Dict[str, Any]
    params: Dict[str, Dict[str, Any]]
