"""
IP rule evaluation and the fast per-request checker.

Semantics:

- block rules win over allow rules;
- any allow rule for a scope turns that scope into allow-list mode (an IP must
  match an allow rule to pass);
- ``GATEWAY_IP_ALLOWLIST`` is merged as implicit allow rules for ``api_layer``
  (and ``IP_ALLOWLIST`` / ``IP_BLOCKLIST`` likewise for ``admin``);
- disabled or expired rules are ignored (the snapshot excludes them).

Caching: the per-scope snapshot lives in Redis ``ip_rules:{scope}`` (TTL
IP_RULES_CACHE_TTL_SECONDS) and in a process-local copy refreshed at most
every IP_RULES_LOCAL_CACHE_SECONDS, so ``check_ip`` on the serving hot path
is a dict lookup plus one Redis GET every couple of seconds. Writers call
``invalidate_scope`` which rewrites the Redis snapshot immediately.

    from app.services.security.ip_rules import check_ip
    decision = await check_ip(client_ip, "api_layer")   # .allowed, .reason, .matched_rule
    allowed = await check_ip_allowed(client_ip, "api_layer")  # bool (serving adapter)
"""

from __future__ import annotations

import ipaddress
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.core.logger import get_logger
from app.core.settings import settings
from app.db.redis.client import get_redis, redis_key

logger = get_logger(__name__)

SCOPES = ("admin", "api_layer")

REASON_BLOCKED = "blocked_by_rule"
REASON_NOT_IN_ALLOWLIST = "not_in_allowlist"
REASON_ALLOWED = "allowed_by_rule"
REASON_NO_RULES = "no_rules"
REASON_ENV_ALLOWLIST = "env_allowlist"


@dataclass
class IpDecision:
    ip: str
    scope: str
    allowed: bool
    reason: str
    matched_rule: Optional[Dict[str, Any]] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ip": self.ip,
            "scope": self.scope,
            "allowed": self.allowed,
            "reason": self.reason,
            "matched_rule": self.matched_rule,
        }


@dataclass
class _Compiled:
    """Snapshot with pre-parsed networks."""

    blocks: List[Tuple[Any, Optional[Dict[str, Any]]]] = field(default_factory=list)
    allows: List[Tuple[Any, Optional[Dict[str, Any]]]] = field(default_factory=list)
    env_allows: List[Any] = field(default_factory=list)
    env_blocks: List[Any] = field(default_factory=list)


def parse_network(value: str):
    """ipaddress network for an IP or CIDR string, None when invalid."""
    try:
        return ipaddress.ip_network(value.strip(), strict=False)
    except ValueError:
        return None


def env_lists_for(scope: str) -> Tuple[List[str], List[str]]:
    """(implicit allows, implicit blocks) from .env for a scope."""
    if scope == "api_layer":
        return settings.get_gateway_ip_allowlist(), []
    if scope == "admin":
        return settings.get_ip_allowlist(), settings.get_ip_blocklist()
    return [], []


def _expired(value: Any, now: datetime) -> bool:
    """True when a snapshot rule's expires_at (ISO string or datetime) has passed."""
    if not value:
        return False
    try:
        at = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except ValueError:
        return False
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return at <= now


def compile_rules(
    rules: List[Dict[str, Any]], env_allow: List[str], env_block: List[str]
) -> _Compiled:
    compiled = _Compiled()
    now = datetime.now(timezone.utc)
    for rule in rules:
        if not rule.get("enabled", True) or _expired(rule.get("expires_at"), now):
            continue
        net = parse_network(str(rule.get("cidr", "")))
        if net is None:
            continue
        target = compiled.blocks if rule.get("action") == "block" else compiled.allows
        target.append((net, rule))
    compiled.env_allows = [n for n in (parse_network(v) for v in env_allow) if n is not None]
    compiled.env_blocks = [n for n in (parse_network(v) for v in env_block) if n is not None]
    return compiled


def evaluate(ip: str, scope: str, compiled: _Compiled) -> IpDecision:
    """Pure decision for one IP against a compiled snapshot."""
    try:
        addr = ipaddress.ip_address(ip.strip())
    except (ValueError, AttributeError):
        addr = None

    def _in(net) -> bool:
        return addr is not None and addr.version == net.version and addr in net

    for net, rule in compiled.blocks:
        if _in(net):
            return IpDecision(ip, scope, False, REASON_BLOCKED, rule)
    for net in compiled.env_blocks:
        if _in(net):
            return IpDecision(ip, scope, False, REASON_BLOCKED, None)

    allowlist_mode = bool(compiled.allows or compiled.env_allows)
    if not allowlist_mode:
        return IpDecision(ip, scope, True, REASON_NO_RULES, None)
    for net, rule in compiled.allows:
        if _in(net):
            return IpDecision(ip, scope, True, REASON_ALLOWED, rule)
    for net in compiled.env_allows:
        if _in(net):
            return IpDecision(ip, scope, True, REASON_ENV_ALLOWLIST, None)
    return IpDecision(ip, scope, False, REASON_NOT_IN_ALLOWLIST, None)


