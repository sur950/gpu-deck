"""
Trace routes (admin.yaml: Traces tag).

GET /traces              search root spans (time, service, ids, format, status, error, duration)
GET /traces/{trace_id}   one trace: root + all spans (waterfall) + external UI link
"""

import uuid
from typing import Optional

from app.core.logger import get_logger
from app.middlewares.session_verification import get_current_admin
from app.services.traces.service import TraceService, trace_filters
from app.utils.api_wrapper import handle_exceptions, paginated_meta, success_response
from fastapi import APIRouter, Depends, Query, Request

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Traces"]


@router.get("/traces", summary="Search root spans")
@handle_exceptions
async def list_traces(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    service: Optional[str] = Query(None, max_length=50),
    api_key_id: Optional[uuid.UUID] = Query(None),
    model_id: Optional[uuid.UUID] = Query(None),
    deployment_id: Optional[uuid.UUID] = Query(None),
    instance_id: Optional[uuid.UUID] = Query(None),
    node_id: Optional[uuid.UUID] = Query(None),
    format_: Optional[str] = Query(None, alias="format", pattern="^(openai|anthropic|ollama)$"),
    status_code: Optional[int] = Query(None, ge=100, le=599),
    error: Optional[bool] = Query(None),
    min_duration_ms: Optional[float] = Query(None, ge=0),
    request_id: Optional[uuid.UUID] = Query(None),
    admin=Depends(get_current_admin),
    traces: TraceService = Depends(TraceService.as_dependency),
):
    filters = trace_filters(
        service=service,
        api_key_id=api_key_id,
        model_id=model_id,
        deployment_id=deployment_id,
        instance_id=instance_id,
        node_id=node_id,
        format=format_,
        status_code=status_code,
        request_id=request_id,
    )
    items, total = await traces.search(page, page_size, from_, to, filters, error, min_duration_ms)
    return success_response(data=items, meta=paginated_meta(total, page, page_size))


@router.get("/traces/{trace_id}", summary="Get one trace")
@handle_exceptions
async def get_trace(
    request: Request,
    trace_id: str,
    admin=Depends(get_current_admin),
    traces: TraceService = Depends(TraceService.as_dependency),
):
    return success_response(data=await traces.get(trace_id))
