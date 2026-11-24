"""
Repository for API keys (api_keys). Plaintext keys are generated in memory,
returned once, and only ``key_prefix`` + SHA-256 ``key_hash`` are stored.
"""

import hashlib
import secrets
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from app.core.logger import get_logger
from app.core.settings import settings
from app.db.postgres.models.generated_models import ApiKeys
from app.db.postgres.repositories.base_repository import BaseRepository
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

KEY_PREFIX_LEN = 12
SORTABLE = {"created_at", "updated_at", "name", "status", "last_used_at", "expires_at"}


def hash_api_key(raw_key: str) -> str:
    """SHA-256 hex of the full plaintext key (what api_keys.key_hash stores)."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


class ApiKeyRepository(BaseRepository[ApiKeys]):
    """CRUD + lookups for api_keys."""

    def __init__(self, session: AsyncSession):
        super().__init__(ApiKeys, session)

    @staticmethod
    def generate_key_pair() -> Tuple[str, str, str]:
        """full_key = prefix + 48 hex chars; key_prefix = first 12 chars; key_hash = sha256."""
        full_key = f"{settings.API_KEY_PREFIX}{secrets.token_hex(24)}"
        return full_key, full_key[:KEY_PREFIX_LEN], hash_api_key(full_key)

    async def get_by_hash(self, key_hash: str) -> Optional[ApiKeys]:
        result = await self.session.execute(
            select(ApiKeys).where(ApiKeys.key_hash == key_hash, ApiKeys.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def get_active(self, key_id: uuid.UUID) -> Optional[ApiKeys]:
        result = await self.session.execute(
            select(ApiKeys).where(ApiKeys.id == key_id, ApiKeys.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def get_by_name(
        self, name: str, exclude_id: Optional[uuid.UUID] = None
    ) -> Optional[ApiKeys]:
        query = select(ApiKeys).where(
            func.lower(ApiKeys.name) == name.lower(), ApiKeys.deleted_at.is_(None)
        )
        if exclude_id is not None:
            query = query.where(ApiKeys.id != exclude_id)
        return (await self.session.execute(query)).scalar_one_or_none()

    async def search(
        self,
        *,
        skip: int,
        limit: int,
        status: Optional[str] = None,
        search: Optional[str] = None,
        sort: Optional[str] = None,
    ) -> Tuple[List[ApiKeys], int]:
        conds = [ApiKeys.deleted_at.is_(None)]
        if status:
            conds.append(ApiKeys.status == status)
        if search:
            conds.append(ApiKeys.name.ilike(f"%{search}%"))
        total = (
            await self.session.execute(select(func.count()).select_from(ApiKeys).where(*conds))
        ).scalar_one()
        query = select(ApiKeys).where(*conds).order_by(*_order(sort)).offset(skip).limit(limit)
        rows = await self.session.execute(query)
        return list(rows.scalars().all()), int(total)

    async def stats(self) -> Dict[str, int]:
        rows = await self.session.execute(
            select(ApiKeys.status, func.count())
            .where(ApiKeys.deleted_at.is_(None))
            .group_by(ApiKeys.status)
        )
        counts = {status: int(count) for status, count in rows.all()}
        return {
            "total": sum(counts.values()),
            "active": counts.get("active", 0),
            "expired": counts.get("expired", 0),
            "revoked": counts.get("revoked", 0),
            "suspended": counts.get("suspended", 0),
        }

    async def soft_delete(self, key_id: uuid.UUID) -> None:
        now = datetime.now(timezone.utc)
        await self.session.execute(
            update(ApiKeys)
            .where(ApiKeys.id == key_id)
            .values(status="revoked", revoked_at=now, deleted_at=now)
        )
        await self.session.flush()

    async def mark_expired(self, now: Optional[datetime] = None) -> List[str]:
        """Flip active keys past expires_at to 'expired'; returns their key hashes."""
        now = now or datetime.now(timezone.utc)
        result = await self.session.execute(
            update(ApiKeys)
            .where(
                ApiKeys.deleted_at.is_(None),
                ApiKeys.status == "active",
                ApiKeys.expires_at.is_not(None),
                ApiKeys.expires_at <= now,
            )
            .values(status="expired")
            .returning(ApiKeys.key_hash)
        )
        hashes = [row[0] for row in result.all()]
        await self.session.flush()
        return hashes

    async def set_last_used(self, pairs: Dict[uuid.UUID, datetime]) -> int:
        """Bulk last_used_at update from the Redis buffer (only moves forward)."""
        updated = 0
        for key_id, at in pairs.items():
            result = await self.session.execute(
                update(ApiKeys)
                .where(
                    ApiKeys.id == key_id,
                    (ApiKeys.last_used_at.is_(None)) | (ApiKeys.last_used_at < at),
                )
                .values(last_used_at=at)
            )
            updated += result.rowcount
        await self.session.flush()
        return updated


def _order(sort: Optional[str]):
    sort = sort or "-created_at"
    desc = sort.startswith("-")
    field = sort.lstrip("-+")
    if field not in SORTABLE:
        field, desc = "created_at", True
    column = getattr(ApiKeys, field)
    return [
        column.desc().nulls_last() if desc else column.asc().nulls_last(),
        ApiKeys.id,
    ]
