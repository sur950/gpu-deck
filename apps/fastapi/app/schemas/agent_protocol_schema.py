"""
Agent protocol models — pydantic v2 mirrors of
``libs/contracts/agent-protocol/*.json`` for the messages the hub consumes
and produces. The JSON Schemas remain the contract: inbound frames are also
validated against them (``app/services/agent_hub/protocol.py``) when
``HUB_VALIDATE_SCHEMAS`` is on; these classes give typed access afterwards.

Top-level envelope fields are strict (``extra="forbid"``) as the contract
requires; payload sub-objects that the contract marks open (``extra``,
``details``, command ``args``) stay dicts.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

PROTOCOL_VERSION = 1

MessageType = Literal[
    "hello",
    "hello_ack",
    "inventory",
    "metrics_batch",
    "event",
    "command",
    "command_result",
    "config_push",
    "heartbeat",
]
CommandName = Literal[
    "load_model",
    "unload_model",
    "drain_instance",
    "start_engine",
    "stop_engine",
    "restart_instance",
    "prefetch_model",
    "evict_cache",
    "run_benchmark",
    "update_config",
    "rotate_cert",
    "install_cert",
    "cancel",
    "scan_path",
    "hash_files",
    "start_ssh_instance",
    "stop_ssh_instance",
]
CommandResultStatus = Literal["accepted", "running", "succeeded", "failed", "rejected", "cancelled"]
EventKind = Literal[
    "agent_started",
    "agent_stopping",
    "engine_started",
    "engine_stopped",
    "engine_crashed",
    "instance_state_changed",
    "instance_health_changed",
    "device_health_changed",
    "device_error",
    "cache_changed",
    "cache_pressure",
    "drain_progress",
    "drain_completed",
    "benchmark_progress",
    "config_applied",
    "config_rejected",
    "cert_rotated",
    "cert_expiring",
    "egress_blocked",
    "ssh_instance_state_changed",
    "log",
]


class Envelope(BaseModel):
    """One WebSocket text frame."""

    model_config = ConfigDict(extra="forbid")

    v: int = Field(default=PROTOCOL_VERSION)
    id: uuid.UUID
    type: MessageType
    ts: datetime
    node_id: Optional[uuid.UUID] = None
    correlation_id: Optional[uuid.UUID] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


# ---------- agent -> hub payloads ----------


class HelloEngine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    versions: List[str] = Field(default_factory=list)
    runtime: Optional[str] = None
    path: Optional[str] = None


class HelloCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engines: List[HelloEngine] = Field(default_factory=list)
    runtimes: List[str] = Field(default_factory=list)
    container_runtime: Optional[str] = None
    glibc_version: Optional[str] = None
    cuda_version: Optional[str] = None
    rocm_version: Optional[str] = None
    musa_version: Optional[str] = None
    metal: bool = False
    egress_lock: str = "none"
    kubernetes: Optional[Dict[str, Any]] = None
    cloud: Optional[Dict[str, Any]] = None


class HelloCache(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dir: str
    limit_bytes: int = 0
    used_bytes: int = 0
    free_bytes: Optional[int] = None


class HelloProxy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    port: int = 9443
    address: Optional[str] = None
    cert_fingerprint_sha256: Optional[str] = None


class Hello(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_version: str
    protocol_version: int
    os: str
    os_version: Optional[str] = None
    arch: str
    kernel_version: Optional[str] = None
    hostname: str
    ip_addresses: List[str] = Field(default_factory=list)
    cpu_cores: Optional[int] = None
    memory_total_bytes: Optional[int] = None
    boot_id: Optional[str] = None
    started_at: datetime
    capabilities: HelloCapabilities
    cache: HelloCache
    proxy: HelloProxy
    config_version: int = 0
    labels: Dict[str, str] = Field(default_factory=dict)


class HeartbeatInstance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instance_id: uuid.UUID
    state: str
    health: str
    active_sessions: Optional[int] = None
    waiting_sessions: Optional[int] = None


class Heartbeat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uptime_seconds: int = 0
    config_version: int = 0
    instances: List[HeartbeatInstance] = Field(default_factory=list)
    running_commands: List[uuid.UUID] = Field(default_factory=list)
    load: Optional[Dict[str, Any]] = None


class Device(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vendor: str
    index: int
    uuid: Optional[str] = None
    name: str
    vram_total_bytes: int = 0
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


class Disk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mount: str
    device: Optional[str] = None
    fs: Optional[str] = None
    total_bytes: int = 0
    free_bytes: int = 0
    is_cache_dir: bool = False


class CacheItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_file_id: uuid.UUID
    model_version_id: uuid.UUID
    local_path: str
    size_bytes: int = 0
    state: str
    progress_pct: float = 0
    sha256_verified: bool = False
    pinned: bool = False
    last_used_at: Optional[datetime] = None
    error_message: Optional[str] = None


class InventoryCache(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dir: str
    limit_bytes: int = 0
    used_bytes: int = 0
    items: List[CacheItem] = Field(default_factory=list)


class InstanceStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instance_id: uuid.UUID
    deployment_id: Optional[uuid.UUID] = None
    engine_process_id: Optional[uuid.UUID] = None
    state: str
    health: str
    port: Optional[int] = None
    served_model_name: Optional[str] = None
    pid: Optional[int] = None
    container_id: Optional[str] = None
    device_ids: List[uuid.UUID] = Field(default_factory=list)
    active_sessions: Optional[int] = None
    waiting_sessions: Optional[int] = None
    message: Optional[str] = None
    distributed_rank: Optional[int] = None


class EngineProcess(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engine_process_id: uuid.UUID
    engine: str
    engine_version_id: Optional[uuid.UUID] = None
    port: int
    pid: Optional[int] = None
    device_ids: List[uuid.UUID] = Field(default_factory=list)
    instance_ids: List[uuid.UUID] = Field(default_factory=list)


class Inventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    devices: List[Device] = Field(default_factory=list)
    disks: List[Disk] = Field(default_factory=list)
    cache: InventoryCache
    instances: List[InstanceStatus] = Field(default_factory=list)
    engine_processes: List[EngineProcess] = Field(default_factory=list)
    system: Optional[Dict[str, Any]] = None
    config_version: Optional[int] = None
    ssh_instances: List[Dict[str, Any]] = Field(default_factory=list)


class GpuSample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ts: datetime
    device_id: uuid.UUID
    utilization_pct: Optional[float] = None
    memory_util_pct: Optional[float] = None
    memory_used_bytes: Optional[int] = None
    memory_total_bytes: Optional[int] = None
    power_watts: Optional[float] = None
    power_limit_watts: Optional[float] = None
    temperature_c: Optional[float] = None
    memory_temperature_c: Optional[float] = None
    sm_clock_mhz: Optional[int] = None
    memory_clock_mhz: Optional[int] = None
    fan_pct: Optional[float] = None
    encoder_pct: Optional[float] = None
    decoder_pct: Optional[float] = None
    pcie_tx_bytes_per_s: Optional[int] = None
    pcie_rx_bytes_per_s: Optional[int] = None
    ecc_errors_total: Optional[int] = None
    error_count: Optional[int] = None
    throttle_reasons: Optional[List[str]] = None
    process_count: Optional[int] = None
    extra: Optional[Dict[str, Any]] = None


class EngineSample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ts: datetime
    instance_id: uuid.UUID
    deployment_id: uuid.UUID
    active_sessions: Optional[int] = None
    waiting_sessions: Optional[int] = None
    slots_total: Optional[int] = None
    slots_idle: Optional[int] = None
    requests_total: Optional[int] = None
    requests_success_total: Optional[int] = None
    errors_total: Optional[int] = None
    prompt_tokens_total: Optional[int] = None
    generated_tokens_total: Optional[int] = None
    cached_tokens_total: Optional[int] = None
    prompt_tps: Optional[float] = None
    generation_tps: Optional[float] = None
    ttft_ms_avg: Optional[float] = None
    ttft_ms_p50: Optional[float] = None
    ttft_ms_p95: Optional[float] = None
    e2e_ms_avg: Optional[float] = None
    e2e_ms_p50: Optional[float] = None
    e2e_ms_p95: Optional[float] = None
    tpot_ms_avg: Optional[float] = None
    queue_ms_avg: Optional[float] = None
    kv_cache_used_tokens: Optional[int] = None
    kv_cache_total_tokens: Optional[int] = None
    kv_cache_usage_pct: Optional[float] = None
    kv_cache_bytes: Optional[int] = None
    prefix_cache_hit_rate: Optional[float] = None
    spec_accept_rate: Optional[float] = None
    process_vram_bytes: Optional[int] = None
    process_rss_bytes: Optional[int] = None
    health: Optional[str] = None
    extra: Optional[Dict[str, Any]] = None


class SessionSample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ts: datetime
    instance_id: uuid.UUID
    slot_id: Optional[int] = None
    session_key: str
    request_id: Optional[uuid.UUID] = None
    state: str
    prompt_tokens: Optional[int] = None
    generated_tokens: Optional[int] = None
    cached_tokens: Optional[int] = None
    kv_cache_tokens: Optional[int] = None
    kv_cache_bytes: Optional[int] = None
    context_size: Optional[int] = None
    ttft_ms: Optional[float] = None
    elapsed_ms: Optional[float] = None
    tokens_per_second: Optional[float] = None
    extra: Optional[Dict[str, Any]] = None


class MetricsBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    interval_seconds: float = 2.0
    sequence: Optional[int] = None
    gpu: List[GpuSample] = Field(default_factory=list)
    engine: List[EngineSample] = Field(default_factory=list)
    sessions: List[SessionSample] = Field(default_factory=list)
    agent: Optional[Dict[str, Any]] = None


class LogLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ts: datetime
    level: Optional[str] = None
    stream: Optional[str] = None
    text: str


class EventLog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    stream: Optional[str] = None
    lines: List[LogLine]


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    severity: str
    message: str
    instance_id: Optional[uuid.UUID] = None
    deployment_id: Optional[uuid.UUID] = None
    engine_process_id: Optional[uuid.UUID] = None
    device_id: Optional[uuid.UUID] = None
    ssh_instance_id: Optional[uuid.UUID] = None
    command_id: Optional[uuid.UUID] = None
    state: Optional[Dict[str, Any]] = None
    drain: Optional[Dict[str, Any]] = None
    cache_item: Optional[CacheItem] = None
    exit: Optional[Dict[str, Any]] = None
    log: Optional[EventLog] = None
    details: Optional[Dict[str, Any]] = None


class ErrorInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    retryable: bool = False
    details: Optional[Dict[str, Any]] = None


class CommandResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str
    status: CommandResultStatus
    progress_pct: Optional[float] = None
    progress_message: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[ErrorInfo] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    instance_id: Optional[uuid.UUID] = None


# ---------- hub -> agent payloads ----------


class Intervals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    heartbeat_seconds: int = 10
    metrics_seconds: float = 2
    inventory_seconds: int = 60
    command_ack_timeout_seconds: int = 10


class HelloAck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: bool
    reason: Optional[str] = None
    server_time: datetime
    node_id: uuid.UUID
    node_name: Optional[str] = None
    cluster_id: Optional[uuid.UUID] = None
    intervals: Intervals
    config_version: int = 0
    min_agent_version: Optional[str] = None
    drain: bool = False


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: CommandName
    timeout_seconds: int = 900
    swap_operation_id: Optional[uuid.UUID] = None
    args: Dict[str, Any] = Field(default_factory=dict)


class CrlPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_at: datetime
    serials: List[str] = Field(default_factory=list)
    fingerprints_sha256: List[str] = Field(default_factory=list)


class RoutingEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instance_id: uuid.UUID
    deployment_id: uuid.UUID
    model_name: Optional[str] = None
    model_type: Optional[str] = None
    engine: Optional[str] = None
    port: int
    served_model_name: str
    rewrite_model: bool = True
    allowed_path_prefixes: List[str]
    accepting: bool = True
    max_concurrent: Optional[int] = None


class ConfigPush(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config_version: int
    full: bool
    routing: Optional[List[RoutingEntry]] = None
    egress_allowlist: Optional[List[str]] = None
    engine_catalog: Optional[List[Dict[str, Any]]] = None
    crl: Optional[CrlPayload] = None
    ca_bundle_pem: Optional[str] = None
    settings: Optional[Dict[str, Any]] = None
    model_sources: Optional[Dict[str, Any]] = None
