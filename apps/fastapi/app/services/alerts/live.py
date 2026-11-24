"""
Best-effort live UI events for the automation domains (alerts, swaps, rule runs).

Thin wrapper over ``app.services.metrics.live.publish_live_event`` (Redis
stream ``gpu-deck:live:events`` behind ``/live/events`` and ``/live/ws``) with a
short timeout and JSON-safe payloads, so a slow or missing Redis never slows or
breaks the caller::

    from app.services.alerts.live import publish_live
    await publish_live("alert", alert_live_payload(...))

Payload keys are what ``apps/web/lib/desktop/events.ts::liveEventNotification`` reads:

- ``alert``: ``id, event_type, severity, title, message, resource_type,
  resource_id, resource_name, status`` (+ ``occurrences``); test-sends add
  ``kind="test", channel_id, channel_name, ok``.
- ``swap``: ``swap_id, state, kind, deployment_id, deployment_name, node_id,
  rule_name, trigger, error_message`` (+ ``step``, ``current_step``).
- ``rule_run``: ``id, rule_id, rule_name, outcome, dry_run, message``.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from app.core.logger import get_logger

logger = get_logger(__name__)

PUBLISH_TIMEOUT_SECONDS = 0.5


def _safe(value: Any) -> Any:
    if isinstance(value, Enum):
        return _safe(value.value)
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


async def publish_live(event: str, data: Dict[str, Any]) -> None:
    """Publish one live event. Never raises; gives up after ``PUBLISH_TIMEOUT_SECONDS``."""
    try:
        from app.services.metrics.live import publish_live_event

        await asyncio.wait_for(publish_live_event(event, _safe(data)), PUBLISH_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 - live UI events are best effort
        logger.debug(f"live {event} publish skipped: {exc}")


def alert_live_payload(row: Any, status: Optional[str] = None) -> Dict[str, Any]:
    """``alert`` frame for an ``alert_events`` row (or any object with the same fields)."""
    return {
        "id": str(row.id),
        "event_type": getattr(row, "event_type", None),
        "severity": getattr(row, "severity", None),
        "title": getattr(row, "title", None),
        "message": getattr(row, "message", None),
        "resource_type": getattr(row, "resource_type", None),
        "resource_id": getattr(row, "resource_id", None),
        "resource_name": getattr(row, "resource_name", None),
        "status": status or getattr(row, "status", None) or "open",
        "occurrences": getattr(row, "occurrences", None),
    }
