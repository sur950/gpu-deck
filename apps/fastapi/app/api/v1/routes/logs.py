"""
Log routes (admin.yaml: Logs tag).

GET /logs          query log chunks (newest first, cursor paginated) -> LogPage
GET /logs/stream   live tail (SSE, ``event: log`` frames carrying LogChunk JSON)
GET /logs/export   matching lines as text/plain (attachment)
"""

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from app.core.logger import get_logger
from app.middlewares.session_verification import get_current_admin
from app.schemas.logs_schema import LogLevel, LogStream
from app.services.logs.service import (
    LogService,
    export_log_lines,
    log_stream,
    split_sources,
)
from app.services.metrics.live import sse_format
from app.services.metrics.service import parse_time
from app.utils.api_wrapper import handle_exceptions, success_response
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Logs"]

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


@router.get("/logs", summary="Query log chunks")
@handle_exceptions
async def query_logs(
    request: Request,
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    node_id: Optional[uuid.UUID] = Query(None),
    instance_id: Optional[uuid.UUID] = Query(None),
    deployment_id: Optional[uuid.UUID] = Query(None),
    source: Optional[List[str]] = Query(None),
    level: Optional[LogLevel] = Query(None),
    stream: Optional[LogStream] = Query(None),
    request_id: Optional[uuid.UUID] = Query(None),
    search: Optional[str] = Query(None, max_length=256),
    cursor: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    admin=Depends(get_current_admin),
    service: LogService = Depends(LogService.as_dependency),
):
    page = await service.query(
        from_,
        to,
        node_id,
        instance_id,
        deployment_id,
        source,
        level,
        stream,
        request_id,
        search,
        cursor,
        limit,
    )
    return success_response(data=page)


@router.get("/logs/stream", summary="Live log tail (SSE)")
async def stream_logs(
    request: Request,
    node_id: Optional[uuid.UUID] = Query(None),
    instance_id: Optional[uuid.UUID] = Query(None),
    deployment_id: Optional[uuid.UUID] = Query(None),
    source: Optional[List[str]] = Query(None),
    level: Optional[LogLevel] = Query(None),
    admin=Depends(get_current_admin),
):
    split_sources(source)  # validate before the stream starts (422 instead of a broken stream)

    async def _gen():
        async for event in log_stream(
            node_id, instance_id, deployment_id, source, level, request.is_disconnected
        ):
            yield sse_format(event)

    return StreamingResponse(_gen(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.get("/logs/export", summary="Download logs as text")
@handle_exceptions
async def export_logs(
    request: Request,
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    node_id: Optional[uuid.UUID] = Query(None),
    instance_id: Optional[uuid.UUID] = Query(None),
    source: Optional[List[str]] = Query(None),
    level: Optional[LogLevel] = Query(None),
    admin=Depends(get_current_admin),
):
    split_sources(source)
    parse_time(from_, datetime.now(timezone.utc))  # 422 on bad input before streaming
    parse_time(to, datetime.now(timezone.utc))

    async def _gen():
        async for line in export_log_lines(from_, to, node_id, instance_id, source, level):
            yield line

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return StreamingResponse(
        _gen(),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="gpudeck-logs-{stamp}.log"'},
    )
