"""
Trace index schemas: ``TraceSpan`` and ``Trace`` from
``libs/contracts/openapi/admin.yaml``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class TraceSpanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ts: datetime
    trace_id: str
    span_id: str
    parent_span_id: Optional[str] = None
    name: str
    service: str
    request_id: Optional[uuid.UUID] = None
    api_key_id: Optional[uuid.UUID] = None
    model_id: Optional[uuid.UUID] = None
    deployment_id: Optional[uuid.UUID] = None
    instance_id: Optional[uuid.UUID] = None
    node_id: Optional[uuid.UUID] = None
    format: Optional[str] = None
    endpoint: Optional[str] = None
    status_code: Optional[int] = None
    duration_ms: Optional[float] = None
    error: bool = False
    attributes: Optional[Dict[str, Any]] = None


class TraceResponse(BaseModel):
    trace_id: str
    root: Optional[TraceSpanResponse] = None
    spans: List[TraceSpanResponse] = Field(default_factory=list)
    duration_ms: Optional[float] = None
    otlp_url: Optional[str] = None
