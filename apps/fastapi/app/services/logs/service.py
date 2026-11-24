"""
Logs service: engine/agent log chunks the hub stores in ``log_chunks``
plus control-plane log lines from other domains.

- ``query``: newest first, cursor-paginated (opaque base64 of ``ts|id``)
- ``stream``: live tail (SSE) from the live event stream (``log`` events)
- ``export``: plain-text download, oldest first, capped

Other domains write their own lines (rule decisions, swap steps, API layer
errors) with::

    from app.services.logs.service import record_log
    await record_log("swap", "info", "unload done", node_id=..., instance_id=...)
"""

from __future__ import annotations

import base64
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence, Tuple

from app.core.exceptions import ValidationError
from app.core.logger import get_logger
from app.core.settings import settings
from app.db.postgres.connection import get_db, get_session_maker
from app.db.postgres.repositories.log_repository import LogRepository
from app.services.metrics.live import LiveFilter, live_event_stream, publish_live_event
from app.services.metrics.service import parse_time
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

SOURCES = ("engine", "agent", "control_plane", "rule", "swap", "api_layer")
LEVELS = ("debug", "info", "warn", "error")
EXPORT_MAX_CHUNKS = 50_000


def encode_cursor(ts: datetime, row_id: int) -> str:
    return base64.urlsafe_b64encode(f"{ts.isoformat()}|{row_id}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> Tuple[datetime, int]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        ts_raw, row_id = base64.urlsafe_b64decode(padded.encode()).decode().rsplit("|", 1)
        return datetime.fromisoformat(ts_raw), int(row_id)
    except Exception as exc:  # noqa: BLE001
        raise ValidationError("invalid cursor") from exc


def split_sources(values: Optional[Sequence[str]]) -> Optional[List[str]]:
    if not values:
        return None
    out: List[str] = []
    for value in values:
        out.extend(p.strip() for p in str(value).split(",") if p.strip())
    bad = [s for s in out if s not in SOURCES]
    if bad:
        raise ValidationError(f"unknown log source(s): {', '.join(bad)}")
    return out or None


def level_at_least(level: Optional[str], candidate: Optional[str]) -> bool:
    if not level:
        return True
    order = {lv: i for i, lv in enumerate(LEVELS)}
    return order.get(candidate or "info", 1) >= order.get(level, 0)


class LogService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = LogRepository(session)

    @staticmethod
    async def as_dependency(request: Request, db: AsyncSession = Depends(get_db)) -> "LogService":
        return LogService(db)

    async def query(
        self,
        from_: Optional[str],
        to: Optional[str],
        node_id: Optional[uuid.UUID] = None,
        instance_id: Optional[uuid.UUID] = None,
        deployment_id: Optional[uuid.UUID] = None,
        sources: Optional[Sequence[str]] = None,
        level: Optional[str] = None,
        stream: Optional[str] = None,
        request_id: Optional[uuid.UUID] = None,
        search: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: int = 200,
    ) -> Dict[str, Any]:
        end = parse_time(to, datetime.now(timezone.utc))
        start = parse_time(from_, end - timedelta(hours=24))
        limit = max(1, min(int(limit), settings.LOGS_QUERY_MAX_LIMIT))
        rows = await self.repo.query(
            start,
            end,
            limit,
            node_id=node_id,
            instance_id=instance_id,
            deployment_id=deployment_id,
            sources=split_sources(sources),
            level=level,
            stream=stream,
            request_id=request_id,
            search=search,
            cursor=decode_cursor(cursor) if cursor else None,
        )
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = encode_cursor(rows[-1]["ts"], rows[-1]["id"]) if has_more and rows else None
        return {
            "items": [_chunk(r) for r in rows],
            "next_cursor": next_cursor,
            "has_more": has_more,
        }


async def export_log_lines(
    from_: Optional[str],
    to: Optional[str],
    node_id: Optional[uuid.UUID] = None,
    instance_id: Optional[uuid.UUID] = None,
    sources: Optional[Sequence[str]] = None,
    level: Optional[str] = None,
) -> AsyncIterator[str]:
    """Oldest-first text lines; one short-lived session per page (safe inside a stream)."""
    end = parse_time(to, datetime.now(timezone.utc))
    start = parse_time(from_, end - timedelta(hours=24))
    src = split_sources(sources)
    cursor: Optional[Tuple[datetime, int]] = None
    sent = 0
    while sent < EXPORT_MAX_CHUNKS:
        async with get_session_maker()() as session:
            rows = await LogRepository(session).query(
                start,
                end,
                1000,
                node_id=node_id,
                instance_id=instance_id,
                sources=src,
                level=level,
                cursor=cursor,
                ascending=True,
            )
        page = rows[:1000]
        for row in page:
            yield format_chunk_text(row)
        sent += len(page)
        if len(rows) <= 1000 or not page:
            break
        cursor = (page[-1]["ts"], page[-1]["id"])


def format_chunk_text(row: Dict[str, Any]) -> str:
    who = row.get("node_name") or (str(row["node_id"])[:8] if row.get("node_id") else "-")
    inst = str(row["instance_id"])[:8] if row.get("instance_id") else "-"
    ts = row["ts"].isoformat() if isinstance(row["ts"], datetime) else str(row["ts"])
    prefix = f"{ts} [{row['level']}] {row['source']}/{row['stream']} {who} {inst}: "
    return "".join(f"{prefix}{line}\n" for line in str(row["content"]).split("\n"))


def _chunk(row: Dict[str, Any]) -> Dict[str, Any]:
    keys = (
        "id",
        "ts",
        "ts_end",
        "node_id",
        "node_name",
        "instance_id",
        "deployment_id",
        "source",
        "stream",
        "level",
        "logger",
        "request_id",
        "line_count",
        "content",
        "attributes",
    )
    return {k: row.get(k) for k in keys}


async def log_stream(
    node_id: Optional[uuid.UUID],
    instance_id: Optional[uuid.UUID],
    deployment_id: Optional[uuid.UUID],
    sources: Optional[Sequence[str]],
    level: Optional[str],
    is_disconnected=None,
) -> AsyncIterator[Dict[str, Any]]:
    """Live ``log`` events (LogChunk-shaped data) plus heartbeats for the SSE tail."""
    src = split_sources(sources)
    flt = LiveFilter(
        topics={"log", "heartbeat"},
        node_id=node_id,
        instance_id=instance_id,
        deployment_id=deployment_id,
    )
    async for event in live_event_stream(flt, is_disconnected=is_disconnected):
        if event["event"] == "log":
            data = event.get("data") or {}
            if src and data.get("source") not in src:
                continue
            if not level_at_least(level, data.get("level")):
                continue
        yield event


async def record_log(
    source: str,
    level: str,
    content: str,
    *,
    node_id: Optional[uuid.UUID] = None,
    instance_id: Optional[uuid.UUID] = None,
    deployment_id: Optional[uuid.UUID] = None,
    request_id: Optional[uuid.UUID] = None,
    logger_name: Optional[str] = None,
    attributes: Optional[Dict[str, Any]] = None,
) -> None:
    """Store one control-plane log line (never raises) and push it to live tails."""
    if source not in SOURCES or level not in LEVELS:
        logger.warning(f"record_log: invalid source/level {source}/{level}")
        return
    now = datetime.now(timezone.utc)
    row = {
        "ts": now,
        "ts_end": now,
        "node_id": node_id,
        "instance_id": instance_id,
        "deployment_id": deployment_id,
        "source": source,
        "stream": "app",
        "level": level,
        "logger": logger_name,
        "request_id": request_id,
        "line_count": content.count("\n") + 1,
        "byte_size": len(content.encode()),
        "content": content,
        "attributes": attributes,
    }
    try:
        async with get_session_maker()() as session:
            await LogRepository(session).insert_chunks([row], settings.PARTITIONS_DAYS_AHEAD)
            await session.commit()
        await publish_live_event(
            "log",
            {
                **row,
                "node_id": str(node_id) if node_id else None,
                "instance_id": str(instance_id) if instance_id else None,
                "deployment_id": str(deployment_id) if deployment_id else None,
                "request_id": str(request_id) if request_id else None,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"record_log failed: {exc}")
