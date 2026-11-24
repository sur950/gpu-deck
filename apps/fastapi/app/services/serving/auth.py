"""
API-key and IP authentication for the serving API (serving.md §3).

Thin adapter over the implementations: ``app.services.api_keys`` and
``app.services.security`` are imported lazily; when they are not available
(parallel build) the same lookup is implemented here — SHA-256 of the key
-> Redis ``apikey:{hash}`` (``API_KEY_CACHE_TTL_SECONDS``) -> ``api_keys``
table — and IP rules from ``ip_rules`` scope ``api_layer`` merged with the
``GATEWAY_IP_ALLOWLIST`` / ``SERVING_IP_ALLOWLIST`` env lists
(block wins; any allow rule switches the scope to
allow-list mode).
"""

from __future__ import annotations

import hashlib
import importlib
import ipaddress
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from app.core.logger import get_logger
from app.core.settings import settings
from app.services.serving.errors import ServingError
from fastapi import Request

logger = get_logger(__name__)

_B1_VERIFY: Optional[Callable[..., Any]] = None
_B1_IP_CHECK: Optional[Callable[..., Any]] = None
_resolved = False


@dataclass
class ApiKeyContext:
    id: str
    name: str
    status: str = "active"
    expires_at: Optional[datetime] = None
    metadata: Optional[Dict[str, Any]] = None

    @property
    def expired(self) -> bool:
        return bool(self.expires_at and self.expires_at <= datetime.now(timezone.utc))


def _env_setting(name: str, default: Any = None) -> Any:
    """Settings declared by another domain or plain env, whichever exists."""
    value = getattr(settings, name, None)
    if value is None:
        value = os.environ.get(name)
    return default if value in (None, "") else value


def _resolve_core_auth() -> None:
    """Pick up verify_api_key / IP checker if they exist (once per process)."""
    global _B1_VERIFY, _B1_IP_CHECK, _resolved
    if _resolved:
        return
    _resolved = True
    for mod_name, attr in (
        ("app.services.api_keys.service", "verify_api_key"),
        ("app.services.api_keys", "verify_api_key"),
    ):
        try:
            mod = importlib.import_module(mod_name)
        except Exception:  # noqa: BLE001
            continue
        fn = getattr(mod, attr, None)
        if callable(fn):
            _B1_VERIFY = fn
            logger.info(f"serving auth: using {mod_name}.{attr}")
            break
    for mod_name, attr in (
        ("app.services.security.ip_rules", "check_ip"),
        ("app.services.security.service", "check_ip_allowed"),
        ("app.services.security", "check_ip_allowed"),
    ):
        try:
            mod = importlib.import_module(mod_name)
        except Exception:  # noqa: BLE001
            continue
        fn = getattr(mod, attr, None)
        if callable(fn):
            _B1_IP_CHECK = fn
            logger.info(f"serving auth: using {mod_name}.{attr}")
            break


def set_verifiers_for_tests(
    verify: Optional[Callable[..., Any]], ip_check: Optional[Callable[..., Any]] = None
) -> None:
    global _B1_VERIFY, _B1_IP_CHECK, _resolved
    _B1_VERIFY = verify
    _B1_IP_CHECK = ip_check
    _resolved = True


# ---------------------------------------------------------------------------
# API key
# ---------------------------------------------------------------------------


def extract_api_key(request: Request) -> Optional[str]:
    auth = request.headers.get("authorization")
    if auth:
        scheme, _, token = auth.partition(" ")
        if scheme.lower() == "bearer" and token.strip():
            return token.strip()
    for header in ("x-api-key",):
        value = request.headers.get(header)
        if value:
            return value.strip()
    return None


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


async def _lookup_key_fallback(key: str) -> ApiKeyContext:
    """Redis cache -> api_keys table."""
    from app.db.redis.client import get_redis, redis_key

    key_hash = _hash_key(key)
    ttl = int(_env_setting("API_KEY_CACHE_TTL_SECONDS", 60))
    cache_key = redis_key("apikey", key_hash)
    client = None
    try:
        client = await get_redis()
        cached = await client.get(cache_key)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"apikey cache read skipped: {exc}")
        cached = None
    if cached:
        data = json.loads(cached)
        if data.get("missing"):
            raise ServingError(401, "invalid_api_key", "invalid API key")
        return _ctx_from_dict(data)

    row = await _db_lookup(key_hash)
    if row is None:
        if client is not None:
            try:
                await client.set(cache_key, json.dumps({"missing": True}), ex=ttl)
            except Exception:  # noqa: BLE001
                pass
        raise ServingError(401, "invalid_api_key", "invalid API key")
    if client is not None:
        try:
            await client.set(cache_key, json.dumps(row, default=str), ex=ttl)
        except Exception:  # noqa: BLE001
            pass
    return _ctx_from_dict(row)


def _ctx_from_dict(data: Dict[str, Any]) -> ApiKeyContext:
    expires = data.get("expires_at")
    if isinstance(expires, str):
        try:
            expires = datetime.fromisoformat(expires)
        except ValueError:
            expires = None
    if isinstance(expires, (int, float)):
        expires = datetime.fromtimestamp(float(expires), tz=timezone.utc)
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return ApiKeyContext(
        id=str(data.get("id")),
        name=str(data.get("name") or ""),
        status=str(data.get("status") or "active"),
        expires_at=expires,
        metadata=data.get("metadata") or {},
    )


