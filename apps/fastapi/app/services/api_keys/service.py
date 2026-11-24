"""
API keys: admin CRUD (plaintext shown once) and the serving-side verifier.

Admin side (``ApiKeyService``): create/list/get/update/delete/revoke/suspend/
activate/rotate. Every status change invalidates the cached verification
entry (Redis ``apikey:{sha256}`` + pub/sub ``apikeys:invalidate``) so the
serving API stops accepting the key within a second.

Serving side (``verify_api_key``): the function the serving API calls on every request::

    from app.services.api_keys.service import verify_api_key, ApiKeyPrincipal

    principal = await verify_api_key(raw_key)       # raises ApiKeyRejected (401) otherwise
    principal.id / .name / .status / .expires_at (aware datetime) / .metadata

Lookup order: process-local cache (API_KEY_LOCAL_CACHE_SECONDS, memory only)
-> Redis (API_KEY_CACHE_TTL_SECONDS) -> Postgres. Unknown keys are negatively
cached briefly. ``last_used_at`` is buffered in a Redis hash and flushed to
Postgres by the ``api_keys.flush_last_used`` job (never on the request path).
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from app.core.exceptions import (
    ApiKeyError,
    DuplicateResourceError,
    ResourceNotFoundError,
)
from app.core.logger import get_logger
from app.core.request_context import set_request_actor
from app.core.settings import settings
from app.db.postgres.connection import get_db, get_session_maker
from app.db.postgres.models.generated_models import ApiKeys
from app.db.postgres.repositories.api_key_repository import (
    ApiKeyRepository,
    hash_api_key,
)
from app.db.redis.client import get_redis, redis_key
from app.services.audit import audit
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

INVALIDATE_CHANNEL = "apikeys:invalidate"
LAST_USED_HASH = "apikeys:last_used"
NEGATIVE_TTL_SECONDS = 5
LAST_USED_THROTTLE_SECONDS = 30
LOCAL_CACHE_MAX_ENTRIES = 10000
_MISSING = {"_missing": True}


class ApiKeyRejected(ApiKeyError):
    """401 with the serving wire code (invalid_api_key, api_key_revoked, api_key_expired ...)."""

    def __init__(self, wire_code: str, message: str):
        super().__init__(message)
        self.wire_code = wire_code
        self.error_code = wire_code.upper()
        # serving_http_exception_handler uses a dict detail's "code" verbatim.
        self.detail = {"message": message, "code": wire_code}


@dataclass(frozen=True)
class ApiKeyPrincipal:
    """The verified key. ``expires_at`` is an aware datetime; ``expires_at_ts`` epoch seconds."""

    id: str
    name: str
    status: str
    expires_at_ts: Optional[float]
    metadata: Dict[str, Any]

    @property
    def expires_at(self) -> Optional[datetime]:
        if self.expires_at_ts is None:
            return None
        return datetime.fromtimestamp(self.expires_at_ts, tz=timezone.utc)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ApiKeyPrincipal":
        return cls(
            id=str(data["id"]),
            name=data.get("name") or "",
            status=data.get("status") or "revoked",
            expires_at_ts=_epoch_or_none(data.get("expires_at")),
            metadata=data.get("metadata") or {},
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "expires_at": self.expires_at_ts,
            "metadata": self.metadata,
        }


def _epoch_or_none(value: Any) -> Optional[float]:
    """Cached entries store epoch seconds; tolerate ISO strings / datetimes too."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _cache_key(key_hash: str) -> str:
    return redis_key("apikey", key_hash)


def _row_to_principal_dict(row: ApiKeys) -> Dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "status": row.status,
        "expires_at": row.expires_at.timestamp() if row.expires_at else None,
        "metadata": row.metadata_ or {},
    }


def _check_status(principal: ApiKeyPrincipal) -> ApiKeyPrincipal:
    if principal.status != "active":
        raise ApiKeyRejected(f"api_key_{principal.status}", f"API key is {principal.status}")
    if principal.expires_at_ts is not None and principal.expires_at_ts <= time.time():
        raise ApiKeyRejected("api_key_expired", "API key has expired")
    return principal


# ==========================================
# Serving-side verifier
# ==========================================


