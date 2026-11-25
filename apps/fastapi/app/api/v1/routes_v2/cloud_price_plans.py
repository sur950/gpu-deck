"""
Cloud list-price plan route, deferred to v2 with price plans (not mounted; see ``__init__``).

POST /cloud/nodes/{cloud_node_id}/apply-price-plan   create/attach the list-price plan
"""

import uuid

from app.core.logger import get_logger
from app.middlewares.session_verification import get_current_admin
from app.services.cloud.service import CloudService
from app.utils.api_wrapper import handle_exceptions, success_response
from fastapi import APIRouter, Depends, Request

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Cloud"]


@router.post(
    "/cloud/nodes/{cloud_node_id}/apply-price-plan",
    summary="Create/attach a price plan from the provider list price",
)
@handle_exceptions
async def apply_cloud_price_plan(
    request: Request,
    cloud_node_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: CloudService = Depends(CloudService.as_dependency),
):
    return success_response(
        data=await service.apply_price_plan(cloud_node_id, getattr(admin, "id", None))
    )
