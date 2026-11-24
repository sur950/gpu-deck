"""
Swap manager routes (admin.yaml: Swaps tag).

GET  /swaps                              list (state, kind, trigger, deployment, node, active_only)
POST /swaps                              manual load / unload / move / restart          202
GET  /swaps/{swap_id}                    get (steps with command ids and timings)
POST /swaps/{swap_id}/cancel             cancel (rolls back when past unload)
POST /swaps/{swap_id}/retry              re-run a failed / rolled back / cancelled swap 202
(GET /deployments/{deployment_id}/swaps is served by the deployments routes.)

Execution is asynchronous: the jobs leader's swap manager picks up pending
rows within SWAP_DRIVE_INTERVAL_SECONDS.
"""

import uuid
from datetime import datetime
from typing import Optional

from app.core.logger import get_logger
from app.core.rate_limiter import rate_limit
from app.middlewares.session_verification import get_current_admin
from app.schemas.swaps_schema import SwapCreate, SwapKind, SwapState, SwapTrigger
from app.services.swap.service import SwapService
from app.utils.api_wrapper import handle_exceptions, paginated_meta, success_response
from fastapi import APIRouter, Depends, Query, Request

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Swaps"]


@router.get("/swaps", summary="List swap operations")
@handle_exceptions
async def list_swaps(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    sort: Optional[str] = Query(None),
    from_: Optional[datetime] = Query(None, alias="from"),
    to: Optional[datetime] = Query(None),
    state: Optional[SwapState] = Query(None),
    kind: Optional[SwapKind] = Query(None),
    trigger: Optional[SwapTrigger] = Query(None),
    deployment_id: Optional[uuid.UUID] = Query(None),
    node_id: Optional[uuid.UUID] = Query(None),
    active_only: bool = Query(False),
    admin=Depends(get_current_admin),
    service: SwapService = Depends(SwapService.as_dependency),
):
    items, total = await service.list_swaps(
        page=page,
        page_size=page_size,
        sort=sort,
        from_ts=from_,
        to_ts=to,
        state=state.value if state else None,
        kind=kind.value if kind else None,
        trigger=trigger.value if trigger else None,
        deployment_id=deployment_id,
        node_id=node_id,
        active_only=active_only,
    )
    return success_response(data=items, meta=paginated_meta(total, page, page_size))


@router.post("/swaps", summary="Start a manual swap", status_code=202)
@rate_limit("30/minute")
@handle_exceptions
async def create_swap(
    request: Request,
    payload: SwapCreate,
    admin=Depends(get_current_admin),
    service: SwapService = Depends(SwapService.as_dependency),
):
    return success_response(data=await service.create_swap(payload, created_by=admin.id))


@router.get("/swaps/{swap_id}", summary="Get a swap operation")
@handle_exceptions
async def get_swap(
    request: Request,
    swap_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: SwapService = Depends(SwapService.as_dependency),
):
    return success_response(data=await service.get_swap(swap_id))


@router.post("/swaps/{swap_id}/cancel", summary="Cancel a swap")
@handle_exceptions
async def cancel_swap(
    request: Request,
    swap_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: SwapService = Depends(SwapService.as_dependency),
):
    return success_response(data=await service.cancel(swap_id))


@router.post("/swaps/{swap_id}/retry", summary="Retry a failed swap", status_code=202)
@handle_exceptions
async def retry_swap(
    request: Request,
    swap_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: SwapService = Depends(SwapService.as_dependency),
):
    return success_response(data=await service.retry(swap_id, created_by=admin.id))
