"""
Pydantic schemas for benchmarks and benchmark runs (admin.yaml tag "Benchmarks").
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from app.core.logger import get_logger
from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = get_logger(__name__)

SLO_KEYS = {
    "ttft_ms_p95",
    "e2e_ms_p95",
    "tpot_ms_p95",
    "min_generation_tps",
    "max_error_rate_pct",
}
WORKLOAD_KEYS = {
    "input_tokens",
    "output_tokens",
    "requests_per_level",
    "dataset",
    "prompts",
    "stream",
}
DEFAULT_WORKLOAD = {
    "input_tokens": 512,
    "output_tokens": 128,
    "requests_per_level": 32,
    "dataset": "synthetic",
}


class BenchmarkRunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _check_slo(value: Dict[str, Any]) -> Dict[str, Any]:
    unknown = set(value) - SLO_KEYS
    if unknown:
        raise ValueError(f"unknown target_slo keys: {sorted(unknown)}")
    for k, v in value.items():
        if v is not None and (not isinstance(v, (int, float)) or v < 0):
            raise ValueError(f"target_slo.{k} must be a non-negative number")
    return value


def _check_workload(value: Dict[str, Any]) -> Dict[str, Any]:
    unknown = set(value) - WORKLOAD_KEYS
    if unknown:
        raise ValueError(f"unknown workload keys: {sorted(unknown)}")
    for k in ("input_tokens", "output_tokens", "requests_per_level"):
        if k in value and (not isinstance(value[k], int) or value[k] < 1):
            raise ValueError(f"workload.{k} must be a positive integer")
    if value.get("dataset") not in (None, "synthetic", "sharegpt_sample", "custom"):
        raise ValueError("workload.dataset must be synthetic, sharegpt_sample or custom")
    if value.get("dataset") == "custom" and not value.get("prompts"):
        raise ValueError("workload.dataset=custom needs prompts")
    return {**DEFAULT_WORKLOAD, **value}


def _check_levels(value: List[int]) -> List[int]:
    if not value:
        raise ValueError("concurrency_levels must not be empty")
    if any(v < 1 for v in value):
        raise ValueError("concurrency levels must be >= 1")
    return sorted(set(value))


class BenchmarkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=2048)
    deployment_id: uuid.UUID
    target_slo: Dict[str, Any] = Field(default_factory=dict)
    concurrency_levels: List[int] = Field(default_factory=lambda: [1, 2, 4, 8, 16])
    workload: Dict[str, Any] = Field(default_factory=lambda: dict(DEFAULT_WORKLOAD))
    duration_seconds: int = Field(60, ge=5, le=3600)
    auto_tune: bool = False

    _slo = field_validator("target_slo")(_check_slo)
    _workload = field_validator("workload")(_check_workload)
    _levels = field_validator("concurrency_levels")(_check_levels)


class BenchmarkUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=2048)
    target_slo: Optional[Dict[str, Any]] = None
    concurrency_levels: Optional[List[int]] = None
    workload: Optional[Dict[str, Any]] = None
    duration_seconds: Optional[int] = Field(None, ge=5, le=3600)
    auto_tune: Optional[bool] = None

    @field_validator("target_slo")
    @classmethod
    def _slo(cls, v: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        return _check_slo(v) if v is not None else v

    @field_validator("workload")
    @classmethod
    def _workload(cls, v: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        return _check_workload(v) if v is not None else v

    @field_validator("concurrency_levels")
    @classmethod
    def _levels(cls, v: Optional[List[int]]) -> Optional[List[int]]:
        return _check_levels(v) if v is not None else v


class BenchmarkRun(BaseModel):
    id: uuid.UUID
    benchmark_id: uuid.UUID
    benchmark_name: Optional[str] = None
    instance_id: Optional[uuid.UUID] = None
    node_id: Optional[uuid.UUID] = None
    status: BenchmarkRunStatus
    progress_pct: float = 0.0
    results: List[Dict[str, Any]] = Field(default_factory=list)
    best_concurrency: Optional[int] = None
    slo_met: Optional[bool] = None
    recommendation: Dict[str, Any] = Field(default_factory=dict)
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_at: datetime


class Benchmark(BaseModel):
    id: uuid.UUID
    name: str
    description: Optional[str] = None
    deployment_id: uuid.UUID
    deployment_name: Optional[str] = None
    target_slo: Dict[str, Any] = Field(default_factory=dict)
    concurrency_levels: List[int] = Field(default_factory=list)
    workload: Dict[str, Any] = Field(default_factory=dict)
    duration_seconds: int
    auto_tune: bool
    last_run: Optional[BenchmarkRun] = None
    run_count: int = 0
    created_at: datetime
    updated_at: Optional[datetime] = None


class BenchmarkStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instance_id: Optional[uuid.UUID] = None
    auto_tune: Optional[bool] = None
    flag_variants: List[Dict[str, Any]] = Field(default_factory=list, max_length=8)


class ApplyRecommendationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    apply_launch_flags: bool = True
    apply_replicas: bool = False
    apply_traffic_config: bool = True
