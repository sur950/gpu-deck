"""
Metering schemas (admin.yaml: Metering tag — /usage, /usage/summary, /usage/export).
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class UsageFormat(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"


class UsageStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class UsageGroupBy(str, Enum):
    API_KEY = "api_key"
    MODEL = "model"
    NODE = "node"
    DEPLOYMENT = "deployment"
    CLUSTER = "cluster"
    FORMAT = "format"
    ENDPOINT = "endpoint"
    DAY = "day"
    HOUR = "hour"


class UsageEvent(BaseModel):
    id: int
    ts: datetime
    request_id: UUID
    api_key_id: Optional[UUID] = None
    api_key_name: Optional[str] = None
    model_id: Optional[UUID] = None
    model_name: str
    deployment_id: Optional[UUID] = None
    instance_id: Optional[UUID] = None
    node_id: Optional[UUID] = None
    node_name: Optional[str] = None
    cluster_id: Optional[UUID] = None
    format: UsageFormat
    endpoint: str
    stream: bool = False
    request_count: int = 1
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    reasoning_tokens: int = 0
    media_units: float = 0
    latency_ms: Optional[int] = None
    ttft_ms: Optional[int] = None
    gpu_seconds: float
    gpu_count: int = 1
    status: UsageStatus
    http_status: Optional[int] = None
    error_code: Optional[str] = None
    price_plan_id: Optional[UUID] = None
    cost_usd: float
    ip_address: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class UsageTotals(BaseModel):
    request_count: int = 0
    success_count: int = 0
    error_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    media_units: float = 0
    gpu_seconds: float = 0
    gpu_minutes: float = 0
    latency_ms_avg: Optional[float] = None
    ttft_ms_avg: Optional[float] = None
    cost_usd: float = 0


class UsageSummaryRow(BaseModel):
    group: Dict[str, str]
    labels: Dict[str, str]
    totals: UsageTotals


class UsageSummary(BaseModel):
    model_config = {"populate_by_name": True}

    from_: datetime = Field(..., alias="from", serialization_alias="from")
    to: datetime
    group_by: List[str]
    rows: List[UsageSummaryRow]
    totals: UsageTotals

    def model_dump(self, **kwargs):  # envelope serialises with the wire name "from"
        kwargs.setdefault("by_alias", True)
        kwargs.setdefault("mode", "json")
        return super().model_dump(**kwargs)
