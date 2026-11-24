"""
Registered model routes (admin.yaml tag "Catalog", /models part).

GET/POST       /models
GET/PATCH/DEL  /models/{model_id}
GET/POST       /models/{model_id}/versions
GET/PATCH/DEL  /models/{model_id}/versions/{version_id}
GET            /models/{model_id}/versions/{version_id}/files
POST           /models/{model_id}/versions/{version_id}/verify
GET            /models/{model_id}/versions/{version_id}/cache
"""

import uuid
from typing import Optional

from app.core.logger import get_logger
from app.schemas.catalog_schema import (
    ModelCreate,
    ModelPriority,
    ModelSource,
    ModelType,
    ModelUpdate,
    ModelVersionCreate,
    ModelVersionUpdate,
)
from app.services.catalog.auth import admin_id, get_current_admin
from app.services.catalog.service import CatalogService
from app.utils.api_wrapper import handle_exceptions, paginated_meta, success_response
from fastapi import APIRouter, Depends, Query, Request, Response, status

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Catalog"]


@router.get("/models", summary="List registered models")
@handle_exceptions
async def list_models(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    search: Optional[str] = Query(None),
    sort: Optional[str] = Query(None),
    type: Optional[ModelType] = Query(None),
    source: Optional[ModelSource] = Query(None),
    priority: Optional[ModelPriority] = Query(None),
    family: Optional[str] = Query(None),
    tag: Optional[str] = Query(None),
    admin=Depends(get_current_admin),
    service: CatalogService = Depends(CatalogService.as_dependency),
):
    rows, total = await service.list_models(
        page=page,
        page_size=page_size,
        search=search,
        sort=sort,
        type_=type.value if type else None,
        source=source.value if source else None,
        priority=priority.value if priority else None,
        family=family,
        tag=tag,
    )
    return success_response(data=rows, meta=paginated_meta(total, page, page_size))


@router.post("/models", status_code=status.HTTP_201_CREATED, summary="Register a model")
@handle_exceptions
async def register_model(
    request: Request,
    body: ModelCreate,
    admin=Depends(get_current_admin),
    service: CatalogService = Depends(CatalogService.as_dependency),
):
    """Register a model with its first version (remote files listed / local path scanned)."""
    model = await service.create_model(body, created_by=admin_id(admin))
    return success_response(data=model)


@router.get("/models/{model_id}", summary="Get a model")
@handle_exceptions
async def get_model(
    request: Request,
    model_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: CatalogService = Depends(CatalogService.as_dependency),
):
    return success_response(data=await service.get_model(model_id))


@router.patch("/models/{model_id}", summary="Update a model")
@handle_exceptions
async def update_model(
    request: Request,
    model_id: uuid.UUID,
    body: ModelUpdate,
    admin=Depends(get_current_admin),
    service: CatalogService = Depends(CatalogService.as_dependency),
):
    return success_response(data=await service.update_model(model_id, body))


@router.delete(
    "/models/{model_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete a model",
)
@handle_exceptions
async def delete_model(
    request: Request,
    model_id: uuid.UUID,
    evict_cache: bool = Query(False),
    admin=Depends(get_current_admin),
    service: CatalogService = Depends(CatalogService.as_dependency),
):
    """409 MODEL_IN_USE while deployments exist."""
    await service.delete_model(model_id, evict_cache=evict_cache)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/models/{model_id}/versions", summary="Versions of a model")
@handle_exceptions
async def list_model_versions(
    request: Request,
    model_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: CatalogService = Depends(CatalogService.as_dependency),
):
    rows = await service.list_versions(model_id)
    return success_response(data=rows, meta=paginated_meta(len(rows), 1, max(len(rows), 1)))


@router.post(
    "/models/{model_id}/versions",
    status_code=status.HTTP_201_CREATED,
    summary="Add a version",
)
@handle_exceptions
async def add_model_version(
    request: Request,
    model_id: uuid.UUID,
    body: ModelVersionCreate,
    admin=Depends(get_current_admin),
    service: CatalogService = Depends(CatalogService.as_dependency),
):
    return success_response(data=await service.add_version(model_id, body))


@router.get("/models/{model_id}/versions/{version_id}", summary="Get a version")
@handle_exceptions
async def get_model_version(
    request: Request,
    model_id: uuid.UUID,
    version_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: CatalogService = Depends(CatalogService.as_dependency),
):
    return success_response(data=await service.get_version(model_id, version_id))


@router.patch("/models/{model_id}/versions/{version_id}", summary="Update a version")
@handle_exceptions
async def update_model_version(
    request: Request,
    model_id: uuid.UUID,
    version_id: uuid.UUID,
    body: ModelVersionUpdate,
    admin=Depends(get_current_admin),
    service: CatalogService = Depends(CatalogService.as_dependency),
):
    return success_response(data=await service.update_version(model_id, version_id, body))


@router.delete(
    "/models/{model_id}/versions/{version_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a version",
)
@handle_exceptions
async def delete_model_version(
    request: Request,
    model_id: uuid.UUID,
    version_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: CatalogService = Depends(CatalogService.as_dependency),
):
    await service.delete_version(model_id, version_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/models/{model_id}/versions/{version_id}/files", summary="Files of a version")
@handle_exceptions
async def list_model_files(
    request: Request,
    model_id: uuid.UUID,
    version_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: CatalogService = Depends(CatalogService.as_dependency),
):
    rows = await service.list_files(model_id, version_id)
    return success_response(data=rows, meta=paginated_meta(len(rows), 1, max(len(rows), 1)))


@router.post("/models/{model_id}/versions/{version_id}/verify", summary="Re-verify hashes")
@handle_exceptions
async def verify_model_version(
    request: Request,
    model_id: uuid.UUID,
    version_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: CatalogService = Depends(CatalogService.as_dependency),
):
    return success_response(data=await service.verify_version(model_id, version_id))


@router.get(
    "/models/{model_id}/versions/{version_id}/cache",
    summary="Where a version is cached",
)
@handle_exceptions
async def list_model_version_cache(
    request: Request,
    model_id: uuid.UUID,
    version_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: CatalogService = Depends(CatalogService.as_dependency),
):
    rows = await service.list_version_cache(model_id, version_id)
    return success_response(data=rows, meta=paginated_meta(len(rows), 1, max(len(rows), 1)))
