"""
Model cache routes (admin.yaml tag "Model Cache").

GET    /model-cache                  — cache items across nodes
GET    /model-cache/summary          — per-node summary
POST   /model-cache/prefetch         — download a version to nodes
POST   /model-cache/evict            — evict / unpin
DELETE /model-cache/{item_id}        — evict one item
POST   /model-cache/{item_id}/pin
POST   /model-cache/{item_id}/unpin
"""

import uuid
from typing import Optional

from app.core.logger import get_logger
from app.schemas.model_cache_schema import (
    CacheState,
    EvictRequest,
    PinRequest,
    PrefetchRequest,
)
from app.services.catalog.auth import get_current_admin
from app.services.model_cache.service import ModelCacheService
from app.utils.api_wrapper import handle_exceptions, paginated_meta, success_response
from fastapi import APIRouter, Body, Depends, Query, Request, status

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Model Cache"]


@router.get("/model-cache", summary="Cache items across nodes")
@handle_exceptions
async def list_cache_items(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    sort: Optional[str] = Query(None),
    node_id: Optional[uuid.UUID] = Query(None),
    model_id: Optional[uuid.UUID] = Query(None),
    model_version_id: Optional[uuid.UUID] = Query(None),
    state: Optional[CacheState] = Query(None),
    pinned: Optional[bool] = Query(None),
    unused_for_hours: Optional[int] = Query(None, ge=0),
    admin=Depends(get_current_admin),
    service: ModelCacheService = Depends(ModelCacheService.as_dependency),
):
    rows, total = await service.list_items(
        page=page,
        page_size=page_size,
        sort=sort,
        node_id=node_id,
        model_id=model_id,
        model_version_id=model_version_id,
        state=state.value if state else None,
        pinned=pinned,
        unused_for_hours=unused_for_hours,
    )
    return success_response(data=rows, meta=paginated_meta(total, page, page_size))


@router.get("/model-cache/summary", summary="Per-node cache summary")
@handle_exceptions
async def get_cache_summary(
    request: Request,
    cluster_id: Optional[uuid.UUID] = Query(None),
    admin=Depends(get_current_admin),
    service: ModelCacheService = Depends(ModelCacheService.as_dependency),
):
    rows = await service.summary(cluster_id)
    return success_response(data=rows, meta=paginated_meta(len(rows), 1, max(len(rows), 1)))


@router.post("/model-cache/prefetch", status_code=status.HTTP_202_ACCEPTED, summary="Prefetch")
@handle_exceptions
async def prefetch_model(
    request: Request,
    body: PrefetchRequest,
    admin=Depends(get_current_admin),
    service: ModelCacheService = Depends(ModelCacheService.as_dependency),
):
    return success_response(data=await service.prefetch_many(body))


@router.post("/model-cache/evict", status_code=status.HTTP_202_ACCEPTED, summary="Evict / unpin")
@handle_exceptions
async def evict_cache(
    request: Request,
    body: EvictRequest,
    admin=Depends(get_current_admin),
    service: ModelCacheService = Depends(ModelCacheService.as_dependency),
):
    return success_response(data=await service.evict(body))


@router.delete(
    "/model-cache/{item_id}",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Evict one item",
)
@handle_exceptions
async def delete_cache_item(
    request: Request,
    item_id: uuid.UUID,
    force: bool = Query(False),
    admin=Depends(get_current_admin),
    service: ModelCacheService = Depends(ModelCacheService.as_dependency),
):
    return success_response(data=await service.evict_item(item_id, force=force))


@router.post("/model-cache/{item_id}/pin", summary="Pin (never auto-evicted)")
@handle_exceptions
async def pin_cache_item(
    request: Request,
    item_id: uuid.UUID,
    body: Optional[PinRequest] = Body(None),
    admin=Depends(get_current_admin),
    service: ModelCacheService = Depends(ModelCacheService.as_dependency),
):
    return success_response(data=await service.pin(item_id, body.reason if body else None))


@router.post("/model-cache/{item_id}/unpin", summary="Unpin")
@handle_exceptions
async def unpin_cache_item(
    request: Request,
    item_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: ModelCacheService = Depends(ModelCacheService.as_dependency),
):
    return success_response(data=await service.unpin(item_id))