async def _db_lookup(key_hash: str) -> Optional[Dict[str, Any]]:
    from app.db.postgres.connection import get_session_maker
    from sqlalchemy import text

    maker = get_session_maker()
    async with maker() as session:
        result = await session.execute(
            text(
                "SELECT id, name, status, expires_at, metadata FROM api_keys "
                "WHERE key_hash = :h AND deleted_at IS NULL"
            ),
            {"h": key_hash},
        )
        row = result.mappings().first()
        await session.rollback()
    if row is None:
        return None
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "status": row["status"],
        "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None,
        "metadata": row["metadata"] or {},
    }


async def verify_api_key(request: Request) -> ApiKeyContext:
    """Resolve and validate the request's API key; raises ServingError on rejection."""
    _resolve_core_auth()
    key = extract_api_key(request)
    if not key:
        raise ServingError(401, "invalid_api_key", "missing API key")
    if _B1_VERIFY is not None:
        try:
            result = _B1_VERIFY(key)
            if hasattr(result, "__await__"):
                result = await result
        except ServingError:
            raise
        except Exception as exc:  # noqa: BLE001
            code = (
                getattr(exc, "wire_code", None)
                or getattr(exc, "error_code", None)
                or "invalid_api_key"
            )
            status = getattr(exc, "status_code", 401)
            message = getattr(exc, "detail", None) or str(exc) or "invalid API key"
            if isinstance(message, dict):
                message = message.get("message") or "invalid API key"
            raise ServingError(int(status), str(code).lower(), str(message))
        if result is None:
            raise ServingError(401, "invalid_api_key", "invalid API key")
        ctx = result if isinstance(result, ApiKeyContext) else _ctx_from_any(result)
    else:
        ctx = await _lookup_key_fallback(key)
    if ctx.status != "active":
        raise ServingError(401, f"api_key_{ctx.status}", f"API key is {ctx.status}")
    if ctx.expired:
        raise ServingError(401, "api_key_expired", "API key has expired")
    return ctx


def _ctx_from_any(obj: Any) -> ApiKeyContext:
    if isinstance(obj, dict):
        return _ctx_from_dict(obj)
    return _ctx_from_dict(
        {
            "id": getattr(obj, "id", None),
            "name": getattr(obj, "name", ""),
            "status": getattr(obj, "status", "active"),
            "expires_at": getattr(obj, "expires_at", None),
            "metadata": getattr(obj, "metadata", None) or getattr(obj, "metadata_", None),
        }
    )


# ---------------------------------------------------------------------------
# IP rules (scope api_layer)
# ---------------------------------------------------------------------------

_ip_rules_cache: Dict[str, Any] = {"at": 0.0, "rules": None}


def _csv(value: Any) -> List[str]:
    if not value:
        return []
    return [v.strip() for v in str(value).split(",") if v.strip()]


def _ip_in(ip: ipaddress._BaseAddress, cidrs: List[str]) -> bool:
    for entry in cidrs:
        try:
            if "/" in entry:
                if ip in ipaddress.ip_network(entry, strict=False):
                    return True
            elif ip == ipaddress.ip_address(entry):
                return True
        except ValueError:
            continue
    return False


async def _load_ip_rules() -> Dict[str, List[str]]:
    ttl = int(_env_setting("IP_RULES_CACHE_TTL_SECONDS", 30))
    now = time.monotonic()
    if _ip_rules_cache["rules"] is not None and now - _ip_rules_cache["at"] < ttl:
        return _ip_rules_cache["rules"]
    rules: Dict[str, List[str]] = {"allow": [], "block": []}
    try:
        from app.db.postgres.connection import get_session_maker
        from sqlalchemy import text

        async with get_session_maker()() as session:
            result = await session.execute(
                text(
                    "SELECT action, cidr::text AS cidr FROM ip_rules WHERE scope = 'api_layer' "
                    "AND enabled AND deleted_at IS NULL "
                    "AND (expires_at IS NULL OR expires_at > NOW())"
                )
            )
            for row in result.mappings():
                rules[str(row["action"])].append(str(row["cidr"]))
            await session.rollback()
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"ip_rules load skipped: {exc}")
    _ip_rules_cache["rules"] = rules
    _ip_rules_cache["at"] = now
    return rules


def evaluate_ip(ip_str: Optional[str], allow: List[str], block: List[str]) -> bool:
    """Pure rule evaluation: block wins; any allow entry -> allow-list mode."""
    if not ip_str:
        return not allow and not block
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if _ip_in(ip, block):
        return False
    if allow:
        return _ip_in(ip, allow)
    return True


async def check_client_ip(request: Request) -> None:
    """Reject blocked source IPs with 403 ip_blocked (checked before key lookup)."""
    _resolve_core_auth()
    ip = getattr(request.state, "client_ip", None) or (
        request.client.host if request.client else None
    )
    if _B1_IP_CHECK is not None:
        try:
            result = _B1_IP_CHECK(ip, "api_layer")
            if hasattr(result, "__await__"):
                result = await result
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"ip check failed open: {exc}")
            return
        allowed = getattr(result, "allowed", result)
        if allowed is False:
            raise ServingError(403, "ip_blocked", "source IP is not allowed")
        return
    env_allow = _csv(_env_setting("GATEWAY_IP_ALLOWLIST")) + _csv(
        getattr(settings, "SERVING_IP_ALLOWLIST", "")
    )
    rules = await _load_ip_rules()
    if not evaluate_ip(ip, rules["allow"] + env_allow, rules["block"]):
        raise ServingError(403, "ip_blocked", "source IP is not allowed")


def reset_ip_rules_cache() -> None:
    _ip_rules_cache["rules"] = None
    _ip_rules_cache["at"] = 0.0
