"""
Price plan routes (admin.yaml: Price Plans tag).

GET    /price-plans             list
POST   /price-plans             create (201)
GET    /price-plans/{plan_id}   get
PATCH  /price-plans/{plan_id}   update (affects future usage only)
DELETE /price-plans/{plan_id}   soft-delete, detaches from clusters/nodes (204)

Plans are attached to a cluster / node via their ``price_plan_id``.
"""

import uuid
from typing import Optional

from app.core.logger import get_logger
from app.middlewares.session_verification import get_current_admin
from app.schemas.price_plan_schema import PricePlan, PricePlanCreate, PricePlanUpdate
from app.services.billing.service import PricePlanService
from app.utils.api_wrapper import handle_exceptions, paginated_meta, success_response
from fastapi import APIRouter, Depends, Query, Request, Response, status

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Price Plans"]


@router.get("/price-plans", summary="List plans")
@handle_exceptions
async def list_price_plans(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    search: Optional[str] = Query(None),
    sort: Optional[str] = Query(None),
    admin=Depends(get_current_admin),
    service: PricePlanService = Depends(PricePlanService.as_dependency),
):
    items, total = await service.list_plans(page, page_size, search, sort)
    return success_response(
        data=[PricePlan.model_validate(i).model_dump(mode="json") for i in items],
        meta=paginated_meta(total, page, page_size),
    )


@router.post("/price-plans", summary="Create a plan", status_code=201)
@handle_exceptions
async def create_price_plan(
    request: Request,
    payload: PricePlanCreate,
    admin=Depends(get_current_admin),
    service: PricePlanService = Depends(PricePlanService.as_dependency),
):
    created = await service.create_plan(payload.model_dump(), created_by=getattr(admin, "id", None))
    return success_response(data=PricePlan.model_validate(created).model_dump(mode="json"))


@router.get("/price-plans/{plan_id}", summary="Get a plan")
@handle_exceptions
async def get_price_plan(
    request: Request,
    plan_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: PricePlanService = Depends(PricePlanService.as_dependency),
):
    return success_response(
        data=PricePlan.model_validate(await service.get_plan(plan_id)).model_dump(mode="json")
    )


@router.patch("/price-plans/{plan_id}", summary="Update a plan (affects future usage events only)")
@handle_exceptions
async def update_price_plan(
    request: Request,
    plan_id: uuid.UUID,
    payload: PricePlanUpdate,
    admin=Depends(get_current_admin),
    service: PricePlanService = Depends(PricePlanService.as_dependency),
):
    updated = await service.update_plan(plan_id, payload.model_dump(exclude_unset=True))
    return success_response(data=PricePlan.model_validate(updated).model_dump(mode="json"))


@router.delete(
    "/price-plans/{plan_id}",
    summary="Soft-delete (detaches from clusters/nodes)",
    status_code=status.HTTP_204_NO_CONTENT,
)
@handle_exceptions
async def delete_price_plan(
    request: Request,
    plan_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: PricePlanService = Depends(PricePlanService.as_dependency),
):
    await service.delete_plan(plan_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
