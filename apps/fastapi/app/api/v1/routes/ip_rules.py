"""
IP rule routes (admin.yaml: IP Rules tag).

GET    /ip-rules            list (scope, action, enabled, sort, page)
POST   /ip-rules            create (409 SELF_LOCKOUT if it would block the caller)   201
POST   /ip-rules/check      evaluate an IP against a scope
GET    /ip-rules/{rule_id}  get
PATCH  /ip-rules/{rule_id}  update
DELETE /ip-rules/{rule_id}  delete                                                  204
"""

import uuid
from typing import Optional

from app.core.logger import get_logger
from app.middlewares.session_verification import get_current_admin
from app.schemas.ip_rule_schema import (
    IpCheckRequest,
    IpCheckResult,
    IpRule,
    IpRuleAction,
    IpRuleCreate,
    IpRuleScope,
    IpRuleUpdate,
)
from app.services.security.client_ip import resolve_request_ip
from app.services.security.service import IpRuleService
from app.utils.api_wrapper import handle_exceptions, paginated_meta, success_response
from fastapi import APIRouter, Depends, Query, Request, Response, status

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["IP Rules"]


@router.get("/ip-rules", summary="List rules")
@handle_exceptions
async def list_ip_rules(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    sort: Optional[str] = Query(None),
    scope: Optional[IpRuleScope] = Query(None),
    action: Optional[IpRuleAction] = Query(None),
    enabled: Optional[bool] = Query(None),
    admin=Depends(get_current_admin),
    service: IpRuleService = Depends(IpRuleService.as_dependency),
):
    items, total = await service.list_rules(
        page=page,
        page_size=page_size,
        scope=scope.value if scope else None,
        action=action.value if action else None,
        enabled=enabled,
        sort=sort,
    )
    return success_response(
        data=[IpRule.model_validate(i) for i in items],
        meta=paginated_meta(total, page, page_size),
    )


@router.post("/ip-rules", summary="Create a rule", status_code=201)
@handle_exceptions
async def create_ip_rule(
    request: Request,
    payload: IpRuleCreate,
    admin=Depends(get_current_admin),
    service: IpRuleService = Depends(IpRuleService.as_dependency),
):
    created = await service.create_rule(
        payload.model_dump(mode="json") | {"expires_at": payload.expires_at},
        created_by=admin.id,
        caller_ip=resolve_request_ip(request),
    )
    return success_response(data=IpRule.model_validate(created))


@router.post("/ip-rules/check", summary="Evaluate an IP against the rules of a scope")
@handle_exceptions
async def check_ip_rule(
    request: Request,
    payload: IpCheckRequest,
    admin=Depends(get_current_admin),
    service: IpRuleService = Depends(IpRuleService.as_dependency),
):
    decision = await service.check(payload.ip, payload.scope.value)
    return success_response(data=IpCheckResult.model_validate(decision.as_dict()))


@router.get("/ip-rules/{rule_id}", summary="Get a rule")
@handle_exceptions
async def get_ip_rule(
    request: Request,
    rule_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: IpRuleService = Depends(IpRuleService.as_dependency),
):
    return success_response(data=IpRule.model_validate(await service.get_rule(rule_id)))


@router.patch("/ip-rules/{rule_id}", summary="Update a rule")
@handle_exceptions
async def update_ip_rule(
    request: Request,
    rule_id: uuid.UUID,
    payload: IpRuleUpdate,
    admin=Depends(get_current_admin),
    service: IpRuleService = Depends(IpRuleService.as_dependency),
):
    data = payload.model_dump(mode="json", exclude_unset=True)
    if "expires_at" in data:
        data["expires_at"] = payload.expires_at
    updated = await service.update_rule(rule_id, data, caller_ip=resolve_request_ip(request))
    return success_response(data=IpRule.model_validate(updated))


@router.delete(
    "/ip-rules/{rule_id}",
    summary="Delete a rule",
    status_code=status.HTTP_204_NO_CONTENT,
)
@handle_exceptions
async def delete_ip_rule(
    request: Request,
    rule_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: IpRuleService = Depends(IpRuleService.as_dependency),
):
    await service.delete_rule(rule_id, caller_ip=resolve_request_ip(request))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
