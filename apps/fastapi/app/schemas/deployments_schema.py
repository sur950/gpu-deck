"""
Pydantic schemas for deployments, instances and placement previews
(admin.yaml tags "Deployments" and "Instances").
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from app.core.logger import get_logger
from app.schemas.catalog_schema import ModelPriority, ModelType
from app.schemas.engines_schema import (
    CompatCheck,
    DeploymentPreset,
    DeviceVendor,
    EngineName,
)
from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = get_logger(__name__)


class DeploymentStatus(str, Enum):
    PENDING = "pending"
    SCHEDULING = "scheduling"
    DEPLOYING = "deploying"
    RUNNING = "running"
    DEGRADED = "degraded"
    SCALING = "scaling"
    STOPPED = "stopped"
    FAILED = "failed"
    DELETING = "deleting"


class InstanceState(str, Enum):
    PENDING = "pending"
    LOADING = "loading"
    READY = "ready"
    DRAINING = "draining"
    UNLOADING = "unloading"
    STOPPED = "stopped"
    ERROR = "error"


class InstanceHealth(str, Enum):
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class PlacementStrategy(str, Enum):
    SPREAD = "spread"
    PACK = "pack"


class TrafficStrategy(str, Enum):
    ROUND_ROBIN = "round_robin"
    LEAST_SESSIONS = "least_sessions"
    WEIGHTED = "weighted"


class DeploymentSpec(BaseModel):
    """Shared by create / output (admin.yaml DeploymentSpec)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=2048)
    model_id: uuid.UUID
    model_version_id: Optional[uuid.UUID] = None
    engine_version_id: Optional[uuid.UUID] = None
    cluster_id: Optional[uuid.UUID] = None
    priority_override: Optional[ModelPriority] = None
    replicas: int = Field(1, ge=0)
    min_replicas: int = Field(0, ge=0)
    max_replicas: int = Field(1, ge=1)
    gpu_count: int = Field(1, ge=1)
    slice_vram_bytes: Optional[int] = Field(None, ge=1)
    slice_compute_pct: Optional[int] = Field(None, ge=1, le=100)
    tp_size: int = Field(1, ge=1)
    pp_size: int = Field(1, ge=1)
    preset: Optional[DeploymentPreset] = None
    pd_split: bool = False
    pd_config: Dict[str, Any] = Field(default_factory=dict)
    spec_decoding: Dict[str, Any] = Field(default_factory=dict)
    kv_sharing: bool = False
    kv_sharing_config: Dict[str, Any] = Field(default_factory=dict)
    launch_flags: Dict[str, Any] = Field(default_factory=dict)
    env_vars: Dict[str, str] = Field(default_factory=dict)
    tool_parser: Optional[str] = Field(None, max_length=100)
    context_size: Optional[int] = Field(None, ge=1)
    control_vectors: List[Dict[str, Any]] = Field(default_factory=list)
    placement_strategy: PlacementStrategy = PlacementStrategy.SPREAD
    node_selector: Dict[str, Any] = Field(default_factory=dict)
    device_vendor_filter: List[DeviceVendor] = Field(default_factory=list)
    traffic_config: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _replica_bounds(self) -> "DeploymentSpec":
        if "max_replicas" not in self.model_fields_set and self.replicas > self.max_replicas:
            self.max_replicas = max(self.replicas, 1)
        if self.min_replicas > self.replicas:
            raise ValueError("min_replicas must be <= replicas")
        if self.replicas > self.max_replicas:
            raise ValueError("replicas must be <= max_replicas")
        if self.slice_compute_pct is not None and self.slice_vram_bytes is None:
            raise ValueError("slice_compute_pct needs slice_vram_bytes")
        return self


class DeploymentCreate(DeploymentSpec):
    start: bool = True
    dry_run: bool = False


