"""
PostgreSQL database connection and session management.

- ``get_db``: request-scoped session; commits once at the end, rolls back on error.
- Repositories flush, never commit. Call ``repo.release_connection()`` before
  slow external I/O so the connection is not idle-in-transaction.
- Celery/scheduler processes use NullPool because each task runs on a fresh
  event loop (see ``_is_worker_process``).
"""

import asyncio
import sys
from typing import Any, AsyncGenerator, Awaitable, Callable, List

from app.core.logger import get_logger
from app.core.settings import settings
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base
from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool
from starlette.exceptions import HTTPException

logger = get_logger(__name__)

Base = declarative_base()

engine = None


def _is_worker_process() -> bool:
    """
    True when launched as a Celery worker/beat or the APScheduler jobs process
    rather than a Uvicorn web process.

    Those processes run async work on a new event loop per task
    (asyncio.run / new_event_loop + run_until_complete). A pooled asyncpg
    connection created in one loop cannot be reused in another ("got Future
    attached to a different loop"), so they must not share a pool.
    """
    argv = " ".join(sys.argv).lower().replace("\\", "/")
    # `python -m app.workers.scheduler` leaves the module *file path* in argv[0]
    # (".../app/workers/scheduler.py"), not the dotted name.
    return "celery" in argv or "app.workers.scheduler" in argv or "app/workers/scheduler" in argv


def _server_settings() -> dict:
    """
    Postgres session GUCs applied to every physical connection (asyncpg
    startup parameters). A backstop against sessions held open across slow
    I/O: Postgres itself reclaims a stuck connection. 0 disables a GUC.

    idle_session_timeout is NOT a startup parameter (Postgres < 14 rejects it);
    see _apply_idle_session_timeout.
    """
    return {
        "idle_in_transaction_session_timeout": str(settings.DB_IDLE_IN_TRANSACTION_TIMEOUT_MS),
        "statement_timeout": str(settings.DB_STATEMENT_TIMEOUT_MS),
        # Time bucketing (date_bin / date_trunc) must not depend on the server's TimeZone.
        "timezone": "UTC",
    }


def _apply_idle_session_timeout(dbapi_connection, connection_record) -> None:
    """Pool "connect" hook (API process only): close connections idle longer than the limit."""
    timeout_ms = int(settings.DB_IDLE_SESSION_TIMEOUT_MS)
    if timeout_ms <= 0:
        return
    if dbapi_connection.driver_connection.get_server_version().major < 14:
        return
    dbapi_connection.run_async(
        lambda conn: conn.execute(f"SET idle_session_timeout = {timeout_ms}")
    )


def get_engine_kwargs():
    if _is_worker_process():
        return {
            "echo": settings.DB_ECHO,
            "poolclass": NullPool,
            "connect_args": {"server_settings": _server_settings()},
        }
    return {
        "echo": settings.DB_ECHO,
        "poolclass": AsyncAdaptedQueuePool,
        "pool_size": settings.DB_POOL_SIZE,
        "max_overflow": settings.DB_MAX_OVERFLOW,
        # LIFO: light traffic keeps reusing the same few connections, the rest
        # go idle long enough for idle_session_timeout to close them.
        "pool_use_lifo": True,
        "pool_timeout": settings.DB_POOL_TIMEOUT,
        "pool_pre_ping": settings.DB_POOL_PRE_PING or settings.DB_IDLE_SESSION_TIMEOUT_MS > 0,
        "pool_recycle": settings.DB_POOL_RECYCLE,
        "connect_args": {"server_settings": _server_settings()},
    }


def get_async_engine():
    global engine
    if engine is None:
        engine = create_async_engine(settings.get_database_url(), **get_engine_kwargs())
        if not _is_worker_process():
            event.listen(engine.sync_engine, "connect", _apply_idle_session_timeout)
    return engine


def get_session_maker():
    return async_sessionmaker(
        get_async_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency: yields an async database session; commits once per request."""
    session_maker = get_session_maker()
    async with session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            # A 4xx the route raised on purpose is a client error, not a DB failure.
            if not (isinstance(e, HTTPException) and e.status_code < 500):
                logger.error(f"Database session error: {e}")
            raise
        finally:
            await session.close()


async def run_concurrent_reads(*factories: Callable[[AsyncSession], Awaitable[Any]]) -> List[Any]:
    """
    Run independent read-only queries concurrently, each on its own pooled
    session (one AsyncSession cannot execute two statements at once). Reads
    only: each branch commits/rolls back independently.
    """
    maker = get_session_maker()

    async def _run(factory: Callable[[AsyncSession], Awaitable[Any]]) -> Any:
        async with maker() as session:
            return await factory(session)

    return await asyncio.gather(*(_run(f) for f in factories))


async def init_db():
    """Initialize database. Schema comes from Alembic migrations, never from metadata."""
    async with get_async_engine().begin():
        try:
            from app.db.postgres.models import generated_models  # noqa: F401
        except ImportError:
            logger.warning(
                "generated_models.py missing — run app/db/postgres/scripts/generate_models.sh"
            )
    logger.info("Database initialized")


async def close_db():
    global engine
    if engine is not None:
        await engine.dispose()
        engine = None
    logger.info("Database connections closed")
