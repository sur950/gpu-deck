"""
SSH GPU instance schemas. Shapes follow ``libs/contracts/openapi/admin.yaml``
components ``GpuInstance*`` (GPU Instances tag).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

GpuInstanceState = Literal[
    "requested", "placing", "starting", "running", "stopping", "stopped", "error"
]
DeviceVendor = Literal["nvidia", "amd", "hygon", "metax", "moorethreads", "thead", "apple"]
_NAME = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_SSH_USER = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
# Variables the agent sets on SSH instance containers itself: keys-only login (no password,
# no sudo), the user and its keys, GPU visibility and the slice limits. A request may not
# override them (the agent drops them too).
RESERVED_ENV_KEYS = frozenset(
    {
        "PASSWORD_ACCESS",
        "USER_PASSWORD",
        "USER_PASSWORD_FILE",
        "SUDO_ACCESS",
        "USER_NAME",
        "PUBLIC_KEY",
        "PUBLIC_KEY_FILE",
        "PUBLIC_KEY_DIR",
        "PUBLIC_KEY_URL",
        "NVIDIA_DRIVER_CAPABILITIES",
    }
)
RESERVED_ENV_PREFIXES = ("GPUDECK_", "CUDA_MPS_")
RESERVED_ENV_SUFFIXES = ("_VISIBLE_DEVICES",)


def is_reserved_env_key(key: str) -> bool:
    upper = key.upper()
    return (
        upper in RESERVED_ENV_KEYS
        or upper.startswith(RESERVED_ENV_PREFIXES)
        or upper.endswith(RESERVED_ENV_SUFFIXES)
    )


class GpuInstanceLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cpu_cores: Optional[float] = Field(None, gt=0, le=1024)
    memory_bytes: Optional[int] = Field(None, ge=4 * 1024 * 1024)
    shm_bytes: Optional[int] = Field(None, ge=1024 * 1024)
    pids: Optional[int] = Field(None, ge=16)


class GpuInstanceVolume(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["volume", "bind", "tmpfs"] = "volume"
    source: Optional[str] = Field(None, max_length=4096)
    target: str = Field(pattern=r"^/", max_length=4096)
    read_only: bool = False
    keep: bool = False
    size_bytes: Optional[int] = Field(None, ge=1024 * 1024)


class GpuInstanceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=63)
    description: Optional[str] = Field(None, max_length=2048)
    image: Optional[str] = Field(None, max_length=512, description="Default: server setting")
    cluster_id: Optional[UUID] = None
    gpu_count: int = Field(1, ge=0, le=64, description="Whole GPUs; 0 = CPU-only container")
    slice_vram_bytes: Optional[int] = Field(None, gt=0, description="Request one GPU slice")
    slice_compute_pct: Optional[int] = Field(None, ge=1, le=100)
    slice_id: Optional[UUID] = Field(None, description="Use an existing direct device slice")
    device_vendor: Optional[DeviceVendor] = None
    node_selector: Dict[str, Any] = Field(
        default_factory=dict, description="Labels plus node_ids / device_ids (pinning)"
    )
    authorized_keys: List[str] = Field(min_length=1, max_length=64)
    ssh_user: str = Field("gpudeck")
    container_port: Optional[int] = Field(None, ge=1, le=65535)
    ssh_port: Optional[int] = Field(None, ge=1, le=65535, description="Requested host port")
    allowed_source_cidrs: List[str] = Field(default_factory=list, max_length=64)
    limits: GpuInstanceLimits = Field(default_factory=GpuInstanceLimits)
    env: Dict[str, str] = Field(default_factory=dict)
    volumes: List[GpuInstanceVolume] = Field(default_factory=list, max_length=16)
    labels: Dict[str, str] = Field(default_factory=dict)
    ttl_seconds: Optional[int] = Field(None, ge=60, description="Auto-stop after; 0/None = default")
    idle_timeout_seconds: Optional[int] = Field(None, ge=60)
    wait_for_capacity_seconds: int = Field(
        0, ge=0, le=86400, description="Queue the request this long when nothing fits"
    )

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not _NAME.match(value):
            raise ValueError("name must be lowercase letters, digits and '-' (DNS label)")
        return value

    @field_validator("ssh_user")
    @classmethod
    def _user(cls, value: str) -> str:
        if not _SSH_USER.match(value) or value == "root":
            raise ValueError("ssh_user must match ^[a-z_][a-z0-9_-]{0,31}$ and not be root")
        return value

    @field_validator("env")
    @classmethod
    def _env(cls, value: Dict[str, str]) -> Dict[str, str]:
        for key in value:
            if not _ENV_KEY.match(key):
                raise ValueError(f"invalid environment variable name {key!r}")
            if is_reserved_env_key(key):
                raise ValueError(
                    f"environment variable {key!r} is managed by GPU Deck (keys-only login, "
                    "GPU visibility and slice limits) and cannot be set"
                )
        return value

    @field_validator("allowed_source_cidrs")
    @classmethod
    def _cidrs(cls, value: List[str]) -> List[str]:
        import ipaddress

        out = []
        for item in value:
            try:
                out.append(str(ipaddress.ip_network(item.strip(), strict=False)))
            except ValueError as exc:
                raise ValueError(f"invalid CIDR {item!r}") from exc
        return out

    @model_validator(mode="after")
    def _shape(self) -> "GpuInstanceCreate":
        if self.slice_compute_pct is not None and self.slice_vram_bytes is None:
            raise ValueError("slice_compute_pct needs slice_vram_bytes")
        if (self.slice_vram_bytes or self.slice_id) and self.gpu_count != 1:
            raise ValueError("a GPU slice is single-device: gpu_count must be 1")
        if self.slice_vram_bytes and self.slice_id:
            raise ValueError("give either slice_vram_bytes or slice_id, not both")
        return self


class GpuInstanceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: Optional[str] = Field(None, max_length=2048)
    labels: Optional[Dict[str, str]] = None
    ttl_seconds: Optional[int] = Field(
        None, ge=0, description="New TTL counted from now; 0 removes the TTL"
    )
    idle_timeout_seconds: Optional[int] = Field(None, ge=0, description="0 disables")
    authorized_keys: Optional[List[str]] = Field(
        None,
        min_length=1,
        max_length=64,
        description="Only while stopped (used on next start)",
    )


class GpuInstanceStopRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force: bool = False
    remove_volumes: bool = True


class GpuInstanceKey(BaseModel):
    type: str
    fingerprint: str
    comment: Optional[str] = None


class GpuInstanceConnection(BaseModel):
    host: Optional[str] = None
    port: Optional[int] = None
    user: str
    host_key_fingerprint: Optional[str] = None
    host_keys: List[Dict[str, Any]] = Field(default_factory=list)
    ssh_command: Optional[str] = None
    known_hosts: List[str] = Field(default_factory=list)
    ready: bool = False


class GpuInstance(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: Optional[str] = None
    state: GpuInstanceState
    state_message: Optional[str] = None
    health: str = "unknown"
    image: str
    cluster_id: Optional[UUID] = None
    node_id: Optional[UUID] = None
    node_name: Optional[str] = None
    gpu_count: int
    slice_vram_bytes: Optional[int] = None
    slice_compute_pct: Optional[int] = None
    device_vendor: Optional[str] = None
    node_selector: Dict[str, Any] = Field(default_factory=dict)
    device_ids: List[UUID] = Field(default_factory=list)
    limits: Dict[str, Any] = Field(default_factory=dict)
    authorized_keys: List[GpuInstanceKey] = Field(default_factory=list)
    ssh_user: str
    container_port: int
    allowed_source_cidrs: List[str] = Field(default_factory=list)
    env: Dict[str, str] = Field(default_factory=dict)
    volumes: List[Dict[str, Any]] = Field(default_factory=list)
    labels: Dict[str, str] = Field(default_factory=dict)
    ttl_seconds: Optional[int] = None
    idle_timeout_seconds: Optional[int] = None
    expires_at: Optional[datetime] = None
    connection: GpuInstanceConnection
    gpu_attached: bool = False
    warnings: List[str] = Field(default_factory=list)
    container_id: Optional[str] = None
    active_connections: int = 0
    connections_total: int = 0
    last_connection_at: Optional[datetime] = None
    stop_reason: Optional[str] = None
    start_command_id: Optional[UUID] = None
    stop_command_id: Optional[UUID] = None
    gpu_seconds_total: float = 0.0
    state_changed_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    running_at: Optional[datetime] = None
    stopped_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
