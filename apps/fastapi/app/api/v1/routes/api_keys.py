"""
API key routes (admin.yaml: API Keys tag).

GET    /api-keys                 list (page, page_size, search, sort, status)
POST   /api-keys                 create — plaintext returned once            201
GET    /api-keys/stats           counts by status
GET    /api-keys/{key_id}        get
PATCH  /api-keys/{key_id}        update name/description/expiry/metadata
DELETE /api-keys/{key_id}        soft-delete (also revokes)                  204
POST   /api-keys/{key_id}/revoke | /suspend | /activate | /rotate
"""

import uuid
from typing import Optional

from app.core.logger import get_logger
from app.middlewares.session_verification import get_current_admin
from app.schemas.api_key_schema import (
    ApiKey,
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyStats,
    ApiKeyStatus,
    ApiKeyUpdate,
)
from app.services.api_keys.service import ApiKeyService
from app.utils.api_wrapper import handle_exceptions, paginated_meta, success_response
from fastapi import APIRouter, Depends, Query, Request, Response, status

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["API Keys"]


@router.get("/api-keys", summary="List API keys")
@handle_exceptions
async def list_api_keys(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    search: Optional[str] = Query(None),
    sort: Optional[str] = Query(None),
    status_filter: Optional[ApiKeyStatus] = Query(None, alias="status"),
    admin=Depends(get_current_admin),
    service: ApiKeyService = Depends(ApiKeyService.as_dependency),
):
    items, total = await service.list_keys(
        page=page,
        page_size=page_size,
        status=status_filter.value if status_filter else None,
        search=search,
        sort=sort,
    )
    return success_response(
        data=[ApiKey.model_validate(i) for i in items],
        meta=paginated_meta(total, page, page_size),
    )


@router.post("/api-keys", summary="Create an API key (plaintext returned once)", status_code=201)
@handle_exceptions
async def create_api_key(
    request: Request,
    payload: ApiKeyCreate,
    admin=Depends(get_current_admin),
    service: ApiKeyService = Depends(ApiKeyService.as_dependency),
):
    created = await service.create_key(payload.model_dump(), created_by=admin.id)
    return success_response(data=ApiKeyCreated.model_validate(created))


@router.get("/api-keys/stats", summary="Counts by status")
@handle_exceptions
async def get_api_key_stats(
    request: Request,
    admin=Depends(get_current_admin),
    service: ApiKeyService = Depends(ApiKeyService.as_dependency),
):
    return success_response(data=ApiKeyStats.model_validate(await service.stats()))


@router.get("/api-keys/{key_id}", summary="Get an API key")
@handle_exceptions
async def get_api_key(
    request: Request,
    key_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: ApiKeyService = Depends(ApiKeyService.as_dependency),
):
    return success_response(data=ApiKey.model_validate(await service.get_key(key_id)))


@router.patch("/api-keys/{key_id}", summary="Update name/description/expiry/metadata")
@handle_exceptions
async def update_api_key(
    request: Request,
    key_id: uuid.UUID,
    payload: ApiKeyUpdate,
    admin=Depends(get_current_admin),
    service: ApiKeyService = Depends(ApiKeyService.as_dependency),
):
    updated = await service.update_key(key_id, payload.model_dump(exclude_unset=True))
    return success_response(data=ApiKey.model_validate(updated))


@router.delete(
    "/api-keys/{key_id}",
    summary="Soft-delete (also revokes)",
    status_code=status.HTTP_204_NO_CONTENT,
)
@handle_exceptions
async def delete_api_key(
    request: Request,
    key_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: ApiKeyService = Depends(ApiKeyService.as_dependency),
):
    await service.delete_key(key_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/api-keys/{key_id}/revoke", summary="Revoke immediately")
@handle_exceptions
async def revoke_api_key(
    request: Request,
    key_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: ApiKeyService = Depends(ApiKeyService.as_dependency),
):
    return success_response(data=ApiKey.model_validate(await service.revoke_key(key_id)))


@router.post("/api-keys/{key_id}/suspend", summary="Suspend (reversible)")
@handle_exceptions
async def suspend_api_key(
    request: Request,
    key_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: ApiKeyService = Depends(ApiKeyService.as_dependency),
):
    return success_response(data=ApiKey.model_validate(await service.suspend_key(key_id)))


@router.post("/api-keys/{key_id}/activate", summary="Re-activate a suspended key")
@handle_exceptions
async def activate_api_key(
    request: Request,
    key_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: ApiKeyService = Depends(ApiKeyService.as_dependency),
):
    return success_response(data=ApiKey.model_validate(await service.activate_key(key_id)))


@router.post("/api-keys/{key_id}/rotate", summary="Rotate: new secret, same id")
@handle_exceptions
async def rotate_api_key(
    request: Request,
    key_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: ApiKeyService = Depends(ApiKeyService.as_dependency),
):
    return success_response(data=ApiKeyCreated.model_validate(await service.rotate_key(key_id)))