class DeploymentUpdate(BaseModel):
    """Partial update; omitted fields stay as they are."""

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=2048)
    model_version_id: Optional[uuid.UUID] = None
    engine_version_id: Optional[uuid.UUID] = None
    cluster_id: Optional[uuid.UUID] = None
    priority_override: Optional[ModelPriority] = None
    replicas: Optional[int] = Field(None, ge=0)
    min_replicas: Optional[int] = Field(None, ge=0)
    max_replicas: Optional[int] = Field(None, ge=1)
    gpu_count: Optional[int] = Field(None, ge=1)
    slice_vram_bytes: Optional[int] = Field(None, ge=1)
    slice_compute_pct: Optional[int] = Field(None, ge=1, le=100)
    tp_size: Optional[int] = Field(None, ge=1)
    pp_size: Optional[int] = Field(None, ge=1)
    preset: Optional[DeploymentPreset] = None
    pd_split: Optional[bool] = None
    pd_config: Optional[Dict[str, Any]] = None
    spec_decoding: Optional[Dict[str, Any]] = None
    kv_sharing: Optional[bool] = None
    kv_sharing_config: Optional[Dict[str, Any]] = None
    launch_flags: Optional[Dict[str, Any]] = None
    env_vars: Optional[Dict[str, str]] = None
    tool_parser: Optional[str] = Field(None, max_length=100)
    context_size: Optional[int] = Field(None, ge=1)
    control_vectors: Optional[List[Dict[str, Any]]] = None
    placement_strategy: Optional[PlacementStrategy] = None
    node_selector: Optional[Dict[str, Any]] = None
    device_vendor_filter: Optional[List[DeviceVendor]] = None
    traffic_config: Optional[Dict[str, Any]] = None


class DeploymentOrigin(str, Enum):
    """managed = GPU Deck runs it; external = an observed server GPU Deck did not start."""

    MANAGED = "managed"
    EXTERNAL = "external"


class ExternalEndpointStat(BaseModel):
    method: str
    path: str
    count: int


class ExternalInfo(BaseModel):
    """What the node agent reports about an external (observed) server (read-only)."""

    node_id: Optional[uuid.UUID] = None
    node_name: Optional[str] = None
    name: str
    engine: str
    engine_label: Optional[str] = None
    endpoint: Optional[str] = None
    models: List[str] = Field(default_factory=list)
    version: Optional[str] = None
    sources: List[str] = Field(default_factory=list)
    pid: Optional[int] = None
    device_ids: List[uuid.UUID] = Field(default_factory=list)
    health: Optional[InstanceHealth] = None
    message: Optional[str] = None
    last_ok_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
    first_seen_at: Optional[datetime] = None
    stale_since: Optional[datetime] = None
    requests_per_minute: Optional[float] = None
    status_counts: Dict[str, int] = Field(default_factory=dict)
    top_endpoints: List[ExternalEndpointStat] = Field(default_factory=list)
    process_vram_bytes: Optional[int] = None
    log_derived: bool = False


class InstanceLive(BaseModel):
    ts: Optional[datetime] = None
    active_sessions: Optional[int] = None
    waiting_sessions: Optional[int] = None
    kv_cache_usage_pct: Optional[float] = None
    generation_tps: Optional[float] = None
    ttft_ms_p95: Optional[float] = None
    requests_per_minute: Optional[float] = None
    error_rate_pct: Optional[float] = None


class Instance(BaseModel):
    id: uuid.UUID
    deployment_id: uuid.UUID
    deployment_name: Optional[str] = None
    model_name: Optional[str] = None
    node_id: uuid.UUID
    node_name: Optional[str] = None
    device_ids: List[uuid.UUID] = Field(default_factory=list)
    port: Optional[int] = None
    served_model_name: Optional[str] = None
    engine_process_id: Optional[uuid.UUID] = None
    engine_name: Optional[EngineName] = None
    state: InstanceState
    state_message: Optional[str] = None
    health: InstanceHealth
    health_message: Optional[str] = None
    traffic_weight: int
    pd_role: Optional[str] = None
    pid: Optional[int] = None
    container_id: Optional[str] = None
    resources: Dict[str, Any] = Field(default_factory=dict)
    restart_count: int = 0
    state_changed_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    ready_at: Optional[datetime] = None
    stopped_at: Optional[datetime] = None
    last_health_at: Optional[datetime] = None
    last_request_at: Optional[datetime] = None
    live: Optional[InstanceLive] = None
    origin: DeploymentOrigin = DeploymentOrigin.MANAGED
    created_at: datetime
    updated_at: Optional[datetime] = None


