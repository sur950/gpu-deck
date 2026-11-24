"""
SessionManager: opaque Fernet sessions with a Redis cache
plus a sliding idle timeout.

- create: insert sessions row (token_hash, ip, UA, expires_at) + Redis
  ``session:{token_hash}`` with TTL = min(hard remaining, idle timeout).
- validate: Redis first; on a miss decrypt the token (rejects tampered ones)
  and read the row from Postgres, then re-populate Redis. Idle sessions
  (no request for SESSION_IDLE_TIMEOUT_MINUTES) are revoked; the hard expiry
  (SESSION_EXPIRE_HOURS from creation) is never extended.
- touch: refresh the Redis TTL at most every TOUCH_INTERVAL seconds and write
  last_active_at to Postgres at most once a minute.
- revoke / revoke_all: flip is_active and drop the Redis entries.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.core.logger import get_logger
from app.core.settings import settings
from app.db.postgres.repositories.session_repository import SessionRepository
from app.db.redis.cache_service import CacheService
from app.services.auth.token_crypto import (
    decrypt_session_token,
    generate_session_token,
    hash_token,
)
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

TOUCH_INTERVAL_SECONDS = 30
DB_TOUCH_INTERVAL_SECONDS = 60


class SessionManager:
    """Create, validate, touch and revoke admin sessions."""

    def __init__(self, db: AsyncSession):
        self.repo = SessionRepository(db)

    # ---------- helpers ----------

    @staticmethod
    async def _cache() -> Optional[CacheService]:
        try:
            return await CacheService.get_instance()
        except Exception as exc:  # noqa: BLE001 - Redis optional, Postgres is the truth
            logger.warning(f"Session cache unavailable: {exc}")
            return None

    @staticmethod
    def _ttl_for(expires_at: datetime, last_active: Optional[datetime] = None) -> int:
        now = datetime.now(timezone.utc)
        hard = int((expires_at - now).total_seconds())
        idle_minutes = int(settings.SESSION_IDLE_TIMEOUT_MINUTES)
        if idle_minutes > 0:
            idle_deadline = (last_active or now) + timedelta(minutes=idle_minutes)
            hard = min(hard, int((idle_deadline - now).total_seconds()))
        return max(hard, 1)

    @staticmethod
    def _to_dict(row, token_hash: str) -> Dict[str, Any]:
        return {
            "session_id": str(row.id),
            "admin_id": str(row.admin_user_id),
            "token_hash": token_hash,
            "ip_address": str(row.ip_address) if row.ip_address else None,
            "user_agent": row.user_agent,
            "created_at": row.created_at.timestamp(),
            "expires_at": row.expires_at.timestamp(),
            "last_active_at": (row.last_active_at or row.created_at).timestamp(),
            "db_touched_at": (row.last_active_at or row.created_at).timestamp(),
        }

    # ---------- lifecycle ----------

    async def create_session(
        self, admin_id: uuid.UUID, ip_address: Optional[str], user_agent: Optional[str]
    ) -> Dict[str, Any]:
        session_id = uuid.uuid4()
        raw_token, token_hash = generate_session_token(str(admin_id), str(session_id))
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=int(settings.SESSION_EXPIRE_HOURS))
        row = await self.repo.create(
            id=session_id,
            admin_user_id=admin_id,
            token_hash=token_hash,
            ip_address=ip_address or None,
            user_agent=(user_agent or None) and user_agent[:512],
            created_at=now,
            expires_at=expires_at,
            last_active_at=now,
            is_active=True,
        )
        data = self._to_dict(row, token_hash)
        cache = await self._cache()
        if cache is not None:
            await cache.create_session(
                session_id=token_hash,
                user_id=str(admin_id),
                session_data=data,
                ttl=self._ttl_for(expires_at, now),
            )
        return {
            "raw_token": raw_token,
            "token_hash": token_hash,
            "session_id": session_id,
            "expires_at": expires_at,
            "expires_in": int((expires_at - now).total_seconds()),
            "session": data,
        }

    async def validate_session(self, raw_token: str) -> Optional[Dict[str, Any]]:
        """Session dict for a live session, None when unknown/expired/revoked/idle."""
        if not raw_token:
            return None
        token_hash = hash_token(raw_token)
        now = datetime.now(timezone.utc).timestamp()

        cache = await self._cache()
        data = await cache.get_session(token_hash) if cache is not None else None
        if data is None:
            if decrypt_session_token(raw_token) is None:
                return None
            row = await self.repo.get_active_by_hash(token_hash)
            if row is None:
                return None
            data = self._to_dict(row, token_hash)
            if cache is not None:
                await cache.create_session(
                    session_id=token_hash,
                    user_id=data["admin_id"],
                    session_data=data,
                    ttl=self._ttl_for(row.expires_at, row.last_active_at),
                )

        if float(data.get("expires_at", 0)) <= now:
            await self.revoke_by_hash(token_hash, data.get("admin_id"))
            return None
        idle_minutes = int(settings.SESSION_IDLE_TIMEOUT_MINUTES)
        if idle_minutes > 0:
            last_active = float(data.get("last_active_at") or data.get("created_at") or now)
            if last_active + idle_minutes * 60 <= now:
                logger.info(f"Session {data.get('session_id')} expired after idle timeout")
                await self.revoke_by_hash(token_hash, data.get("admin_id"))
                return None
        return data

    async def touch_session(self, data: Dict[str, Any]) -> None:
        """Sliding refresh: extend the Redis TTL and (less often) last_active_at in Postgres."""
        now = datetime.now(timezone.utc)
        now_ts = now.timestamp()
        last_active = float(data.get("last_active_at") or 0)
        if now_ts - last_active < TOUCH_INTERVAL_SECONDS:
            return
        data["last_active_at"] = now_ts
        expires_at = datetime.fromtimestamp(float(data["expires_at"]), tz=timezone.utc)
        cache = await self._cache()
        if cache is not None:
            try:
                await cache._redis_client.setex(  # noqa: SLF001 - same TTL rule as create
                    f"{cache.session_prefix}{data['token_hash']}",
                    self._ttl_for(expires_at, now),
                    json.dumps(data),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Session touch (cache) failed: {exc}")
        if now_ts - float(data.get("db_touched_at") or 0) >= DB_TOUCH_INTERVAL_SECONDS:
            data["db_touched_at"] = now_ts
            try:
                await self.repo.touch(uuid.UUID(data["session_id"]), now)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Session touch (db) failed: {exc}")

    async def revoke_by_hash(self, token_hash: str, admin_id: Optional[str] = None) -> None:
        cache = await self._cache()
        if cache is not None:
            await cache.delete_session(token_hash, admin_id)

    async def revoke_session(self, session_id: uuid.UUID, admin_id: uuid.UUID) -> bool:
        row = await self.repo.get_active_by_id(session_id, admin_id)
        if row is None:
            return False
        await self.repo.revoke(session_id, admin_id)
        await self.revoke_by_hash(row.token_hash, str(admin_id))
        return True

    async def revoke_all_sessions(
        self, admin_id: uuid.UUID, exclude_session_id: Optional[uuid.UUID] = None
    ) -> int:
        hashes = await self.repo.revoke_all(admin_id, exclude_session_id)
        cache = await self._cache()
        if cache is not None:
            for token_hash in hashes:
                await cache.delete_session(token_hash, str(admin_id))
        return len(hashes)

    async def list_sessions(
        self, admin_id: uuid.UUID, page: int, page_size: int
    ) -> tuple[List[Any], int]:
        return await self.repo.list_active(admin_id, skip=(page - 1) * page_size, limit=page_size)