class _Verifier:
    """Local cache + Redis cache + DB lookup, plus the invalidation subscriber."""

    def __init__(self) -> None:
        self._local: Dict[str, Tuple[float, Optional[ApiKeyPrincipal]]] = {}
        self._last_touch: Dict[str, float] = {}
        self._background: Set[asyncio.Task] = set()
        self._subscriber: Optional[asyncio.Task] = None
        self.local_cache_enabled = True

    def reset(self) -> None:
        self._local.clear()
        self._last_touch.clear()

    def drop_local(self, key_hash: str) -> None:
        self._local.pop(key_hash, None)

    # ---------- lookups ----------

    async def _lookup_db(self, key_hash: str) -> Optional[Dict[str, Any]]:
        async with get_session_maker()() as session:
            row = await ApiKeyRepository(session).get_by_hash(key_hash)
            return _row_to_principal_dict(row) if row else None

    async def _lookup_redis(self, key_hash: str) -> Optional[Dict[str, Any]]:
        try:
            redis = await get_redis()
            raw = await redis.get(_cache_key(key_hash))
            return json.loads(raw) if raw else None
        except Exception as exc:  # noqa: BLE001 - Redis optional
            logger.warning(f"api key cache read failed: {exc}")
            return None

    async def _store_redis(self, key_hash: str, data: Dict[str, Any], ttl: int) -> None:
        try:
            redis = await get_redis()
            await redis.set(_cache_key(key_hash), json.dumps(data), ex=ttl)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"api key cache write failed: {exc}")

    async def resolve(self, raw_key: str) -> ApiKeyPrincipal:
        if not raw_key:
            raise ApiKeyRejected("invalid_api_key", "Missing API key")
        key_hash = hash_api_key(raw_key)
        now = time.monotonic()

        if self.local_cache_enabled:
            entry = self._local.get(key_hash)
            if entry is not None and now - entry[0] < float(settings.API_KEY_LOCAL_CACHE_SECONDS):
                cached = entry[1]
                if cached is None:
                    raise ApiKeyRejected("invalid_api_key", "Invalid API key")
                principal = _check_status(cached)
                self._touch(principal.id)
                return principal

        data = await self._lookup_redis(key_hash)
        if data is None:
            data = await self._lookup_db(key_hash)
            if data is None:
                await self._store_redis(key_hash, _MISSING, NEGATIVE_TTL_SECONDS)
                data = _MISSING
            else:
                await self._store_redis(key_hash, data, int(settings.API_KEY_CACHE_TTL_SECONDS))
        missing = bool(data.get("_missing") or data.get("missing"))
        principal_or_none = None if missing else ApiKeyPrincipal.from_dict(data)
        if len(self._local) >= LOCAL_CACHE_MAX_ENTRIES:
            self._local.clear()  # bound memory against floods of random keys
        self._local[key_hash] = (now, principal_or_none)
        if principal_or_none is None:
            raise ApiKeyRejected("invalid_api_key", "Invalid API key")
        principal = _check_status(principal_or_none)
        self._touch(principal.id)
        return principal

    # ---------- last_used buffering ----------

    def _touch(self, key_id: str) -> None:
        now = time.monotonic()
        last = self._last_touch.get(key_id, 0.0)
        if now - last < LAST_USED_THROTTLE_SECONDS:
            return
        self._last_touch[key_id] = now
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._write_touch(key_id))
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _write_touch(self, key_id: str) -> None:
        try:
            redis = await get_redis()
            await redis.hset(
                redis_key(LAST_USED_HASH),
                key_id,
                datetime.now(timezone.utc).isoformat(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"api key last_used buffer write failed: {exc}")

    async def flush_pending(self) -> None:
        if self._background:
            await asyncio.gather(*list(self._background), return_exceptions=True)

    # ---------- invalidation ----------

    async def invalidate(self, key_hashes: List[str]) -> None:
        """Drop Redis + local entries and tell every process (serving replicas) to do the same."""
        for key_hash in key_hashes:
            self.drop_local(key_hash)
        try:
            redis = await get_redis()
            if key_hashes:
                await redis.delete(*[_cache_key(h) for h in key_hashes])
                await redis.publish(redis_key(INVALIDATE_CHANNEL), json.dumps(key_hashes))
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"api key cache invalidation failed: {exc}")

    async def start_subscriber(self) -> None:
        """Background pub/sub listener (serving process) dropping local entries on revoke."""
        if self._subscriber is not None and not self._subscriber.done():
            return
        self._subscriber = asyncio.create_task(self._subscribe_loop(), name="apikeys-invalidate")

    async def stop_subscriber(self) -> None:
        if self._subscriber is not None:
            self._subscriber.cancel()
            try:
                await self._subscriber
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._subscriber = None

    async def _subscribe_loop(self) -> None:
        channel = redis_key(INVALIDATE_CHANNEL)
        while True:
            try:
                redis = await get_redis()
                pubsub = redis.pubsub()
                await pubsub.subscribe(channel)
                logger.info(f"api key invalidation subscriber listening on {channel}")
                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        continue
                    try:
                        for key_hash in json.loads(message["data"]):
                            self.drop_local(key_hash)
                    except (ValueError, TypeError):
                        self.reset()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect; TTLs bound the staleness
                logger.warning(f"api key invalidation subscriber error (retrying): {exc}")
                await asyncio.sleep(2)


