"""
Rules engine routes (admin.yaml: Rules tag).

GET    /rules                     list (type, enabled, search, sort, page)
POST   /rules                     create (params validated against libs/contracts/rules)   201
GET    /rules/schemas             the rule / params / target / layout JSON Schemas verbatim
GET    /rules/{rule_id}           get
PATCH  /rules/{rule_id}           update
DELETE /rules/{rule_id}           delete                                                   204
POST   /rules/{rule_id}/enable    enable
POST   /rules/{rule_id}/disable   disable
POST   /rules/{rule_id}/dry-run   evaluate now without acting (works for disabled rules)
POST   /rules/{rule_id}/run       evaluate now and act (enabled rules)
GET    /rules/{rule_id}/runs      history of one rule
GET    /rule-runs                 history of every rule
GET    /rule-runs/{run_id}        one run
"""

import uuid
from datetime import datetime
from typing import Optional

from app.core.logger import get_logger
from app.core.rate_limiter import rate_limit
from app.middlewares.session_verification import get_current_admin
from app.schemas.rules_schema import RuleCreate, RuleRunOutcome, RuleType, RuleUpdate
from app.services.rules.service import RuleService
from app.utils.api_wrapper import handle_exceptions, paginated_meta, success_response
from fastapi import APIRouter, Depends, Query, Request, Response, status

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Rules"]


@router.get("/rules", summary="List rules")
@handle_exceptions
async def list_rules(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    search: Optional[str] = Query(None),
    sort: Optional[str] = Query(None),
    type: Optional[RuleType] = Query(None),
    enabled: Optional[bool] = Query(None),
    admin=Depends(get_current_admin),
    service: RuleService = Depends(RuleService.as_dependency),
):
    items, total = await service.list_rules(
        page=page,
        page_size=page_size,
        search=search,
        sort=sort,
        rule_type=type.value if type else None,
        enabled=enabled,
    )
    return success_response(data=items, meta=paginated_meta(total, page, page_size))


@router.post("/rules", summary="Create a rule", status_code=201)
@handle_exceptions
async def create_rule(
    request: Request,
    payload: RuleCreate,
    admin=Depends(get_current_admin),
    service: RuleService = Depends(RuleService.as_dependency),
):
    return success_response(data=await service.create_rule(payload, created_by=admin.id))


@router.get("/rules/schemas", summary="Rule JSON Schemas (form definitions)")
@handle_exceptions
async def get_rule_schemas(
    request: Request,
    admin=Depends(get_current_admin),
):
    return success_response(data=RuleService.schemas())


@router.get("/rules/{rule_id}", summary="Get a rule")
@handle_exceptions
async def get_rule(
    request: Request,
    rule_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: RuleService = Depends(RuleService.as_dependency),
):
    return success_response(data=await service.get_rule(rule_id))


@router.patch("/rules/{rule_id}", summary="Update a rule")
@handle_exceptions
async def update_rule(
    request: Request,
    rule_id: uuid.UUID,
    payload: RuleUpdate,
    admin=Depends(get_current_admin),
    service: RuleService = Depends(RuleService.as_dependency),
):
    return success_response(data=await service.update_rule(rule_id, payload))


@router.delete("/rules/{rule_id}", summary="Delete a rule", status_code=status.HTTP_204_NO_CONTENT)
@handle_exceptions
async def delete_rule(
    request: Request,
    rule_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: RuleService = Depends(RuleService.as_dependency),
):
    await service.delete_rule(rule_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/rules/{rule_id}/enable", summary="Enable a rule")
@handle_exceptions
async def enable_rule(
    request: Request,
    rule_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: RuleService = Depends(RuleService.as_dependency),
):
    return success_response(data=await service.set_enabled(rule_id, True))


@router.post("/rules/{rule_id}/disable", summary="Disable a rule")
@handle_exceptions
async def disable_rule(
    request: Request,
    rule_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: RuleService = Depends(RuleService.as_dependency),
):
    return success_response(data=await service.set_enabled(rule_id, False))


@router.post("/rules/{rule_id}/dry-run", summary="Evaluate now without acting")
@rate_limit("30/minute")
@handle_exceptions
async def dry_run_rule(
    request: Request,
    rule_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: RuleService = Depends(RuleService.as_dependency),
):
    return success_response(data=await service.evaluate_now(rule_id, dry_run=True))


@router.post("/rules/{rule_id}/run", summary="Evaluate now and act")
@rate_limit("10/minute")
@handle_exceptions
async def run_rule_now(
    request: Request,
    rule_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: RuleService = Depends(RuleService.as_dependency),
):
    return success_response(data=await service.evaluate_now(rule_id, dry_run=False))


@router.get("/rules/{rule_id}/runs", summary="Run history of one rule")
@handle_exceptions
async def list_rule_runs(
    request: Request,
    rule_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    from_: Optional[datetime] = Query(None, alias="from"),
    to: Optional[datetime] = Query(None),
    outcome: Optional[RuleRunOutcome] = Query(None),
    fired: Optional[bool] = Query(None),
    admin=Depends(get_current_admin),
    service: RuleService = Depends(RuleService.as_dependency),
):
    items, total = await service.list_rule_runs(
        rule_id,
        page=page,
        page_size=page_size,
        from_ts=from_,
        to_ts=to,
        outcome=outcome.value if outcome else None,
        fired=fired,
    )
    return success_response(data=items, meta=paginated_meta(total, page, page_size))


@router.get("/rule-runs", summary="Run history of every rule")
@handle_exceptions
async def list_all_rule_runs(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    from_: Optional[datetime] = Query(None, alias="from"),
    to: Optional[datetime] = Query(None),
    rule_type: Optional[RuleType] = Query(None),
    outcome: Optional[RuleRunOutcome] = Query(None),
    fired: Optional[bool] = Query(None),
    deployment_id: Optional[uuid.UUID] = Query(None),
    admin=Depends(get_current_admin),
    service: RuleService = Depends(RuleService.as_dependency),
):
    items, total = await service.list_runs(
        page=page,
        page_size=page_size,
        from_ts=from_,
        to_ts=to,
        rule_type=rule_type.value if rule_type else None,
        outcome=outcome.value if outcome else None,
        fired=fired,
        deployment_id=deployment_id,
    )
    return success_response(data=items, meta=paginated_meta(total, page, page_size))


@router.get("/rule-runs/{run_id}", summary="Get one rule run")
@handle_exceptions
async def get_rule_run(
    request: Request,
    run_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: RuleService = Depends(RuleService.as_dependency),
):
    return success_response(data=await service.get_run(run_id))
