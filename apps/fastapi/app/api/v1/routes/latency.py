"""
Network latency routes (admin.yaml: Metrics tag).

GET /metrics/latency/live    current RTT per node: from the agent hub and from every API layer
GET /metrics/latency         RTT history per node / source / API layer + p50 / p95 summaries
GET /metrics/request-timing  where request time went (route, queue, connect, network, engine)
"""

import uuid
from typing import List, Optional

from app.core.logger import get_logger
from app.middlewares.session_verification import get_current_admin
from app.services.latency.service import LatencyService
from app.utils.api_wrapper import handle_exceptions, success_response
from fastapi import APIRouter, Depends, Query, Request

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Metrics"]


@router.get("/metrics/latency/live", summary="Current network round trips per node")
@handle_exceptions
async def get_live_latency(
    request: Request,
    node_id: Optional[uuid.UUID] = Query(None),
    admin=Depends(get_current_admin),
    service: LatencyService = Depends(LatencyService.as_dependency),
):
    return success_response(data=await service.live(node_id))


@router.get("/metrics/latency", summary="Network round-trip history")
@handle_exceptions
async def query_latency_history(
    request: Request,
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    step: str = Query("1m"),
    node_id: Optional[List[uuid.UUID]] = Query(None),
    admin=Depends(get_current_admin),
    service: LatencyService = Depends(LatencyService.as_dependency),
):
    return success_response(data=await service.history(node_id, from_, to, step))


@router.get("/metrics/request-timing", summary="Per-request timing breakdown")
@handle_exceptions
async def query_request_timing(
    request: Request,
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    step: str = Query("5m"),
    deployment_id: Optional[uuid.UUID] = Query(None),
    node_id: Optional[uuid.UUID] = Query(None),
    model_id: Optional[uuid.UUID] = Query(None),
    api_key_id: Optional[uuid.UUID] = Query(None),
    region: Optional[str] = Query(None, max_length=64),
    stream: Optional[bool] = Query(None),
    admin=Depends(get_current_admin),
    service: LatencyService = Depends(LatencyService.as_dependency),
):
    filters = {
        "deployment_id": deployment_id,
        "node_id": node_id,
        "model_id": model_id,
        "api_key_id": api_key_id,
        "region": region,
        "stream": stream,
    }
    return success_response(data=await service.request_timing(filters, from_, to, step))