verifier = _Verifier()


async def verify_api_key(raw_key: str, *, bind_request_actor: bool = True) -> ApiKeyPrincipal:
    """
    Verify a plaintext API key for the serving API.

    Returns an ``ApiKeyPrincipal`` or raises ``ApiKeyRejected`` (HTTP 401, wire
    code ``invalid_api_key`` / ``api_key_<status>`` / ``api_key_expired``,
    ``WWW-Authenticate: ApiKey``). Safe to call on every request: the warm
    path is a dict lookup (no I/O); a cold key costs one Redis GET, a Redis
    miss one indexed Postgres read. Never performs writes on the request path.
    """
    principal = await verifier.resolve(raw_key)
    if bind_request_actor:
        set_request_actor(f"api_key:{principal.id}")
    return principal


async def flush_last_used() -> int:
    """Move buffered last_used_at timestamps from Redis to Postgres (jobs process)."""
    try:
        redis = await get_redis()
        key = redis_key(LAST_USED_HASH)
        entries = await redis.hgetall(key)
        if not entries:
            return 0
        await redis.delete(key)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"api key last_used flush: Redis read failed: {exc}")
        return 0
    pairs: Dict[uuid.UUID, datetime] = {}
    for key_id, stamp in entries.items():
        try:
            pairs[uuid.UUID(key_id)] = datetime.fromisoformat(stamp)
        except ValueError:
            continue
    async with get_session_maker()() as session:
        updated = await ApiKeyRepository(session).set_last_used(pairs)
        await session.commit()
    return updated


async def expire_due_keys() -> int:
    """Flip active keys past expires_at to 'expired' and invalidate their cache entries."""
    async with get_session_maker()() as session:
        hashes = await ApiKeyRepository(session).mark_expired()
        await session.commit()
    if hashes:
        await verifier.invalidate(hashes)
        audit.record("api_key.expired", actor_type="system", metadata={"count": len(hashes)})
        logger.info(f"API keys expired: {len(hashes)}")
    return len(hashes)


# ==========================================
# Admin service
# ==========================================


