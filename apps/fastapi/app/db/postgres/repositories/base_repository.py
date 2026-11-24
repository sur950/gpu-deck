"""
Base repository with common CRUD operations.

Repositories flush, never commit — ``get_db`` commits once per request.
"""

import uuid
from typing import Any, Dict, Generic, List, Optional, Type, TypeVar

from app.core.logger import get_logger
from sqlalchemy import bindparam, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

logger = get_logger(__name__)

ModelType = TypeVar("ModelType", bound=DeclarativeBase)


class BaseRepository(Generic[ModelType]):
    """Base repository with common CRUD operations for all models."""

    def __init__(self, model: Type[ModelType], session: AsyncSession):
        self.model = model
        self.session = session

    async def release_connection(self) -> None:
        """
        End the current transaction so its pooled connection goes back before
        slow external I/O (an agent call, an engine request). Commit, not
        rollback: anything already flushed stays. Rows loaded so far stay
        usable (expire_on_commit=False); the next query opens a new transaction.
        """
        await self.session.commit()

    async def create(self, **kwargs) -> ModelType:
        """Create a record (uuid4 id if absent); add + flush + refresh; rollback on flush error."""
        if "id" not in kwargs:
            kwargs["id"] = uuid.uuid4()
        instance = self.model(**kwargs)
        self.session.add(instance)
        try:
            await self.session.flush()
        except Exception:
            # flush() failed (e.g. unique violation) and left the transaction
            # aborted — roll back so the original error propagates cleanly.
            await self.session.rollback()
            raise
        await self.session.refresh(instance)
        logger.debug(f"Created {self.model.__name__} with id: {instance.id}")
        return instance

    async def get_by_id(self, record_id: Any) -> Optional[ModelType]:
        result = await self.session.execute(select(self.model).where(self.model.id == record_id))
        return result.scalar_one_or_none()

    async def get_by_field(self, field_name: str, value: Any) -> Optional[ModelType]:
        result = await self.session.execute(
            select(self.model).where(getattr(self.model, field_name) == value)
        )
        return result.scalar_one_or_none()

    async def get_all(
        self,
        skip: int = 0,
        limit: int = 100,
        filters: Optional[Dict[str, Any]] = None,
        order_by: Optional[str] = None,
    ) -> List[ModelType]:
        """List with pagination and equality filters; ``order_by="-field"`` for descending."""
        query = select(self.model)
        if filters:
            for field, value in filters.items():
                if hasattr(self.model, field):
                    query = query.where(getattr(self.model, field) == value)
        if order_by:
            if order_by.startswith("-"):
                field = order_by[1:]
                if hasattr(self.model, field):
                    query = query.order_by(getattr(self.model, field).desc())
            elif hasattr(self.model, order_by):
                query = query.order_by(getattr(self.model, order_by))
        query = query.offset(skip).limit(limit)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def update(self, record_id: Any, **kwargs) -> Optional[ModelType]:
        instance = await self.get_by_id(record_id)
        if not instance:
            logger.warning(f"{self.model.__name__} with id {record_id} not found for update")
            return None
        for field, value in kwargs.items():
            if hasattr(instance, field):
                setattr(instance, field, value)
        try:
            await self.session.flush()
        except Exception:
            await self.session.rollback()
            raise
        await self.session.refresh(instance)
        logger.debug(f"Updated {self.model.__name__} with id: {record_id}")
        return instance

    async def delete(self, record_id: Any) -> bool:
        result = await self.session.execute(delete(self.model).where(self.model.id == record_id))
        await self.session.flush()
        deleted = result.rowcount > 0
        if deleted:
            logger.debug(f"Deleted {self.model.__name__} with id: {record_id}")
        else:
            logger.warning(f"{self.model.__name__} with id {record_id} not found for deletion")
        return deleted

    async def count(self, filters: Optional[Dict[str, Any]] = None) -> int:
        query = select(func.count()).select_from(self.model)
        if filters:
            for field, value in filters.items():
                if hasattr(self.model, field):
                    query = query.where(getattr(self.model, field) == value)
        result = await self.session.execute(query)
        return result.scalar_one()

    async def exists(self, **kwargs) -> bool:
        query = select(self.model)
        for field, value in kwargs.items():
            if hasattr(self.model, field):
                query = query.where(getattr(self.model, field) == value)
        result = await self.session.execute(query.limit(1))
        return result.scalar_one_or_none() is not None

    async def bulk_create(self, records: List[Dict[str, Any]]) -> List[ModelType]:
        instances = [self.model(**record) for record in records]
        self.session.add_all(instances)
        try:
            await self.session.flush()
        except Exception:
            await self.session.rollback()
            raise
        for instance in instances:
            await self.session.refresh(instance)
        logger.debug(f"Bulk created {len(instances)} {self.model.__name__} records")
        return instances

    async def bulk_update(self, updates: List[Dict[str, Any]]) -> int:
        """
        One executemany UPDATE for many rows. Every row must set the same
        fields (the common case: bulk-editing one field set across records).
        """
        rows = [dict(u) for u in updates if u.get("id") is not None]
        if not rows:
            return 0
        field_names = set()
        for row in rows:
            field_names.update(k for k in row.keys() if k != "id")
        for row in rows:
            row["_id"] = row.pop("id")
            missing = field_names - row.keys()
            if missing:
                raise ValueError(
                    "bulk_update requires every row to set the same fields; "
                    f"row for id={row['_id']} is missing {missing}"
                )
        stmt = (
            update(self.model)
            .where(self.model.id == bindparam("_id"))
            .values({field: bindparam(field) for field in field_names})
        )
        await self.session.execute(stmt, rows)
        logger.debug(f"Bulk updated {len(rows)} {self.model.__name__} records")
        return len(rows)
