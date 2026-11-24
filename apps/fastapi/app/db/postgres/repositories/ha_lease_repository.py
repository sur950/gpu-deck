"""
Repository for ``ha_leases`` — Postgres-backed leader election.

Semantics (see the table comment in 001_init): a lease is held by
``holder_id`` until ``expires_at``. Acquire succeeds only when the row is
free or expired; every successful acquire increments ``version`` (the
fencing token). Renew succeeds only for the current holder of an unexpired
lease and keeps the version. All statements are single atomic UPDATEs so two
contenders can never both believe they hold the lease.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from app.core.logger import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


class HaLeaseRepository:
    """Atomic acquire / renew / release on ``ha_leases``."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def ensure_row(self, name: str) -> None:
        await self.session.execute(
            text(
                "INSERT INTO ha_leases (name, version) VALUES (:name, 0) "
                "ON CONFLICT (name) DO NOTHING"
            ),
            {"name": name},
        )

    async def try_acquire(self, name: str, holder_id: str, ttl_seconds: int) -> Optional[int]:
        """
        Take the lease if it is free/expired, or renew it if we already hold it.
        Returns the fencing token (version) on success, None otherwise.
        """
        await self.ensure_row(name)
        result = await self.session.execute(
            text("""
                UPDATE ha_leases
                   SET holder_id   = :holder,
                       acquired_at = CASE WHEN holder_id IS DISTINCT FROM :holder
                                          OR expires_at IS NULL OR expires_at < now()
                                          THEN now() ELSE acquired_at END,
                       renewed_at  = now(),
                       expires_at  = now() + make_interval(secs => :ttl),
                       version     = CASE WHEN holder_id IS DISTINCT FROM :holder
                                          OR expires_at IS NULL OR expires_at < now()
                                          THEN version + 1 ELSE version END
                 WHERE name = :name
                   AND (holder_id IS NULL OR holder_id = :holder
                        OR expires_at IS NULL OR expires_at < now())
             RETURNING version
                """),
            {"name": name, "holder": holder_id, "ttl": float(ttl_seconds)},
        )
        row = result.first()
        return int(row[0]) if row else None

    async def renew(self, name: str, holder_id: str, ttl_seconds: int) -> Optional[int]:
        """Extend our own unexpired lease. Returns the version, or None if we lost it."""
        result = await self.session.execute(
            text("""
                UPDATE ha_leases
                   SET renewed_at = now(),
                       expires_at = now() + make_interval(secs => :ttl)
                 WHERE name = :name AND holder_id = :holder AND expires_at >= now()
             RETURNING version
                """),
            {"name": name, "holder": holder_id, "ttl": float(ttl_seconds)},
        )
        row = result.first()
        return int(row[0]) if row else None

    async def release(self, name: str, holder_id: str) -> bool:
        result = await self.session.execute(
            text(
                "UPDATE ha_leases SET expires_at = now(), holder_id = NULL "
                "WHERE name = :name AND holder_id = :holder"
            ),
            {"name": name, "holder": holder_id},
        )
        return (result.rowcount or 0) > 0

    async def current(self, name: str) -> Optional[Dict[str, Any]]:
        result = await self.session.execute(
            text(
                "SELECT name, holder_id, acquired_at, renewed_at, expires_at, version, "
                "(expires_at IS NOT NULL AND expires_at >= now()) AS held "
                "FROM ha_leases WHERE name = :name"
            ),
            {"name": name},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def is_held_by(self, name: str, holder_id: str, version: int) -> bool:
        """Fencing check: the lease is still ours with the same token."""
        result = await self.session.execute(
            text(
                "SELECT 1 FROM ha_leases WHERE name = :name AND holder_id = :holder "
                "AND version = :version AND expires_at >= now()"
            ),
            {"name": name, "holder": holder_id, "version": version},
        )
        return result.first() is not None

    async def delete(self, name: str) -> None:
        await self.session.execute(text("DELETE FROM ha_leases WHERE name = :name"), {"name": name})

    @staticmethod
    def now_utc() -> datetime:
        from datetime import timezone

        return datetime.now(timezone.utc)