class ApiKeyService:
    """Admin-side API key management."""

    def __init__(self, repo: ApiKeyRepository) -> None:
        self.repo = repo

    @staticmethod
    async def as_dependency(
        request: Request, db: AsyncSession = Depends(get_db)
    ) -> "ApiKeyService":
        return ApiKeyService(ApiKeyRepository(db))

    @staticmethod
    def to_dict(row: ApiKeys) -> Dict[str, Any]:
        return {
            "id": row.id,
            "name": row.name,
            "description": row.description,
            "key_prefix": row.key_prefix,
            "status": row.status,
            "expires_at": row.expires_at,
            "last_used_at": row.last_used_at,
            "total_requests": int(row.total_requests or 0),
            "metadata": row.metadata_ or {},
            "revoked_at": row.revoked_at,
            "last_rotated_at": row.last_rotated_at,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    async def _get(self, key_id: uuid.UUID) -> ApiKeys:
        row = await self.repo.get_active(key_id)
        if row is None:
            raise ResourceNotFoundError(resource_type="API key")
        return row

    async def list_keys(
        self,
        *,
        page: int,
        page_size: int,
        status: Optional[str] = None,
        search: Optional[str] = None,
        sort: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        rows, total = await self.repo.search(
            skip=(page - 1) * page_size,
            limit=page_size,
            status=status,
            search=search,
            sort=sort,
        )
        return [self.to_dict(r) for r in rows], total

    async def stats(self) -> Dict[str, int]:
        return await self.repo.stats()

    async def get_key(self, key_id: uuid.UUID) -> Dict[str, Any]:
        return self.to_dict(await self._get(key_id))

    async def create_key(
        self, data: Dict[str, Any], *, created_by: Optional[uuid.UUID]
    ) -> Dict[str, Any]:
        if await self.repo.get_by_name(data["name"]):
            raise DuplicateResourceError(resource_type="API key", field="name")
        full_key, key_prefix, key_hash = ApiKeyRepository.generate_key_pair()
        row = await self.repo.create(
            name=data["name"],
            description=data.get("description"),
            key_prefix=key_prefix,
            key_hash=key_hash,
            status="active",
            expires_at=data.get("expires_at"),
            metadata_=data.get("metadata") or {},
            created_by=created_by,
        )
        await verifier.invalidate([key_hash])  # clear any negative cache entry
        result = self.to_dict(row)
        audit.record(
            "api_key.created",
            resource_type="api_key",
            resource_id=row.id,
            resource_name=row.name,
            after_state={"key_prefix": key_prefix, "expires_at": row.expires_at},
        )
        logger.info(f"API key created: id={row.id} prefix={key_prefix}")
        result["key"] = full_key  # shown exactly once
        return result

    async def update_key(self, key_id: uuid.UUID, data: Dict[str, Any]) -> Dict[str, Any]:
        row = await self._get(key_id)
        before = self.to_dict(row)
        changes: Dict[str, Any] = {}
        if "name" in data and data["name"] is not None:
            if await self.repo.get_by_name(data["name"], exclude_id=key_id):
                raise DuplicateResourceError(resource_type="API key", field="name")
            changes["name"] = data["name"]
        for field in ("description", "expires_at"):
            if field in data:
                changes[field] = data[field]
        if data.get("metadata") is not None:
            changes["metadata_"] = data["metadata"]
        if changes:
            await self.repo.update(key_id, **changes)
            await verifier.invalidate([row.key_hash])
        row = await self._get(key_id)
        result = self.to_dict(row)
        audit.record(
            "api_key.updated",
            resource_type="api_key",
            resource_id=key_id,
            resource_name=row.name,
            before_state=before,
            after_state=result,
        )
        return result

    async def _set_status(
        self, key_id: uuid.UUID, status: str, event: str, allowed_from: Tuple[str, ...]
    ) -> Dict[str, Any]:
        row = await self._get(key_id)
        if row.status not in allowed_from:
            from app.core.exceptions import ConflictError

            raise ConflictError(f"API key is {row.status}; cannot {event.split('.')[-1]}")
        before = self.to_dict(row)
        values: Dict[str, Any] = {"status": status}
        if status == "revoked":
            values["revoked_at"] = datetime.now(timezone.utc)
        await self.repo.update(key_id, **values)
        await verifier.invalidate([row.key_hash])
        row = await self._get(key_id)
        result = self.to_dict(row)
        audit.record(
            event,
            resource_type="api_key",
            resource_id=key_id,
            resource_name=row.name,
            before_state={"status": before["status"]},
            after_state={"status": status},
        )
        logger.info(f"API key {event}: id={key_id}")
        return result

    async def revoke_key(self, key_id: uuid.UUID) -> Dict[str, Any]:
        return await self._set_status(
            key_id, "revoked", "api_key.revoked", ("active", "suspended", "expired")
        )

    async def suspend_key(self, key_id: uuid.UUID) -> Dict[str, Any]:
        return await self._set_status(key_id, "suspended", "api_key.suspended", ("active",))

    async def activate_key(self, key_id: uuid.UUID) -> Dict[str, Any]:
        row = await self._get(key_id)
        if row.expires_at and row.expires_at <= datetime.now(timezone.utc):
            from app.core.exceptions import ConflictError

            raise ConflictError("API key has expired; extend expires_at first")
        return await self._set_status(
            key_id, "active", "api_key.activated", ("suspended", "expired")
        )

    async def rotate_key(self, key_id: uuid.UUID) -> Dict[str, Any]:
        row = await self._get(key_id)
        if row.status == "revoked":
            from app.core.exceptions import ConflictError

            raise ConflictError("Revoked API keys cannot be rotated")
        old_hash = row.key_hash
        full_key, key_prefix, key_hash = ApiKeyRepository.generate_key_pair()
        await self.repo.update(
            key_id,
            key_prefix=key_prefix,
            key_hash=key_hash,
            last_rotated_at=datetime.now(timezone.utc),
        )
        await verifier.invalidate([old_hash, key_hash])
        row = await self._get(key_id)
        result = self.to_dict(row)
        audit.record(
            "api_key.rotated",
            resource_type="api_key",
            resource_id=key_id,
            resource_name=row.name,
            after_state={"key_prefix": key_prefix},
        )
        logger.info(f"API key rotated: id={key_id} prefix={key_prefix}")
        result["key"] = full_key
        return result

    async def delete_key(self, key_id: uuid.UUID) -> None:
        row = await self._get(key_id)
        before = self.to_dict(row)
        await self.repo.soft_delete(key_id)
        await verifier.invalidate([row.key_hash])
        audit.record(
            "api_key.deleted",
            resource_type="api_key",
            resource_id=key_id,
            resource_name=row.name,
            before_state=before,
        )
        logger.info(f"API key deleted: id={key_id}")
