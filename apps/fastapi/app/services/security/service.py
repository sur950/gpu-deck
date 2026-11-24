"""
IpRuleService: CRUD for ip_rules with self-lockout protection and cache
invalidation on every change.
"""

import uuid
from typing import Any, Dict, List, Optional, Tuple

from app.core.exceptions import (
    ConflictError,
    DuplicateResourceError,
    ResourceNotFoundError,
)
from app.core.logger import get_logger
from app.db.postgres.connection import get_db
from app.db.postgres.repositories.ip_rule_repository import IpRuleRepository
from app.services.audit import audit
from app.services.security.ip_rules import (
    SCOPES,
    IpDecision,
    compile_rules,
    env_lists_for,
    evaluate,
    invalidate_scope,
    parse_network,
)
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


def _self_lockout() -> ConflictError:
    exc = ConflictError("This rule would block your own IP address")
    exc.error_code = "SELF_LOCKOUT"
    return exc


class IpRuleService:
    """Business logic for IP allow/block rules."""

    def __init__(self, repo: IpRuleRepository) -> None:
        self.repo = repo

    @staticmethod
    async def as_dependency(
        request: Request, db: AsyncSession = Depends(get_db)
    ) -> "IpRuleService":
        return IpRuleService(IpRuleRepository(db))

    # ---------- reads ----------

    async def list_rules(
        self,
        *,
        page: int,
        page_size: int,
        scope: Optional[str] = None,
        action: Optional[str] = None,
        enabled: Optional[bool] = None,
        sort: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        rows, total = await self.repo.search(
            skip=(page - 1) * page_size,
            limit=page_size,
            scope=scope,
            action=action,
            enabled=enabled,
            sort=sort,
        )
        return [self.repo.to_dict(r) for r in rows], total

    async def get_rule(self, rule_id: uuid.UUID) -> Dict[str, Any]:
        row = await self.repo.get_active(rule_id)
        if row is None:
            raise ResourceNotFoundError(resource_type="IP rule")
        return self.repo.to_dict(row)

    async def check(self, ip: str, scope: str) -> IpDecision:
        """Evaluate against the live database rules (not the cache)."""
        if scope not in SCOPES:
            raise ResourceNotFoundError(resource_type="Scope")
        rules = await self.repo.snapshot(scope)
        env_allow, env_block = env_lists_for(scope)
        return evaluate(ip, scope, compile_rules(rules, env_allow, env_block))

    # ---------- writes ----------

    async def _guard_self_lockout(
        self, scope: str, caller_ip: Optional[str], rules_after: List[Dict[str, Any]]
    ) -> None:
        if scope != "admin" or not caller_ip:
            return
        env_allow, env_block = env_lists_for(scope)
        decision = evaluate(caller_ip, scope, compile_rules(rules_after, env_allow, env_block))
        if not decision.allowed:
            raise _self_lockout()

    @staticmethod
    def _normalize_cidr(cidr: str) -> str:
        net = parse_network(cidr)
        if net is None:
            from app.core.exceptions import ValidationError

            raise ValidationError(f"Invalid IP or CIDR: {cidr!r}")
        return str(net)

    async def create_rule(
        self,
        data: Dict[str, Any],
        *,
        created_by: Optional[uuid.UUID],
        caller_ip: Optional[str],
    ) -> Dict[str, Any]:
        cidr = self._normalize_cidr(data["cidr"])
        scope, action = data["scope"], data["action"]
        if await self.repo.find_duplicate(scope, action, cidr):
            raise DuplicateResourceError(resource_type="IP rule", field="cidr")

        candidate = {
            "id": "new",
            "action": action,
            "scope": scope,
            "cidr": cidr,
            "enabled": data.get("enabled", True),
        }
        if candidate["enabled"]:
            current = await self.repo.snapshot(scope)
            await self._guard_self_lockout(scope, caller_ip, current + [candidate])

        row = await self.repo.create(
            action=action,
            scope=scope,
            cidr=cidr,
            description=data.get("description"),
            enabled=data.get("enabled", True),
            expires_at=data.get("expires_at"),
            created_by=created_by,
        )
        result = self.repo.to_dict(row)
        await invalidate_scope(scope, await self.repo.snapshot(scope))
        audit.record(
            "ip_rule.created",
            resource_type="ip_rule",
            resource_id=row.id,
            resource_name=f"{action} {cidr} ({scope})",
            after_state=result,
        )
        return result

    async def update_rule(
        self, rule_id: uuid.UUID, data: Dict[str, Any], *, caller_ip: Optional[str]
    ) -> Dict[str, Any]:
        row = await self.repo.get_active(rule_id)
        if row is None:
            raise ResourceNotFoundError(resource_type="IP rule")
        before = self.repo.to_dict(row)
        changes = {k: v for k, v in data.items() if v is not None or k in ("expires_at",)}
        if "cidr" in changes:
            changes["cidr"] = self._normalize_cidr(changes["cidr"])
        scope = changes.get("scope", row.scope)
        action = changes.get("action", row.action)
        cidr = changes.get("cidr", str(row.cidr))
        if await self.repo.find_duplicate(scope, action, cidr, exclude_id=rule_id):
            raise DuplicateResourceError(resource_type="IP rule", field="cidr")

        merged = {**before, **changes, "cidr": cidr, "scope": scope, "action": action}
        for affected in {row.scope, scope}:
            others = [r for r in await self.repo.snapshot(affected) if r["id"] != str(rule_id)]
            if affected == scope and merged.get("enabled", True):
                others.append(merged)
            await self._guard_self_lockout(affected, caller_ip, others)

        await self.repo.update(rule_id, **changes)
        row = await self.repo.get_active(rule_id)
        result = self.repo.to_dict(row)
        for affected in {before["scope"], scope}:
            await invalidate_scope(affected, await self.repo.snapshot(affected))
        audit.record(
            "ip_rule.updated",
            resource_type="ip_rule",
            resource_id=rule_id,
            resource_name=f"{result['action']} {result['cidr']} ({result['scope']})",
            before_state=before,
            after_state=result,
        )
        return result

    async def delete_rule(self, rule_id: uuid.UUID, *, caller_ip: Optional[str]) -> None:
        row = await self.repo.get_active(rule_id)
        if row is None:
            raise ResourceNotFoundError(resource_type="IP rule")
        before = self.repo.to_dict(row)
        remaining = [r for r in await self.repo.snapshot(row.scope) if r["id"] != str(rule_id)]
        await self._guard_self_lockout(row.scope, caller_ip, remaining)
        await self.repo.soft_delete(rule_id)
        await invalidate_scope(row.scope, remaining)
        audit.record(
            "ip_rule.deleted",
            resource_type="ip_rule",
            resource_id=rule_id,
            resource_name=f"{before['action']} {before['cidr']} ({before['scope']})",
            before_state=before,
        )
