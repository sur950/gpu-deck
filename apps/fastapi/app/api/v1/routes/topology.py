"""
Topology route (admin.yaml: Topology tag).

GET /topology?cluster_id=&include_stopped=   clusters -> nodes -> devices -> instances + links
"""

import uuid
from typing import Optional

from app.core.logger import get_logger
from app.middlewares.session_verification import get_current_admin
from app.schemas.fleet_schema import Topology
from app.services.topology.service import TopologyService
from app.utils.api_wrapper import handle_exceptions, success_response
from fastapi import APIRouter, Depends, Query, Request

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Topology"]


@router.get("/topology", summary="Full topology with live state")
@handle_exceptions
async def get_topology(
    request: Request,
    cluster_id: Optional[uuid.UUID] = Query(None),
    include_stopped: bool = Query(False),
    admin=Depends(get_current_admin),
    service: TopologyService = Depends(TopologyService.as_dependency),
):
    data = await service.get(cluster_id, include_stopped)
    return success_response(data=Topology.model_validate(data))
