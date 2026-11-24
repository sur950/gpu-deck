"""
Log schemas: ``LogChunk`` and the cursor-paginated ``LogPage`` from
``libs/contracts/openapi/admin.yaml``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

LogSource = Literal["engine", "agent", "control_plane", "rule", "swap", "api_layer"]
LogLevel = Literal["debug", "info", "warn", "error"]
LogStream = Literal["stdout", "stderr", "app"]


class LogChunkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ts: datetime
    ts_end: Optional[datetime] = None
    node_id: Optional[uuid.UUID] = None
    node_name: Optional[str] = None
    instance_id: Optional[uuid.UUID] = None
    deployment_id: Optional[uuid.UUID] = None
    source: str
    stream: str = "stdout"
    level: str = "info"
    logger: Optional[str] = None
    request_id: Optional[uuid.UUID] = None
    line_count: int = 1
    content: str
    attributes: Optional[Dict[str, Any]] = None


class LogPage(BaseModel):
    items: List[LogChunkResponse] = Field(default_factory=list)
    next_cursor: Optional[str] = None
    has_more: bool = False
