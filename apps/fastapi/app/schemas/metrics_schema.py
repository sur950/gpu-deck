"""
Metrics, overview, live and session schemas — shapes from
``libs/contracts/openapi/admin.yaml`` (TimeSeriesResponse, SessionsSnapshot,
Overview, LiveEvent, metric catalog entries).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

MetricType = Literal["gauge", "counter", "histogram"]
MetricScope = Literal["gpu", "engine", "session", "usage", "node"]
Aggregate = Literal["none", "sum", "avg", "max"]
UsageGroupBy = Literal["none", "api_key", "model", "node", "deployment"]
LiveTopic = Literal[
    "metrics",
    "instance",
    "node",
    "device",
    "swap",
    "rule_run",
    "alert",
    "log",
    "heartbeat",
]


class MetricCatalogEntry(BaseModel):
    name: str
    unit: str
    type: MetricType
    scope: MetricScope
    description: str = ""
    prometheus_name: Optional[str] = None


class TimeSeries(BaseModel):
    labels: Dict[str, str] = Field(default_factory=dict)
    metric: str
    unit: str
    points: List[Tuple[datetime, Optional[float]]] = Field(default_factory=list)


class TimeSeriesResponse(BaseModel):
    from_: datetime = Field(alias="from")
    to: datetime
    step: str
    series: List[TimeSeries] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class SessionSample(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ts: datetime
    instance_id: uuid.UUID
    slot_id: Optional[int] = None
    session_key: str
    request_id: Optional[uuid.UUID] = None
    state: Literal["idle", "waiting", "processing", "generating"]
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


class SessionsSnapshot(BaseModel):
    ts: datetime
    instance_id: uuid.UUID
    active: int = 0
    waiting: int = 0
    slots_total: Optional[int] = None
    sessions: List[SessionSample] = Field(default_factory=list)


class OverviewCounts(BaseModel):
    clusters: int = 0
    nodes: int = 0
    nodes_online: int = 0
    devices: int = 0
    devices_unhealthy: int = 0
    models: int = 0
    deployments: int = 0
    deployments_running: int = 0
    instances_ready: int = 0
    api_keys_active: int = 0
    rules_enabled: int = 0
    swaps_in_progress: int = 0
    alerts_open: int = 0


class OverviewLive(BaseModel):
    ts: datetime
    gpu_utilization_pct_avg: Optional[float] = None
    vram_used_bytes: int = 0
    vram_total_bytes: int = 0
    power_watts: Optional[float] = None
    active_sessions: int = 0
    waiting_sessions: int = 0
    requests_per_minute: Optional[float] = None
    generation_tps: Optional[float] = None
    ttft_ms_p95: Optional[float] = None
    error_rate_pct: Optional[float] = None


class Overview(BaseModel):
    counts: OverviewCounts
    live: OverviewLive
    cost_today_usd: float = 0.0
    cost_month_usd: float = 0.0
    recent_alerts: List[Dict[str, Any]] = Field(default_factory=list)
    recent_swaps: List[Dict[str, Any]] = Field(default_factory=list)
    recent_rule_runs: List[Dict[str, Any]] = Field(default_factory=list)


class LiveEvent(BaseModel):
    event: LiveTopic
    ts: datetime
    data: Dict[str, Any] = Field(default_factory=dict)
