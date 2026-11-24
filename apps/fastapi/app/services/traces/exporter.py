"""
Trace index exporter: an OpenTelemetry ``SpanExporter`` that writes the
subset of each span the UI needs into the day-partitioned ``traces`` table.
Full spans still go to ``OTEL_EXPORTER_OTLP_ENDPOINT`` when configured.

Indexed spans: SERVER and CLIENT spans, root spans, and any span carrying a
``gpudeck.*`` attribute. ASGI ``http send/receive`` sub-spans are skipped.
Runs inside ``BatchSpanProcessor``'s worker thread, so it uses a small
synchronous psycopg engine of its own (never the request event loop).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from app.core.logger import get_logger
from app.core.settings import settings
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.trace import SpanKind, StatusCode
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

logger = get_logger(__name__)

SERVICE_MAP = {
    "serving": "api_layer",
    "admin": "admin_api",
    "worker": "jobs",
    "scheduler": "jobs",
    "hub": "agent_hub",
}
FORMATS = ("openai", "anthropic", "ollama")
ID_ATTRS = (
    "request_id",
    "api_key_id",
    "model_id",
    "deployment_id",
    "instance_id",
    "node_id",
)

INSERT_SQL = text("""
    INSERT INTO traces (ts, trace_id, span_id, parent_span_id, name, service, request_id,
        api_key_id, model_id, deployment_id, instance_id, node_id, format, endpoint,
        status_code, duration_ms, error, attributes)
    VALUES (:ts, :trace_id, :span_id, :parent_span_id, :name, :service, :request_id,
        :api_key_id, :model_id, :deployment_id, :instance_id, :node_id,
        CAST(:format AS usage_format), :endpoint, :status_code, :duration_ms, :error,
        CAST(:attributes AS JSONB))
    """)


def _uuid(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        return None


def service_of(span: ReadableSpan) -> str:
    name = str((span.resource.attributes or {}).get("service.name") or "")
    suffix = name.rsplit("-", 1)[-1] if "-" in name else name
    return SERVICE_MAP.get(suffix, (suffix or "unknown")[:50])


def should_index(span: ReadableSpan) -> bool:
    attrs = span.attributes or {}
    if "asgi.event.type" in attrs:
        return False
    if span.parent is None or span.kind in (SpanKind.SERVER, SpanKind.CLIENT):
        return True
    return any(str(k).startswith("gpudeck.") for k in attrs)


def span_to_row(span: ReadableSpan) -> Dict[str, Any]:
    import json

    attrs = dict(span.attributes or {})
    ctx = span.get_span_context()
    start = datetime.fromtimestamp((span.start_time or 0) / 1e9, tz=timezone.utc)
    duration = (
        (span.end_time - span.start_time) / 1e6 if span.end_time and span.start_time else None
    )
    status_code = attrs.get("http.status_code") or attrs.get("http.response.status_code")
    fmt = attrs.get("gpudeck.format")
    row: Dict[str, Any] = {
        "ts": start,
        "trace_id": format(ctx.trace_id, "032x"),
        "span_id": format(ctx.span_id, "016x"),
        "parent_span_id": format(span.parent.span_id, "016x") if span.parent else None,
        "name": (span.name or "span")[:255],
        "service": service_of(span),
        "format": fmt if fmt in FORMATS else None,
        "endpoint": str(attrs.get("gpudeck.endpoint") or attrs.get("http.route") or "")[:100]
        or None,
        "status_code": int(status_code) if status_code is not None else None,
        "duration_ms": duration,
        "error": span.status.status_code == StatusCode.ERROR
        or (status_code is not None and int(status_code) >= 500),
        "attributes": json.dumps(
            {k: v if isinstance(v, (str, int, float, bool)) else str(v) for k, v in attrs.items()}
        ),
    }
    for key in ID_ATTRS:
        row[key] = _uuid(attrs.get(f"gpudeck.{key}"))
    return row


class TraceIndexExporter(SpanExporter):
    def __init__(self, database_url: Optional[str] = None):
        self._url = database_url or settings.get_sync_database_url()
        self._engine = None

    def _get_engine(self):
        if self._engine is None:
            self._engine = create_engine(self._url, pool_size=1, max_overflow=1, pool_pre_ping=True)
        return self._engine

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        rows: List[Dict[str, Any]] = [span_to_row(s) for s in spans if should_index(s)]
        if not rows:
            return SpanExportResult.SUCCESS
        try:
            self._insert(rows)
            return SpanExportResult.SUCCESS
        except Exception as exc:  # noqa: BLE001 - tracing must never break the app
            logger.warning(f"trace index export failed ({len(rows)} spans): {exc}")
            return SpanExportResult.FAILURE

    def _insert(self, rows: List[Dict[str, Any]]) -> None:
        engine = self._get_engine()
        try:
            with engine.begin() as conn:
                conn.execute(INSERT_SQL, rows)
        except DBAPIError as exc:
            if "no partition" not in str(exc) and getattr(exc.orig, "sqlstate", None) != "23514":
                raise
            with engine.begin() as conn:
                conn.execute(
                    text("SELECT create_daily_partitions(:ahead, 2)"),
                    {"ahead": settings.PARTITIONS_DAYS_AHEAD},
                )
            with engine.begin() as conn:
                conn.execute(INSERT_SQL, rows)

    def shutdown(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True
