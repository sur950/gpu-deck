"""
Engine registry routes (admin.yaml tag "Engines").

GET        /engines
GET/PATCH  /engines/{engine_id}
GET/POST   /engines/{engine_id}/versions
GET/PATCH/DELETE /engines/{engine_id}/versions/{version_id}
POST       /engines/compat-check
GET        /engines/compat-checks
POST       /engines/auto-pick
"""

import uuid
from typing import Optional

from app.core.logger import get_logger
from app.schemas.catalog_schema import ModelType
from app.schemas.engines_schema import (
    AutoPickRequest,
    CompatCheckRequest,
    CompatResult,
    DeviceVendor,
    EngineUpdate,
    EngineVersionCreate,
    EngineVersionUpdate,
)
from app.services.catalog.auth import get_current_admin
from app.services.engines.service import EngineService
from app.utils.api_wrapper import handle_exceptions, paginated_meta, success_response
from fastapi import APIRouter, Depends, Query, Request, Response, status

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Engines"]


@router.get("/engines", summary="List engines with their versions")
@handle_exceptions
async def list_engines(
    request: Request,
    enabled: Optional[bool] = Query(None),
    model_type: Optional[ModelType] = Query(None),
    vendor: Optional[DeviceVendor] = Query(None),
    admin=Depends(get_current_admin),
    service: EngineService = Depends(EngineService.as_dependency),
):
    rows = await service.list_engines(
        enabled=enabled,
        model_type=model_type.value if model_type else None,
        vendor=vendor.value if vendor else None,
    )
    return success_response(data=rows, meta=paginated_meta(len(rows), 1, max(len(rows), 1)))


# Static paths before /engines/{engine_id} so "compat-check" is not parsed as an id.
@router.post("/engines/compat-check", summary="Check model version × engine version(s)")
@handle_exceptions
async def run_compat_check(
    request: Request,
    body: CompatCheckRequest,
    admin=Depends(get_current_admin),
    service: EngineService = Depends(EngineService.as_dependency),
):
    rows = await service.run_compat_check(body)
    return success_response(data=rows, meta=paginated_meta(len(rows), 1, max(len(rows), 1)))


@router.get("/engines/compat-checks", summary="Past compatibility checks")
@handle_exceptions
async def list_compat_checks(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    model_version_id: Optional[uuid.UUID] = Query(None),
    engine_version_id: Optional[uuid.UUID] = Query(None),
    result: Optional[CompatResult] = Query(None),
    admin=Depends(get_current_admin),
    service: EngineService = Depends(EngineService.as_dependency),
):
    rows, total = await service.list_checks(
        page=page,
        page_size=page_size,
        model_version_id=model_version_id,
        engine_version_id=engine_version_id,
        result=result.value if result else None,
    )
    return success_response(data=rows, meta=paginated_meta(total, page, page_size))


@router.post("/engines/auto-pick", summary="Pick the best engine version for a model version")
@handle_exceptions
async def auto_pick_engine(
    request: Request,
    body: AutoPickRequest,
    admin=Depends(get_current_admin),
    service: EngineService = Depends(EngineService.as_dependency),
):
    return success_response(data=await service.auto_pick(body))


@router.get("/engines/{engine_id}", summary="Get an engine")
@handle_exceptions
async def get_engine(
    request: Request,
    engine_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: EngineService = Depends(EngineService.as_dependency),
):
    return success_response(data=await service.get_engine(engine_id))


@router.patch("/engines/{engine_id}", summary="Enable/disable, edit capabilities")
@handle_exceptions
async def update_engine(
    request: Request,
    engine_id: uuid.UUID,
    body: EngineUpdate,
    admin=Depends(get_current_admin),
    service: EngineService = Depends(EngineService.as_dependency),
):
    return success_response(data=await service.update_engine(engine_id, body))


@router.get("/engines/{engine_id}/versions", summary="Versions of an engine")
@handle_exceptions
async def list_engine_versions(
    request: Request,
    engine_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: EngineService = Depends(EngineService.as_dependency),
):
    rows = await service.list_versions(engine_id)
    return success_response(data=rows, meta=paginated_meta(len(rows), 1, max(len(rows), 1)))


@router.post(
    "/engines/{engine_id}/versions",
    status_code=status.HTTP_201_CREATED,
    summary="Add a version",
)
@handle_exceptions
async def create_engine_version(
    request: Request,
    engine_id: uuid.UUID,
    body: EngineVersionCreate,
    admin=Depends(get_current_admin),
    service: EngineService = Depends(EngineService.as_dependency),
):
    return success_response(data=await service.create_version(engine_id, body))


@router.get("/engines/{engine_id}/versions/{version_id}", summary="Get a version")
@handle_exceptions
async def get_engine_version(
    request: Request,
    engine_id: uuid.UUID,
    version_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: EngineService = Depends(EngineService.as_dependency),
):
    return success_response(data=await service.get_version(engine_id, version_id))


@router.patch("/engines/{engine_id}/versions/{version_id}", summary="Update a version")
@handle_exceptions
async def update_engine_version(
    request: Request,
    engine_id: uuid.UUID,
    version_id: uuid.UUID,
    body: EngineVersionUpdate,
    admin=Depends(get_current_admin),
    service: EngineService = Depends(EngineService.as_dependency),
):
    return success_response(data=await service.update_version(engine_id, version_id, body))


@router.delete(
    "/engines/{engine_id}/versions/{version_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a version",
)
@handle_exceptions
async def delete_engine_version(
    request: Request,
    engine_id: uuid.UUID,
    version_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: EngineService = Depends(EngineService.as_dependency),
):
    """409 ENGINE_VERSION_IN_USE while deployments reference it."""
    await service.delete_version(engine_id, version_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
