"""
Audit log service.

Two entry points:

``audit.record(...)`` — the helper every domain calls from the hot path. It is
synchronous, never raises, and never blocks on I/O: the row is handed to
Celery (queue ``audit``) on a background thread/task, so the request keeps
going even when the broker is slow. Actor, IP, user agent and request id are
filled from the request context when not given::

    from app.services.audit import audit
    audit.record("api_key.revoked", resource_type="api_key", resource_id=key.id,
                 resource_name=key.name, before_state={"status": "active"},
                 after_state={"status": "revoked"})

``AuditService`` — the admin API's read side (list with filters, get, CSV export)
plus ``write_now`` for callers that must have the row in their own transaction.

Tests and offline scripts can swap the sink with ``audit.set_sink`` /
``audit.capture()``.
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import io
import json
import threading
import uuid
from datetime import datetime, timezone
from typing import (
    Any,
    AsyncIterator,
    Callable,
    Dict,
    Iterator,
    List,
    Optional,
    Set,
    Tuple,
)

from app.core.logger import get_logger
from app.core.request_context import (
    get_request_actor,
    get_request_id,
    get_request_ip,
    get_request_ua,
)
from app.db.postgres.connection import get_db, get_session_maker
from app.db.postgres.repositories.audit_log_repository import AuditLogRepository
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

ACTOR_TYPES = ("admin", "api_key", "system", "rule", "node_agent")
OUTCOMES = ("success", "failure")

AuditSink = Callable[[Dict[str, Any]], Any]

CSV_COLUMNS = [
    "id",
    "created_at",
    "event_type",
    "actor_type",
    "actor_id",
    "actor_name",
    "resource_type",
    "resource_id",
    "resource_name",
    "outcome",
    "message",
    "ip_address",
    "user_agent",
    "request_id",
    "before_state",
    "after_state",
    "metadata",
]


def _json_safe(value: Any) -> Any:
    """Make states/metadata JSON-serialisable (UUIDs, datetimes, nested)."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (uuid.UUID, datetime)):
        return str(value) if isinstance(value, uuid.UUID) else value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    return str(value)


