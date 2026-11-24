"""
Cloud schemas: providers, regions, GPU instance types, quotas and
provisioned cloud nodes. Shapes follow the ``Cloud`` tag in
``libs/contracts/openapi/admin.yaml``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

CloudProviderName = Literal["aws", "azure", "gcp", "digitalocean"]
CloudNodeStatus = Literal[
    "pending",
    "provisioning",
    "booting",
    "running",
    "draining",
    "stopping",
    "stopped",
    "starting",
    "terminating",
    "terminated",
    "error",
]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Response(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ==========================================
# Catalog
# ==========================================


class CloudProviderInfo(BaseModel):
    id: CloudProviderName
    name: str
    cluster_type: str
    credential_type: str
    credential_fields: List[str]
    sdk: str
    enabled: bool
    pricing: str
    launch_options: List[str]
    verified_live: bool = False
    notes: Optional[str] = None


class CloudRegionResponse(BaseModel):
    id: str
    name: str
    zones: List[str] = Field(default_factory=list)
    available: bool = True


class CloudInstanceTypeResponse(BaseModel):
    id: str
    gpu_model: Optional[str] = None
    gpu_vendor: Optional[str] = None
    gpu_count: int = 0
    gpu_vram_bytes: Optional[int] = None
    total_vram_bytes: Optional[int] = None
    vcpus: Optional[int] = None
    memory_bytes: Optional[int] = None
    price_per_hour_usd: Optional[float] = None
    price_source: Optional[str] = None
    regions: List[str] = Field(default_factory=list)
    family: Optional[str] = None
    description: Optional[str] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


class CloudQuotaResponse(BaseModel):
    name: str
    used: Optional[float] = None
    limit: Optional[float] = None
    unit: str = "count"
    region: Optional[str] = None


class CloudCredentialCheckRequest(_Request):
    credential_id: UUID


class CloudCredentialCheckResponse(BaseModel):
    ok: bool
    provider: CloudProviderName
    account: Optional[str] = None
    detail: Optional[str] = None


# ==========================================
# Provisioned nodes
# ==========================================


class CloudNodeCreate(_Request):
    cluster_id: UUID
    instance_type: str = Field(min_length=1, max_length=100)
    name: Optional[str] = Field(
        None,
        max_length=200,
        description="VM / node name; generated when absent (count > 1 adds -N)",
    )
    region: Optional[str] = Field(None, max_length=100, description="Default: the cluster's region")
    zone: Optional[str] = Field(None, max_length=100)
    image: Optional[str] = Field(
        None,
        max_length=1024,
        description="Provider image; default is the provider's GPU image",
    )
    disk_gb: Optional[int] = Field(None, ge=20, le=16384)
    count: int = Field(default=1, ge=1, le=10)
    labels: Dict[str, str] = Field(default_factory=dict)
    auto_terminate_idle_minutes: Optional[int] = Field(
        None,
        ge=5,
        le=43200,
        description="Terminate (after drain) when idle this long; default from the cluster config",
    )
    options: Dict[str, Any] = Field(
        default_factory=dict,
        description="Provider launch options merged over clusters.config.cloud (subnet, ...)",
    )
    apply_price_plan: bool = Field(
        default=False,
        description="Create/attach a price plan from the provider list price once the node joins",
    )
    dry_run: bool = Field(default=False, description="Validate and price without creating")


class CloudNodeUpdate(_Request):
    auto_terminate_idle_minutes: Optional[int] = Field(None, ge=5, le=43200)
    labels: Optional[Dict[str, str]] = None


class CloudNodeActionRequest(_Request):
    force: bool = Field(default=False, description="Skip the node drain")
    reason: Optional[str] = Field(None, max_length=512)
    drain_timeout_seconds: Optional[int] = Field(None, ge=0, le=3600)


class PricePlanSuggestion(BaseModel):
    name: str
    usd_per_hour: Optional[float] = None
    usd_per_gpu_minute: Optional[float] = None
    source: Optional[str] = None


class CloudNodeResponse(_Response):
    id: UUID
    cluster_id: UUID
    cluster_name: Optional[str] = None
    node_id: Optional[UUID] = None
    node_name: Optional[str] = None
    node_status: Optional[str] = None
    credential_id: Optional[UUID] = None
    join_token_id: Optional[UUID] = None
    provider: CloudProviderName
    name: str
    region: str
    zone: Optional[str] = None
    instance_type: str
    image: Optional[str] = None
    disk_gb: int
    provider_instance_id: Optional[str] = None
    provider_state: Optional[str] = None
    status: CloudNodeStatus
    status_message: Optional[str] = None
    pending_action: Optional[str] = None
    public_ip: Optional[str] = None
    private_ip: Optional[str] = None
    gpu_model: Optional[str] = None
    gpu_count: int = 0
    gpu_vram_bytes: Optional[int] = None
    vcpus: Optional[int] = None
    memory_bytes: Optional[int] = None
    price_per_hour_usd: Optional[float] = None
    price_source: Optional[str] = None
    price_plan_suggestion: Optional[PricePlanSuggestion] = None
    auto_terminate_idle_minutes: Optional[int] = None
    idle_since: Optional[datetime] = None
    labels: Dict[str, str] = Field(default_factory=dict)
    tags: Dict[str, str] = Field(default_factory=dict)
    options: Dict[str, Any] = Field(default_factory=dict)
    last_error: Optional[Dict[str, Any]] = None
    created_by: Optional[UUID] = None
    launched_at: Optional[datetime] = None
    joined_at: Optional[datetime] = None
    drain_started_at: Optional[datetime] = None
    stopped_at: Optional[datetime] = None
    terminated_at: Optional[datetime] = None
    last_polled_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class CloudProvisionPreview(BaseModel):
    cluster_id: UUID
    provider: CloudProviderName
    region: str
    zone: Optional[str] = None
    instance_type: CloudInstanceTypeResponse
    count: int
    names: List[str]
    image: Optional[str] = None
    disk_gb: int
    options: Dict[str, Any] = Field(default_factory=dict)
    auto_terminate_idle_minutes: Optional[int] = None
    max_nodes: int
    current_nodes: int
    price_per_hour_usd: Optional[float] = None
    estimated_monthly_usd: Optional[float] = None
    price_plan_suggestion: Optional[PricePlanSuggestion] = None
