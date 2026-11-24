"""
Fleet schemas: clusters, nodes, join tokens, devices, slices, certificates,
agent commands, cache summary, topology. Shapes follow
``libs/contracts/openapi/admin.yaml`` components of the same names.
"""

from __future__ import annotations

import ipaddress
import re
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ClusterType = Literal[
    "bare_metal",
    "vm",
    "kubernetes",
    "cloud_aws",
    "cloud_azure",
    "cloud_gcp",
    "cloud_do",
]
ClusterStatus = Literal["active", "disabled"]
NodeStatus = Literal["pending", "online", "offline", "unhealthy", "draining", "drained", "removed"]
NodeOs = Literal["linux", "macos"]
NodeArch = Literal["x86_64", "aarch64"]
DeviceVendor = Literal["nvidia", "amd", "hygon", "metax", "moorethreads", "thead", "apple"]
DeviceHealth = Literal["healthy", "degraded", "unhealthy", "unknown"]
CertificateKind = Literal["node_agent", "api_layer", "agent_hub", "control_plane"]
AgentCommandState = Literal[
    "pending",
    "sent",
    "accepted",
    "running",
    "succeeded",
    "failed",
    "rejected",
    "timed_out",
    "cancelled",
]
DeviceSliceStatus = Literal["reserved", "active", "released"]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Response(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ==========================================
# Clusters
# ==========================================


class ClusterCounts(BaseModel):
    nodes: int = 0
    nodes_online: int = 0
    devices: int = 0
    instances: int = 0
    vram_total_bytes: int = 0
    vram_used_bytes: int = 0


_HOSTNAME = re.compile(r"^[A-Za-z0-9_](?:[A-Za-z0-9_.-]{0,251}[A-Za-z0-9_])?$")


def validate_egress_entry(entry: str) -> str:
    """
    ``host``, ``host:port``, ``ip``, ``ip/prefix``, either with ``:port``, or ``[v6]:port``.
    The agent renders these into pf / nftables rules, so nothing else is accepted.
    """
    value = entry.strip()
    host, port = value, None
    if value.startswith("["):
        inner, sep, rest = value[1:].partition("]")
        if not sep or (rest and not rest.startswith(":")):
            raise ValueError(f"invalid egress allow-list entry {entry!r}")
        host, port = inner, (rest[1:] or None)
    elif value.count(":") == 1:
        host, port = value.split(":", 1)
    if port is not None and not (port.isdigit() and 1 <= int(port) <= 65535):
        raise ValueError(f"invalid port in egress allow-list entry {entry!r}")
    try:
        ipaddress.ip_network(host, strict=False)
        if "/" not in host:
            ipaddress.ip_address(host)
        return value
    except ValueError:
        pass
    if "/" in host or not _HOSTNAME.match(host):
        raise ValueError(f"invalid egress allow-list entry {entry!r} (IP, CIDR or host name)")
    return value


def _egress_list(value: Optional[List[str]]) -> Optional[List[str]]:
    if value is None:
        return None
    if len(value) > 256:
        raise ValueError("egress_allowlist: at most 256 entries")
    return [validate_egress_entry(v) for v in value]


class ClusterCreate(_Request):
    name: str = Field(min_length=1, max_length=255)
    type: ClusterType
    description: Optional[str] = Field(None, max_length=2048)
    region: Optional[str] = Field(None, max_length=100)
    credential_id: Optional[UUID] = None
    price_plan_id: Optional[UUID] = None
    config: Dict[str, Any] = Field(default_factory=dict)
    egress_allowlist: List[str] = Field(default_factory=list)
    labels: Dict[str, str] = Field(default_factory=dict)

    @field_validator("egress_allowlist")
    @classmethod
    def _egress(cls, value):
        return _egress_list(value)


class ClusterUpdate(_Request):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    type: Optional[ClusterType] = None
    description: Optional[str] = Field(None, max_length=2048)
    region: Optional[str] = Field(None, max_length=100)
    credential_id: Optional[UUID] = None
    price_plan_id: Optional[UUID] = None
    config: Optional[Dict[str, Any]] = None
    egress_allowlist: Optional[List[str]] = None
    labels: Optional[Dict[str, str]] = None
    status: Optional[ClusterStatus] = None

    @field_validator("egress_allowlist")
    @classmethod
    def _egress(cls, value):
        return _egress_list(value)


class ClusterResponse(_Response):
    id: UUID
    name: str
    type: str
    description: Optional[str] = None
    region: Optional[str] = None
    status: str
    credential_id: Optional[UUID] = None
    price_plan_id: Optional[UUID] = None
    config: Dict[str, Any] = Field(default_factory=dict)
    egress_allowlist: List[str] = Field(default_factory=list)
    labels: Dict[str, str] = Field(default_factory=dict)
    counts: Optional[ClusterCounts] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


# ==========================================
# Nodes and join tokens
# ==========================================


class NodeDisk(BaseModel):
    mount: str
    device: Optional[str] = None
    fs: Optional[str] = None
    total_bytes: int = 0
    free_bytes: int = 0
    is_cache_dir: bool = False


class NodeUpdate(_Request):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    labels: Optional[Dict[str, str]] = None
    price_plan_id: Optional[UUID] = None
    proxy_address: Optional[str] = None
    proxy_port: Optional[int] = Field(None, ge=1, le=65535)
    cache_limit_bytes: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None


class NodeResponse(_Response):
    id: UUID
    cluster_id: UUID
    name: str
    hostname: Optional[str] = None
    ip_address: Optional[str] = None
    proxy_address: Optional[str] = None
    proxy_port: int = 9443
    os: Optional[str] = None
    os_version: Optional[str] = None
    arch: Optional[str] = None
    kernel_version: Optional[str] = None
    agent_version: Optional[str] = None
    protocol_version: Optional[int] = None
    status: str
    status_message: Optional[str] = None
    labels: Dict[str, str] = Field(default_factory=dict)
    capabilities: Dict[str, Any] = Field(default_factory=dict)
    cert_fingerprint: Optional[str] = None
    cpu_cores: Optional[int] = None
    memory_total_bytes: Optional[int] = None
    disks: List[NodeDisk] = Field(default_factory=list)
    cache_dir: Optional[str] = None
    cache_limit_bytes: Optional[int] = None
    cache_used_bytes: Optional[int] = None
    last_heartbeat_at: Optional[datetime] = None
    last_inventory_at: Optional[datetime] = None
    connected_at: Optional[datetime] = None
    disconnected_at: Optional[datetime] = None
    drain_requested_at: Optional[datetime] = None
    drain_reason: Optional[str] = None
    drained_at: Optional[datetime] = None
    price_plan_id: Optional[UUID] = None
    kubernetes_node_name: Optional[str] = None
    cloud_instance_id: Optional[str] = None
    cloud_instance_type: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    device_count: int = 0
    instance_count: int = 0
    activity: Optional[str] = Field(
        None,
        description="detached | stale | gpu_deck | mixed | external | idle, from the live GPU "
        "samples (external = models running outside GPU Deck)",
    )
    outside_processes: int = 0
    detached: bool = Field(
        False,
        description="Agent gone: offline / removed or heartbeat older than twice the "
        "offline threshold",
    )
    created_at: datetime
    updated_at: Optional[datetime] = None


# Single-use tokens live minutes; multi-use enrolment tokens (DaemonSets, node pools) up to
# a year.
SINGLE_USE_MAX_TTL_MINUTES = 1440
MULTI_USE_MAX_TTL_MINUTES = 525600


class JoinTokenCreate(_Request):
    cluster_id: UUID
    node_name_hint: Optional[str] = Field(None, max_length=255)
    labels: Dict[str, str] = Field(default_factory=dict)
    ttl_minutes: int = Field(default=15, ge=1, le=MULTI_USE_MAX_TTL_MINUTES)
    max_uses: int = Field(
        default=1,
        ge=1,
        le=100000,
        description="Machines the token may enrol (1 = one-time token)",
    )

    @model_validator(mode="after")
    def _check_uses(self) -> "JoinTokenCreate":
        if self.max_uses == 1 and self.ttl_minutes > SINGLE_USE_MAX_TTL_MINUTES:
            raise ValueError("a one-time token lives at most 1440 minutes")
        if self.max_uses > 1 and self.node_name_hint:
            raise ValueError("node_name_hint needs a one-time token (max_uses = 1)")
        return self


class JoinTokenResponse(_Response):
    id: UUID
    cluster_id: UUID
    token_prefix: str
    node_name_hint: Optional[str] = None
    labels: Dict[str, str] = Field(default_factory=dict)
    expires_at: datetime
    max_uses: int = 1
    use_count: int = 0
    last_used_at: Optional[datetime] = None
    used_at: Optional[datetime] = None
    used_by_node_id: Optional[UUID] = None
    revoked_at: Optional[datetime] = None
    created_at: datetime


class JoinTokenCreatedResponse(JoinTokenResponse):
    token: str
    join_command: str
    ca_fingerprint_sha256: Optional[str] = None
    ca_pem_sha256: Optional[str] = None
    hub_url: Optional[str] = None
    install_command: Optional[str] = None
    install_script_url: Optional[str] = None


class DrainRequest(_Request):
    reason: Optional[str] = Field(None, max_length=512)
    timeout_seconds: int = Field(default=300, ge=0, le=3600)
    move_instances: bool = True


class CommandRefs(BaseModel):
    command_ids: List[UUID] = Field(default_factory=list)
    swap_operation_ids: List[UUID] = Field(default_factory=list)


class RevokeRequest(_Request):
    reason: Optional[str] = Field(None, max_length=255)


# ==========================================
# Devices and slices
# ==========================================


class DeviceLive(BaseModel):
    ts: Optional[datetime] = None
    utilization_pct: Optional[float] = None
    memory_used_bytes: Optional[int] = None
    power_watts: Optional[float] = None
    temperature_c: Optional[float] = None


class DeviceSliceResponse(_Response):
    id: UUID
    device_id: UUID
    instance_id: Optional[UUID] = None
    deployment_id: Optional[UUID] = None
    gpu_instance_id: Optional[UUID] = None
    name: Optional[str] = None
    vram_bytes: int
    compute_pct: int
    status: str
    created_at: datetime
    released_at: Optional[datetime] = None


class DeviceSliceCreate(BaseModel):
    """
    Direct slice on one device. Without ``deployment_id`` it is a
    standalone reservation (usable by an SSH GPU instance via ``slice_id``);
    with it, one new replica of that sliced deployment is placed on this device.
    """

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(None, max_length=255)
    vram_bytes: Optional[int] = Field(None, ge=1, description="Required unless deployment_id")
    compute_pct: Optional[int] = Field(None, ge=1, le=100)
    deployment_id: Optional[UUID] = None


class DeviceSliceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(None, max_length=255)
    vram_bytes: Optional[int] = Field(None, ge=1)
    compute_pct: Optional[int] = Field(None, ge=1, le=100)


class DeviceSliceResult(BaseModel):
    """Outcome of a slice create / resize / delete."""

    slice: DeviceSliceResponse
    deployment_id: Optional[UUID] = None
    instance_id: Optional[UUID] = None
    gpu_instance_id: Optional[UUID] = None
    rolling_update: bool = False
    command_ids: List[UUID] = Field(default_factory=list)
    message: Optional[str] = None


class DeviceProcess(BaseModel):
    """A process holding memory on the device (NVML; latest sample)."""

    pid: int
    name: Optional[str] = None
    used_memory_bytes: Optional[int] = None
    instance_id: Optional[UUID] = None
    deployment_id: Optional[UUID] = None
    deployment_name: Optional[str] = None
    origin: Optional[str] = Field(
        None,
        description="managed (GPU Deck runs it), external (an observed server) or null "
        "(not GPU Deck's)",
    )


class DeviceResponse(_Response):
    id: UUID
    node_id: UUID
    node_name: Optional[str] = None
    cluster_id: Optional[UUID] = None
    vendor: str
    index: int
    uuid: Optional[str] = None
    name: str
    vram_total_bytes: int = 0
    vram_reserved_bytes: int = 0
    is_unified_memory: bool = False
    driver_version: Optional[str] = None
    runtime_version: Optional[str] = None
    compute_capability: Optional[str] = None
    pci_bus_id: Optional[str] = None
    numa_node: Optional[int] = None
    max_power_watts: Optional[float] = None
    core_count: Optional[int] = None
    health: str = "unknown"
    health_reason: Optional[str] = None
    labels: Dict[str, str] = Field(default_factory=dict)
    extra: Dict[str, Any] = Field(default_factory=dict)
    last_seen_at: Optional[datetime] = None
    live: Optional[DeviceLive] = None
    slices: List[DeviceSliceResponse] = Field(default_factory=list)
    instance_ids: List[UUID] = Field(default_factory=list)
    vram_used_bytes: Optional[int] = Field(
        None, description="Memory in use from the latest fresh sample (null when stale)"
    )
    vram_used_outside_bytes: Optional[int] = Field(
        None,
        description="Part of vram_used_bytes held by processes GPU Deck does not run "
        "(external servers, anything else); null when the per-process list is unknown",
    )
    vram_headroom_bytes: int = 0
    vram_free_bytes: int = Field(
        0,
        description="total - headroom - max(reserved, GPU Deck's use) - used outside GPU Deck: "
        "what placement and new slices can still take",
    )
    vram_usage_stale: bool = Field(
        False,
        description="No fresh sample: vram_free_bytes counts reservations only",
    )
    processes: List[DeviceProcess] = Field(default_factory=list)
    processes_ts: Optional[datetime] = None
    activity: str = Field(
        "idle",
        description="detached | stale | gpu_deck | mixed | external | idle (from live data, "
        "not only GPU Deck instances)",
    )
    outside_processes: int = Field(0, description="Processes holding memory outside GPU Deck")
    node_detached: bool = False
    created_at: datetime
    updated_at: Optional[datetime] = None


# ==========================================
# Instances (read-only view used by node/topology endpoints)
# ==========================================


class InstanceLive(BaseModel):
    ts: Optional[datetime] = None
    active_sessions: Optional[int] = None
    waiting_sessions: Optional[int] = None
    kv_cache_usage_pct: Optional[float] = None
    generation_tps: Optional[float] = None
    ttft_ms_p95: Optional[float] = None
    requests_per_minute: Optional[float] = None
    error_rate_pct: Optional[float] = None


class InstanceResponse(_Response):
    id: UUID
    deployment_id: UUID
    deployment_name: Optional[str] = None
    model_name: Optional[str] = None
    node_id: UUID
    node_name: Optional[str] = None
    device_ids: List[UUID] = Field(default_factory=list)
    port: Optional[int] = None
    served_model_name: Optional[str] = None
    engine_process_id: Optional[UUID] = None
    engine_name: Optional[str] = None
    state: str
    state_message: Optional[str] = None
    health: str
    health_message: Optional[str] = None
    traffic_weight: int = 100
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
    origin: str = "managed"
    created_at: datetime
    updated_at: Optional[datetime] = None


# ==========================================
# Cache summary, commands, certificates
# ==========================================


class CacheByModel(BaseModel):
    model_id: Optional[UUID] = None
    model_name: Optional[str] = None
    model_version_id: UUID
    version: Optional[str] = None
    size_bytes: int = 0
    state: str
    pinned: bool = False
    in_use: bool = False
    last_used_at: Optional[datetime] = None


class CacheSummary(BaseModel):
    node_id: UUID
    node_name: Optional[str] = None
    cache_dir: Optional[str] = None
    limit_bytes: Optional[int] = None
    used_bytes: int = 0
    free_disk_bytes: Optional[int] = None
    pinned_bytes: int = 0
    item_count: int = 0
    by_model: List[CacheByModel] = Field(default_factory=list)


class AgentCommandResponse(_Response):
    id: UUID
    node_id: UUID
    command_type: str
    state: str
    attempts: int = 0
    swap_operation_id: Optional[UUID] = None
    instance_id: Optional[UUID] = None
    progress_pct: Optional[float] = None
    result: Optional[Dict[str, Any]] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    expires_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None
    accepted_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime


class NodeCertificateResponse(_Response):
    id: UUID
    kind: str
    node_id: Optional[UUID] = None
    serial: str
    fingerprint_sha256: str
    subject: str
    not_before: datetime
    not_after: datetime
    issued_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    revocation_reason: Optional[str] = None
    is_active: bool = False


class CaCertificate(BaseModel):
    certificate_pem: str
    fingerprint_sha256: str
    subject: str
    not_after: datetime


class Crl(BaseModel):
    generated_at: datetime
    serials: List[str] = Field(default_factory=list)
    fingerprints_sha256: List[str] = Field(default_factory=list)
    crl_pem: Optional[str] = None


class ServiceCertificateRequest(_Request):
    kind: Literal["api_layer", "control_plane", "agent_hub"]
    common_name: Optional[str] = None
    sans: List[str] = Field(default_factory=list)
    validity_days: int = Field(default=365, ge=1, le=3650)
    csr_pem: Optional[str] = None


class ServiceCertificateIssued(BaseModel):
    certificate: NodeCertificateResponse
    certificate_pem: str
    private_key_pem: Optional[str] = None
    ca_bundle_pem: str


# ==========================================
# Topology
# ==========================================


class TopologyInstance(BaseModel):
    id: UUID
    deployment_id: UUID
    deployment_name: Optional[str] = None
    model_name: Optional[str] = None
    engine: Optional[str] = None
    state: str
    health: str
    device_ids: List[UUID] = Field(default_factory=list)
    traffic_weight: int = 100
    origin: str = "managed"
    active_sessions: Optional[int] = None
    waiting_sessions: Optional[int] = None


class TopologyDevice(BaseModel):
    device: DeviceResponse
    instances: List[TopologyInstance] = Field(default_factory=list)


class TopologyNode(BaseModel):
    node: NodeResponse
    devices: List[TopologyDevice] = Field(default_factory=list)


class TopologyCluster(BaseModel):
    cluster: ClusterResponse
    nodes: List[TopologyNode] = Field(default_factory=list)


class TopologyLink(BaseModel):
    from_instance_id: UUID
    to_instance_id: UUID
    kind: Literal["tensor_parallel", "pipeline_parallel", "pd_transfer", "kv_share"]


class Topology(BaseModel):
    generated_at: datetime
    clusters: List[TopologyCluster] = Field(default_factory=list)
    links: List[TopologyLink] = Field(default_factory=list)


# ---------- node-side file commands ----------


class NodeScanRequest(BaseModel):
    """``scan_path`` on the node's disk (agent >= 1.1.0). ``~`` expands on the node."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=4096)
    recursive: bool = True
    max_depth: int = Field(default=4, ge=1, le=10)
    max_entries: int = Field(default=20000, ge=1, le=200000)
    read_metadata: bool = True
    wait_seconds: float = Field(default=30, ge=0, le=300, description="0 = return at once (202)")


class NodeHashRequest(BaseModel):
    """``hash_files`` on the node's disk (agent >= 1.1.0)."""

    model_config = ConfigDict(extra="forbid")

    root: Optional[str] = Field(None, max_length=4096)
    paths: List[str] = Field(min_length=1, max_length=10000)
    wait_seconds: float = Field(default=30, ge=0, le=300)


class NodeFileCommandResult(BaseModel):
    command_id: UUID
    command: str
    node_id: UUID
    state: str
    progress_pct: Optional[float] = None
    result: Optional[Dict[str, Any]] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
