"""
Database utilities including transaction management.
"""

from functools import wraps
from typing import Any, Callable

from app.core.logger import get_logger
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


def transactional(func: Callable) -> Callable:
    """
    Decorator for automatic transaction management: commit on success, rollback
    on error. The decorated function must receive an AsyncSession as ``db``.
    """

    @wraps(func)
    async def wrapper(*args, **kwargs) -> Any:
        db: AsyncSession = kwargs.get("db")
        if db is None:
            for arg in args:
                if isinstance(arg, AsyncSession):
                    db = arg
                    break
        if db is None:
            raise ValueError(
                f"@transactional requires a 'db' AsyncSession parameter in {func.__name__}"
            )
        try:
            result = await func(*args, **kwargs)
            await db.commit()
            logger.debug(f"Transaction committed for {func.__name__}")
            return result
        except Exception as e:
            await db.rollback()
            logger.error(f"Transaction rolled back for {func.__name__}: {e}")
            raise

    return wrapper


class TransactionContext:
    """``async with TransactionContext(db):`` — commit on exit, rollback on exception."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            await self.session.rollback()
            logger.error(f"Transaction rolled back: {exc_val}")
            return False
        await self.session.commit()
        logger.debug("Transaction committed")
        return True