# ==========================================
# Cached checker (Redis snapshot + process-local copy)
# ==========================================


def _snapshot_key(scope: str) -> str:
    return redis_key("ip_rules", scope)


class IpRuleChecker:
    """Process-local cache over the Redis snapshot, loading from Postgres on a miss."""

    def __init__(self) -> None:
        self._local: Dict[str, Tuple[float, _Compiled]] = {}

    def reset(self) -> None:
        self._local.clear()

    async def _load_from_db(self, scope: str) -> List[Dict[str, Any]]:
        from app.db.postgres.connection import get_session_maker
        from app.db.postgres.repositories.ip_rule_repository import IpRuleRepository

        async with get_session_maker()() as session:
            return await IpRuleRepository(session).snapshot(scope)

    async def _read_snapshot(self, scope: str) -> List[Dict[str, Any]]:
        redis = None
        try:
            redis = await get_redis()
            cached = await redis.get(_snapshot_key(scope))
            if cached is not None:
                return json.loads(cached)
        except Exception as exc:  # noqa: BLE001 - Redis optional
            logger.warning(f"ip_rules cache read failed for {scope}: {exc}")
        rules = await self._load_from_db(scope)
        if redis is not None:
            try:
                await redis.set(
                    _snapshot_key(scope),
                    json.dumps(rules),
                    ex=int(settings.IP_RULES_CACHE_TTL_SECONDS),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"ip_rules cache write failed for {scope}: {exc}")
        return rules

    async def compiled_for(self, scope: str, *, force: bool = False) -> _Compiled:
        now = time.monotonic()
        entry = self._local.get(scope)
        if not force and entry and now - entry[0] < float(settings.IP_RULES_LOCAL_CACHE_SECONDS):
            return entry[1]
        try:
            rules = await self._read_snapshot(scope)
        except Exception as exc:  # noqa: BLE001 - fail open on infra outage, keep last copy
            logger.error(f"ip_rules snapshot unavailable for {scope}: {exc}")
            if entry:
                return entry[1]
            rules = []
        env_allow, env_block = env_lists_for(scope)
        compiled = compile_rules(rules, env_allow, env_block)
        self._local[scope] = (now, compiled)
        return compiled

    async def check(self, ip: str, scope: str) -> IpDecision:
        if scope not in SCOPES:
            raise ValueError(f"Unknown ip rule scope {scope!r}")
        return evaluate(ip, scope, await self.compiled_for(scope))

    async def refresh(self, scope: str, rules: Optional[List[Dict[str, Any]]] = None) -> None:
        """Rewrite the Redis snapshot (from ``rules`` or Postgres) and drop the local copy."""
        if rules is None:
            rules = await self._load_from_db(scope)
        try:
            redis = await get_redis()
            await redis.set(
                _snapshot_key(scope),
                json.dumps(rules),
                ex=int(settings.IP_RULES_CACHE_TTL_SECONDS),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"ip_rules cache refresh failed for {scope}: {exc}")
        env_allow, env_block = env_lists_for(scope)
        self._local[scope] = (
            time.monotonic(),
            compile_rules(rules, env_allow, env_block),
        )


checker = IpRuleChecker()


async def check_ip(ip: str, scope: str) -> IpDecision:
    """Fast path for middlewares and the API layer: cached, never raises on infra errors."""
    return await checker.check(ip, scope)


async def check_ip_allowed(ip: Optional[str], scope: str = "api_layer") -> bool:
    """
    Boolean form for the API layer (the serving ``check_client_ip`` adapter): True when
    ``ip`` may call ``scope``. Same cache and semantics as ``check_ip``.
    """
    return (await checker.check(ip or "", scope)).allowed


async def invalidate_scope(scope: str, rules: Optional[List[Dict[str, Any]]] = None) -> None:
    """Called by the CRUD service after every change."""
    await checker.refresh(scope, rules)


async def warm_cache() -> None:
    for scope in SCOPES:
        await checker.compiled_for(scope, force=True)