class Deployment(BaseModel):
    """Deployment with live state (admin.yaml Deployment)."""

    id: uuid.UUID
    name: str
    description: Optional[str] = None
    model_id: Optional[uuid.UUID] = None
    model_version_id: Optional[uuid.UUID] = None
    engine_version_id: Optional[uuid.UUID] = None
    cluster_id: Optional[uuid.UUID] = None
    priority_override: Optional[ModelPriority] = None
    replicas: int
    min_replicas: int
    max_replicas: int
    gpu_count: int
    slice_vram_bytes: Optional[int] = None
    slice_compute_pct: Optional[int] = None
    tp_size: int
    pp_size: int
    preset: Optional[DeploymentPreset] = None
    pd_split: bool
    pd_config: Dict[str, Any] = Field(default_factory=dict)
    spec_decoding: Dict[str, Any] = Field(default_factory=dict)
    kv_sharing: bool
    kv_sharing_config: Dict[str, Any] = Field(default_factory=dict)
    launch_flags: Dict[str, Any] = Field(default_factory=dict)
    env_vars: Dict[str, str] = Field(default_factory=dict)
    tool_parser: Optional[str] = None
    context_size: Optional[int] = None
    control_vectors: List[Dict[str, Any]] = Field(default_factory=list)
    placement_strategy: PlacementStrategy
    node_selector: Dict[str, Any] = Field(default_factory=dict)
    device_vendor_filter: List[DeviceVendor] = Field(default_factory=list)
    traffic_config: Dict[str, Any] = Field(default_factory=dict)
    model_name: Optional[str] = None
    model_type: Optional[ModelType] = None
    effective_priority: Optional[ModelPriority] = None
    engine_name: Optional[EngineName] = None
    engine_version: Optional[str] = None
    status: DeploymentStatus
    status_message: Optional[str] = None
    desired_running: bool
    last_request_at: Optional[datetime] = None
    instances: List[Instance] = Field(default_factory=list)
    ready_replicas: int = 0
    active_swap_ids: List[uuid.UUID] = Field(default_factory=list)
    live: Optional[InstanceLive] = None
    origin: DeploymentOrigin = DeploymentOrigin.MANAGED
    external: Optional[ExternalInfo] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class ScaleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    replicas: int = Field(..., ge=0, le=64)
    placement_strategy: Optional[PlacementStrategy] = None
    drain_timeout_seconds: int = Field(120, ge=0)


class TrafficWeightsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weights: Dict[uuid.UUID, int]
    strategy: Optional[TrafficStrategy] = None

    @model_validator(mode="after")
    def _ranges(self) -> "TrafficWeightsRequest":
        for iid, w in self.weights.items():
            if w < 0 or w > 1000:
                raise ValueError(f"weight for {iid} must be between 0 and 1000")
        return self


class PlacementPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deployment: Optional[DeploymentSpec] = None
    deployment_id: Optional[uuid.UUID] = None
    replicas: Optional[int] = Field(None, ge=0, le=64)

    @model_validator(mode="after")
    def _one_target(self) -> "PlacementPreviewRequest":
        if (self.deployment is None) == (self.deployment_id is None):
            raise ValueError("exactly one of deployment or deployment_id is required")
        return self


class SliceSpec(BaseModel):
    vram_bytes: int
    compute_pct: int


class PlacementCandidate(BaseModel):
    node_id: uuid.UUID
    node_name: Optional[str] = None
    device_ids: List[uuid.UUID]
    device_names: List[str] = Field(default_factory=list)
    slice: Optional[SliceSpec] = None
    engine_version_id: uuid.UUID
    score: float
    estimated_vram_bytes: Optional[int] = None
    free_vram_bytes_after: Optional[int] = None
    reasons: List[str] = Field(default_factory=list)


class PlacementRejection(BaseModel):
    node_id: uuid.UUID
    device_ids: List[uuid.UUID] = Field(default_factory=list)
    reason: str


class PlacementPreview(BaseModel):
    feasible: bool
    placements: List[PlacementCandidate] = Field(default_factory=list)
    rejected: List[PlacementRejection] = Field(default_factory=list)
    would_evict: List[Instance] = Field(default_factory=list)
    compat: Optional[CompatCheck] = None


class InstanceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    traffic_weight: Optional[int] = Field(None, ge=0, le=1000)


class DrainThen(str, Enum):
    NONE = "none"
    UNLOAD = "unload"
    RESTART = "restart"


class InstanceDrainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeout_seconds: int = Field(120, ge=0, le=3600)
    then: DrainThen = DrainThen.NONE
