"""
Pydantic schemas for GPU layouts (mirror of admin.yaml: LayoutItemInput,
LayoutCreate, LayoutUpdate, Layout, LayoutDiff, LayoutApplyRequest,
LayoutApplyResult, LayoutCaptureRequest).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from app.core.logger import get_logger
from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = get_logger(__name__)


class UnlistedPolicy(str, Enum):
    KEEP = "keep"
    UNLOAD = "unload"


class SliceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vram_bytes: int = Field(ge=1)
    compute_pct: int = Field(default=100, ge=1, le=100)


class LayoutItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deployment_id: uuid.UUID
    node_id: Optional[uuid.UUID] = None
    device_ids: List[uuid.UUID] = Field(default_factory=list)
    replicas: int = Field(default=1, ge=0, le=64)
    slice: Optional[SliceInput] = None
    traffic_weight: int = Field(default=100, ge=0, le=1000)
    position: Optional[int] = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _devices_need_node(self) -> "LayoutItemInput":
        if self.device_ids and self.node_id is None:
            raise ValueError("device_ids requires node_id")
        if len(set(self.device_ids)) != len(self.device_ids):
            raise ValueError("device_ids must be unique")
        return self


class LayoutCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=2048)
    unlisted_policy: UnlistedPolicy = UnlistedPolicy.KEEP
    items: List[LayoutItemInput] = Field(min_length=1)


class LayoutUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=2048)
    unlisted_policy: Optional[UnlistedPolicy] = None
    items: Optional[List[LayoutItemInput]] = Field(default=None, min_length=1)


class LayoutCaptureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=2048)
    cluster_id: Optional[uuid.UUID] = None


class LayoutItemOut(BaseModel):
    id: uuid.UUID
    position: int
    deployment_id: uuid.UUID
    deployment_name: Optional[str] = None
    node_id: Optional[uuid.UUID] = None
    node_name: Optional[str] = None
    device_ids: List[uuid.UUID] = Field(default_factory=list)
    replicas: int
    slice_vram_bytes: Optional[int] = None
    slice_compute_pct: Optional[int] = None
    traffic_weight: int


class LayoutOut(BaseModel):
    id: uuid.UUID
    name: str
    description: Optional[str] = None
    unlisted_policy: UnlistedPolicy = UnlistedPolicy.KEEP
    is_active: bool
    applied_at: Optional[datetime] = None
    items: List[LayoutItemOut]
    used_by_rule_ids: List[uuid.UUID] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class LayoutDiffAction(str, Enum):
    LOAD = "load"
    UNLOAD = "unload"
    MOVE = "move"
    SCALE = "scale"
    REWEIGHT = "reweight"
    NOOP = "noop"


class LayoutDiffEntry(BaseModel):
    deployment_id: uuid.UUID
    deployment_name: Optional[str] = None
    action: LayoutDiffAction
    from_: Optional[Dict[str, Any]] = Field(default=None, alias="from")
    to: Optional[Dict[str, Any]] = None
    placement: Optional[Dict[str, Any]] = None
    needs_prefetch_bytes: int = 0
    protected: bool = False

    model_config = ConfigDict(populate_by_name=True)


class LayoutDiff(BaseModel):
    layout_id: uuid.UUID
    feasible: bool
    entries: List[LayoutDiffEntry]
    estimated_seconds: Optional[int] = None
    warnings: List[str] = Field(default_factory=list)


class LayoutApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    drain_timeout_seconds: int = Field(default=300, ge=0, le=3600)
    prefetch_only: bool = False


class LayoutApplyResult(BaseModel):
    layout_id: uuid.UUID
    swap_operation_ids: List[uuid.UUID]
    command_ids: List[uuid.UUID] = Field(default_factory=list)
    diff: LayoutDiff
