"""
Fleet event repository: the audit rows and alert events the fleet domain
emits (node joined/removed, cert revoked, agent events such as engine crashes
and egress blocks).

- ``audit(...)`` delegates to ``audit.record`` (async enqueue, never raises).
- ``alert(...)`` hands the event to ``alerts.emit`` when that module
  exposes one (channel delivery, dedup), otherwise inserts the
  ``alert_events`` row itself with the same dedup rule so nothing is lost.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional

from app.core.logger import get_logger
from app.db.postgres.models.generated_models import AlertEvents
from app.services.audit import audit as audit_recorder
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


def _alerts_emit() -> Optional[Callable[..., Any]]:
    """``alerts.emit`` when the alerts module is importable (resolved lazily)."""
    try:
        import app.services.alerts as alerts_pkg  # noqa: WPS433

        emit = getattr(alerts_pkg, "emit", None)
        if emit is None:
            from app.services.alerts import service as alerts_service  # type: ignore

            emit = getattr(alerts_service, "emit", None)
        return emit if callable(emit) else None
    except Exception:  # noqa: BLE001 - alerts module not present
        return None


class FleetEventRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def audit(
        self,
        event_type: str,
        actor_type: str = "system",
        actor_id: Optional[uuid.UUID] = None,
        actor_name: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[uuid.UUID] = None,
        resource_name: Optional[str] = None,
        message: Optional[str] = None,
        before_state: Optional[Dict[str, Any]] = None,
        after_state: Optional[Dict[str, Any]] = None,
        outcome: str = "success",
        ip_address: Optional[str] = None,
        request_id: Optional[uuid.UUID] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        audit_recorder.record(
            event_type,
            actor_type=actor_type,
            actor_id=actor_id,
            actor_name=actor_name,
            resource_type=resource_type,
            resource_id=resource_id,
            resource_name=resource_name,
            before_state=before_state,
            after_state=after_state,
            outcome=outcome,
            message=message,
            metadata=metadata,
            ip_address=ip_address,
            request_id=request_id,
        )

    async def alert(
        self,
        severity: str,
        event_type: str,
        title: str,
        message: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[uuid.UUID] = None,
        resource_name: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        dedupe_key: Optional[str] = None,
        dedupe_window_seconds: int = 600,
    ) -> Optional[AlertEvents]:
        """Raise an alert; repeats within the dedupe window bump ``occurrences``."""
        emit = _alerts_emit()
        if emit is not None:
            try:
                result = emit(
                    event_type=event_type,
                    severity=severity,
                    title=title[:512],
                    message=message,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    resource_name=resource_name,
                    payload=payload or {},
                    dedupe_key=dedupe_key,
                )
                if hasattr(result, "__await__"):
                    await result
                return None
            except TypeError as exc:
                logger.warning(f"alerts.emit signature mismatch, writing the row directly: {exc}")
            except Exception as exc:  # noqa: BLE001 - alerting must not break ingest
                logger.warning(f"alerts.emit failed, writing the row directly: {exc}")
        return await self._insert_alert(
            severity,
            event_type,
            title,
            message,
            resource_type,
            resource_id,
            resource_name,
            payload,
            dedupe_key,
            dedupe_window_seconds,
        )

    async def _insert_alert(
        self,
        severity: str,
        event_type: str,
        title: str,
        message: Optional[str],
        resource_type: Optional[str],
        resource_id: Optional[uuid.UUID],
        resource_name: Optional[str],
        payload: Optional[Dict[str, Any]],
        dedupe_key: Optional[str],
        dedupe_window_seconds: int,
    ) -> AlertEvents:
        now = datetime.now(timezone.utc)
        if dedupe_key:
            existing = (
                await self.session.execute(
                    select(AlertEvents)
                    .where(
                        AlertEvents.dedupe_key == dedupe_key,
                        AlertEvents.status == "open",
                        AlertEvents.last_seen_at >= now - timedelta(seconds=dedupe_window_seconds),
                    )
                    .order_by(AlertEvents.last_seen_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if existing is not None:
                await self.session.execute(
                    update(AlertEvents)
                    .where(AlertEvents.id == existing.id)
                    .values(occurrences=AlertEvents.occurrences + 1, last_seen_at=now)
                )
                return existing
        row = AlertEvents(
            id=uuid.uuid4(),
            severity=severity,
            event_type=event_type,
            title=title[:512],
            message=message,
            resource_type=resource_type,
            resource_id=resource_id,
            resource_name=resource_name,
            payload=payload or {},
            dedupe_key=dedupe_key,
            status="open",
            occurrences=1,
            first_seen_at=now,
            last_seen_at=now,
        )
        self.session.add(row)
        await self.session.flush()
        return row
