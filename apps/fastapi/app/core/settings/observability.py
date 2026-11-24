"""
Logging, Prometheus and OpenTelemetry settings.
"""

from __future__ import annotations

from typing import Optional

from app.core.settings.base import DomainSettings, strip_quotes
from pydantic import Field, field_validator


class ObservabilitySettings(DomainSettings):
    """Loguru sinks, /metrics and OTLP export."""

    LOG_LEVEL: str = Field(default="INFO", description="Log level")
    LOG_FORMAT: str = Field(default="text", description="Log format: json or text")
    LOG_FILE_PATH: str = Field(default="logs/app.log", description="Loguru file sink path")
    LOG_ROTATION: str = Field(default="500 MB", description="Log rotation size")
    LOG_RETENTION: str = Field(default="10 days", description="Log retention period")

    SERVING_ACCESS_LOG_SAMPLE_RATE: float = Field(
        default=0.01,
        ge=0.0,
        le=1.0,
        description="Serving API: fraction of requests that get an INFO access-log line "
        "(the rest log at DEBUG; 5xx always log at WARNING)",
    )

    METRICS_ENABLED: bool = Field(default=True, description="Expose Prometheus /metrics")
    WORKER_METRICS_PORT: int = Field(
        default=9464,
        ge=0,
        le=65535,
        description="Prometheus port of the Celery worker process (celery_tasks_total; 0 = off). "
        "Prefork pools need PROMETHEUS_MULTIPROC_DIR",
    )
    SCHEDULER_METRICS_PORT: int = Field(
        default=9465,
        ge=0,
        le=65535,
        description="Prometheus port of the jobs/scheduler process (rules, swaps, leader; 0 = off)",
    )
    METRICS_BIND_HOST: str = Field(
        default="0.0.0.0",
        description="Bind address of the worker/scheduler Prometheus listeners",
    )
    OTEL_EXPORTER_OTLP_ENDPOINT: Optional[str] = Field(None, description="OTLP gRPC endpoint")
    OTEL_SERVICE_NAME: str = Field(default="gpu-deck", description="OTel service name base")
    OTEL_TRACES_SAMPLER: str = Field(default="always_on", description="OTel traces sampler")
    OTEL_CONSOLE_EXPORT: bool = Field(
        default=False, description="Print spans to stdout when no OTLP endpoint is set"
    )

    HEALTH_CHECK_ENABLED: bool = Field(default=True, description="Enable health checks")
    HEALTH_CHECK_INTERVAL_SECONDS: int = Field(default=30, description="Health check interval")

    @field_validator("LOG_LEVEL")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        v = v.upper()
        if v not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {allowed}")
        return v

    @field_validator("LOG_FORMAT")
    @classmethod
    def validate_log_format(cls, v: str) -> str:
        v = v.lower()
        if v not in ("json", "text"):
            raise ValueError("LOG_FORMAT must be json or text")
        return v

    @field_validator("LOG_ROTATION", "LOG_RETENTION")
    @classmethod
    def _strip(cls, v: str) -> str:
        return strip_quotes(v)