def _split_actor(actor: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Request-context actor strings look like 'admin:<uuid>' or 'api_key:<uuid>'."""
    if not actor or ":" not in actor:
        return None, None
    kind, _, ident = actor.partition(":")
    return (kind if kind in ACTOR_TYPES else None), (ident or None)


class _AuditRecorder:
    """The ``audit`` singleton: builds events and dispatches them off the hot path."""

    def __init__(self) -> None:
        self._sink: Optional[AuditSink] = None
        self._pending: Set[asyncio.Task] = set()
        self._lock = threading.Lock()

    # ---------- configuration (tests, scripts) ----------

    def set_sink(self, sink: Optional[AuditSink]) -> None:
        """Replace the Celery dispatch with ``sink(event_dict)``; None restores the default."""
        self._sink = sink

    @contextlib.contextmanager
    def capture(self) -> Iterator[List[Dict[str, Any]]]:
        """``with audit.capture() as events:`` collects events instead of enqueuing them."""
        events: List[Dict[str, Any]] = []
        previous = self._sink
        self._sink = events.append
        try:
            yield events
        finally:
            self._sink = previous

    # ---------- the helper ----------

    def build_event(
        self,
        event_type: str,
        *,
        actor_type: Optional[str] = None,
        actor_id: Any = None,
        actor_name: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Any = None,
        resource_name: Optional[str] = None,
        before_state: Optional[Dict[str, Any]] = None,
        after_state: Optional[Dict[str, Any]] = None,
        outcome: str = "success",
        message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        request_id: Any = None,
    ) -> Dict[str, Any]:
        ctx_actor_type, ctx_actor_id = _split_actor(get_request_actor())
        if actor_type is None:
            actor_type = ctx_actor_type or "system"
        if actor_id is None and actor_type == ctx_actor_type:
            actor_id = ctx_actor_id
        if actor_type not in ACTOR_TYPES:
            actor_type = "system"
        if outcome not in OUTCOMES:
            outcome = "failure" if outcome in ("error", "failed") else "success"
        ua = user_agent if user_agent is not None else get_request_ua()
        return {
            "event_type": str(event_type)[:100],
            "actor_type": actor_type,
            "actor_id": str(actor_id) if actor_id else None,
            "actor_name": (actor_name or None) and str(actor_name)[:255],
            "resource_type": (resource_type or None) and str(resource_type)[:100],
            "resource_id": str(resource_id) if resource_id else None,
            "resource_name": (resource_name or None) and str(resource_name)[:255],
            "before_state": _json_safe(before_state),
            "after_state": _json_safe(after_state),
            "outcome": outcome,
            "message": (message or None) and str(message)[:2048],
            "ip_address": ip_address if ip_address is not None else get_request_ip(),
            "user_agent": str(ua)[:512] if ua else None,
            "request_id": str(request_id) if request_id else get_request_id(),
            "metadata": _json_safe(metadata) or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    def record(self, event_type: str, **kwargs: Any) -> None:
        """Record an audit event. Never raises; never blocks on the broker."""
        try:
            event = self.build_event(event_type, **kwargs)
        except Exception as exc:  # noqa: BLE001 - audit must never break a request
            logger.warning(f"audit.record({event_type!r}) could not build the event: {exc}")
            return
        try:
            self._dispatch(event)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"audit.record({event_type!r}) dispatch failed (event lost): {exc}")

    def _dispatch(self, event: Dict[str, Any]) -> None:
        if self._sink is not None:
            self._sink(event)
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is None:
            _enqueue(event)
            return
        # Hot path: hand the broker round-trip to a worker thread and move on.
        task = loop.create_task(asyncio.to_thread(_enqueue, event))
        with self._lock:
            self._pending.add(task)
        task.add_done_callback(self._discard)

    def _discard(self, task: asyncio.Task) -> None:
        with self._lock:
            self._pending.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.warning(f"audit enqueue task failed: {task.exception()}")

    async def drain(self) -> None:
        """Wait for in-flight enqueues (shutdown hooks, tests)."""
        with self._lock:
            pending = list(self._pending)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


def _enqueue(event: Dict[str, Any]) -> None:
    """Celery handoff (sync). Falls back to a direct write when the broker is unreachable."""
    from app.workers.tasks.audit_tasks import enqueue_audit_log

    enqueue_audit_log(event)


audit = _AuditRecorder()


# ==========================================
# Direct writes + read side
# ==========================================


async def write_audit_event(session: AsyncSession, event: Dict[str, Any]) -> uuid.UUID:
    """Insert one event dict (as produced by ``audit.build_event``) in ``session``."""
    repo = AuditLogRepository(session)
    row = await repo.create_log(
        event_type=event["event_type"],
        actor_type=event.get("actor_type") or "system",
        actor_id=_uuid_or_none(event.get("actor_id")),
        actor_name=event.get("actor_name"),
        resource_type=event.get("resource_type"),
        resource_id=_uuid_or_none(event.get("resource_id")),
        resource_name=event.get("resource_name"),
        before_state=event.get("before_state"),
        after_state=event.get("after_state"),
        outcome=event.get("outcome") or "success",
        message=event.get("message"),
        ip_address=event.get("ip_address") or None,
        user_agent=event.get("user_agent"),
        request_id=_uuid_or_none(event.get("request_id")),
        metadata=event.get("metadata") or {},
        created_at=_datetime_or_now(event.get("created_at")),
    )
    return row.id


async def write_audit_event_standalone(event: Dict[str, Any]) -> Optional[uuid.UUID]:
    """Insert with a fresh session (fallback path when Celery is down). Never raises."""
    try:
        async with get_session_maker()() as session:
            row_id = await write_audit_event(session, event)
            await session.commit()
            return row_id
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"audit direct write failed (event lost): {exc}")
        return None


def _uuid_or_none(value: Any) -> Optional[uuid.UUID]:
    if not value:
        return None
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except ValueError:
        return None


def _datetime_or_now(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


class AuditService:
    """Admin API read side for audit_logs."""

    def __init__(self, repo: AuditLogRepository) -> None:
        self.repo = repo

    @staticmethod
    async def as_dependency(
        request: Request, db: AsyncSession = Depends(get_db, scope="function")
    ) -> "AuditService":
        return AuditService(AuditLogRepository(db))

    async def write_now(self, event_type: str, **kwargs: Any) -> uuid.UUID:
        """Insert in the caller's transaction (rare; prefer ``audit.record``)."""
        return await write_audit_event(self.repo.session, audit.build_event(event_type, **kwargs))

    async def list_logs(
        self, *, page: int, page_size: int, sort: Optional[str] = None, **filters: Any
    ) -> Tuple[List[Dict[str, Any]], int]:
        rows, total = await self.repo.search(
            skip=(page - 1) * page_size, limit=page_size, sort=sort, **filters
        )
        return [self.repo.to_dict(r) for r in rows], total

    async def get_log(self, log_id: uuid.UUID) -> Dict[str, Any]:
        from app.core.exceptions import ResourceNotFoundError

        row = await self.repo.get_by_id(log_id)
        if row is None:
            raise ResourceNotFoundError(resource_type="Audit log")
        return self.repo.to_dict(row)

    async def export_csv(self, *, max_rows: int, **filters: Any) -> AsyncIterator[str]:
        """Yield CSV text chunks (header first) for the matching rows, oldest first."""
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        yield _flush(buffer)
        count = 0
        async for row in self.repo.iter_for_export(max_rows=max_rows, **filters):
            data = self.repo.to_dict(row)
            for key in ("before_state", "after_state", "metadata"):
                data[key] = json.dumps(data[key], default=str) if data[key] else ""
            data["created_at"] = data["created_at"].isoformat() if data["created_at"] else ""
            writer.writerow({k: ("" if v is None else v) for k, v in data.items()})
            count += 1
            if count % 500 == 0:
                yield _flush(buffer)
        yield _flush(buffer)


def _flush(buffer: io.StringIO) -> str:
    value = buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)
    return value
