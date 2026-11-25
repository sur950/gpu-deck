"""
Traces service: search root spans and fetch one trace (span waterfall)
from the ``traces`` index table.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.core.exceptions import ResourceNotFoundError, ValidationError
from app.core.logger import get_logger
from app.core.settings import settings
from app.db.postgres.connection import get_db
from app.db.postgres.repositories.trace_repository import TraceRepository
from app.services.metrics.service import parse_time
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

SPAN_KEYS = (
    "ts",
    "trace_id",
    "span_id",
    "parent_span_id",
    "name",
    "service",
    "request_id",
    "api_key_id",
    "model_id",
    "deployment_id",
    "instance_id",
    "node_id",
    "format",
    "endpoint",
    "status_code",
    "duration_ms",
    "error",
    "attributes",
)


def span_dict(row: Dict[str, Any]) -> Dict[str, Any]:
    return {k: row.get(k) for k in SPAN_KEYS}


def otlp_url(trace_id: str) -> Optional[str]:
    template = settings.OTEL_UI_URL
    return template.replace("{trace_id}", trace_id) if template else None


class TraceService:
    def __init__(self, session: AsyncSession):
        self.repo = TraceRepository(session)

    @staticmethod
    async def as_dependency(
        request: Request, db: AsyncSession = Depends(get_db, scope="function")
    ) -> "TraceService":
        return TraceService(db)

    async def search(
        self,
        page: int,
        page_size: int,
        from_: Optional[str] = None,
        to: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        error: Optional[bool] = None,
        min_duration_ms: Optional[float] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        end = parse_time(to, datetime.now(timezone.utc))
        start = parse_time(from_, end - timedelta(hours=1))
        if start >= end:
            raise ValidationError("'from' must be before 'to'")
        rows, total = await self.repo.search_roots(
            start, end, page, page_size, filters or {}, error, min_duration_ms
        )
        return [span_dict(r) for r in rows], total

    async def get(self, trace_id: str) -> Dict[str, Any]:
        trace_id = trace_id.strip().lower()
        if not trace_id or len(trace_id) > 32:
            raise ValidationError("invalid trace id")
        rows = await self.repo.spans_for_trace(trace_id)
        if not rows:
            raise ResourceNotFoundError(resource_type="Trace")
        spans = [span_dict(r) for r in rows]
        root = next((s for s in spans if not s["parent_span_id"]), None)
        start = min(s["ts"] for s in spans)
        ends = [
            s["ts"] + timedelta(milliseconds=float(s["duration_ms"]))
            for s in spans
            if s["duration_ms"] is not None
        ]
        duration = (
            float(root["duration_ms"])
            if root and root["duration_ms"] is not None
            else ((max(ends) - start).total_seconds() * 1000 if ends else None)
        )
        return {
            "trace_id": trace_id,
            "root": root,
            "spans": spans,
            "duration_ms": duration,
            "otlp_url": otlp_url(trace_id),
        }


def trace_filters(**kwargs: Any) -> Dict[str, Any]:
    """Map route params onto ``traces`` columns (None values dropped)."""
    out: Dict[str, Any] = {}
    for key, value in kwargs.items():
        if value is None:
            continue
        out[key] = value
    return out
