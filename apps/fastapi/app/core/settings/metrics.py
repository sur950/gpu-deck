"""
Metrics, logs and traces settings: retention windows, partition horizon,
live-sample staleness, log batching pushed to agents, Prometheus scrape token
and the trace index knobs. Names follow ``libs/contracts/env.md`` §10.
"""

from __future__ import annotations

from typing import Optional

from app.core.settings.base import DomainSettings
from pydantic import Field


class MetricsSettings(DomainSettings):
    """Observability storage and query knobs."""

    METRICS_RAW_RETENTION_HOURS: int = Field(
        default=48, description="metric_samples_* partitions kept (hours)"
    )
    METRICS_ROLLUP_RETENTION_DAYS: int = Field(
        default=90, description="metric_rollups_*_1m partitions kept (days)"
    )
    LOGS_RETENTION_DAYS: int = Field(default=14, description="log_chunks partitions kept")
    TRACES_RETENTION_DAYS: int = Field(default=7, description="traces partitions kept")
    PARTITIONS_DAYS_AHEAD: int = Field(
        default=7,
        description="create_daily_partitions() horizon (today + N days)",
    )
    METRICS_QUERY_MAX_POINTS: int = Field(
        default=5000,
        description="Max buckets per series in /metrics/* (from/to/step guard)",
    )
    METRICS_LIVE_STALE_SECONDS: int = Field(
        default=10,
        description="Latest sample older than this reads as null in `live` fields",
    )
    METRICS_ROLLUP_CATCHUP_MINUTES: int = Field(
        default=120, description="How far back the minute rollup job backfills on a gap"
    )
    DB_METRICS_INGEST_BATCH_SIZE: int = Field(
        default=2000, description="Rows per insert batch in the hub ingest"
    )
    PROMETHEUS_SCRAPE_TOKEN: Optional[str] = Field(
        None, description="Bearer token required on /api/v1/metrics/prometheus when set"
    )

    # Log batching pushed to agents via config_push.settings
    LOG_CHUNK_MAX_LINES: int = Field(default=100, description="Lines per log event / chunk")
    LOG_FLUSH_INTERVAL_MS: int = Field(default=1000, description="Agent log flush interval")
    LOG_FORWARD_ENGINE_OUTPUT: bool = Field(
        default=True, description="Agents forward engine stdout/stderr as log events"
    )
    LOGS_QUERY_MAX_LIMIT: int = Field(default=1000, description="Max rows per /logs page")

    # Live push
    LIVE_EVENTS_REPLAY_SECONDS: int = Field(
        default=60,
        description="Replay window honoured for Last-Event-ID on /live/events",
    )
    LIVE_HEARTBEAT_SECONDS: int = Field(default=15, description="SSE heartbeat cadence")

    # Traces
    OTEL_TRACES_SAMPLER_ARG: float = Field(
        default=0.1, description="Ratio for parentbased_traceidratio samplers"
    )
    OTEL_UI_URL: Optional[str] = Field(
        None,
        description="Deep link template to an external tracing UI, with {trace_id}",
    )
    GRAFANA_URL: Optional[str] = Field(None, description="Grafana link shown in the UI")
    TRACE_INDEX_ENABLED: bool = Field(
        default=True, description="Write a span index into the traces table"
    )
