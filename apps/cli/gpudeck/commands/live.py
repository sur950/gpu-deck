"""Streaming commands: `live events`, `logs stream` (SSE) and `live ws` (WebSocket pointer)."""

from __future__ import annotations

import json
import logging
from typing import Optional

import typer

from .. import output
from ..errors import ConfigError

logger = logging.getLogger(__name__)


def register(root: typer.Typer, groups: dict[str, typer.Typer]) -> None:
    from ..registry import ensure_group

    live_app = ensure_group(root, groups, ["live"])
    logs_app = ensure_group(root, groups, ["logs"])

    @live_app.command("events")
    def events(
        ctx: typer.Context,
        topics: Optional[list[str]] = typer.Option(
            None, "--topic", "-t", help="Topic filter (repeatable)"
        ),  # noqa: UP007
        limit: Optional[int] = typer.Option(
            None, "--limit", "-n", help="Stop after N events"
        ),  # noqa: UP007
    ) -> None:
        """Follow server-sent events from GET /live/events (Ctrl-C to stop)."""
        state = ctx.obj
        session = state.session()
        params = {"topics": topics} if topics else None
        count = 0
        try:
            for ev in session.stream_sse("/live/events", params=params):
                count += 1
                _print_event(ev, state.fmt)
                if limit and count >= limit:
                    break
        except KeyboardInterrupt:  # pragma: no cover - interactive
            pass

    @logs_app.command("stream")
    def stream_logs(
        ctx: typer.Context,
        source: Optional[list[str]] = typer.Option(
            None, "--source", help="log source filter (repeatable)"
        ),  # noqa: UP007
        node_id: Optional[str] = typer.Option(None, "--node-id"),  # noqa: UP007
        instance_id: Optional[str] = typer.Option(None, "--instance-id"),  # noqa: UP007
        deployment_id: Optional[str] = typer.Option(None, "--deployment-id"),  # noqa: UP007
        level: Optional[str] = typer.Option(None, "--level"),  # noqa: UP007
        search: Optional[str] = typer.Option(None, "--search"),  # noqa: UP007
        limit: Optional[int] = typer.Option(
            None, "--limit", "-n", help="Stop after N lines"
        ),  # noqa: UP007
    ) -> None:
        """Follow log lines from GET /logs/stream (Ctrl-C to stop)."""
        state = ctx.obj
        session = state.session()
        params = {
            k: v
            for k, v in {
                "source": source,
                "node_id": node_id,
                "instance_id": instance_id,
                "deployment_id": deployment_id,
                "level": level,
                "search": search,
            }.items()
            if v
        }
        count = 0
        try:
            for ev in session.stream_sse("/logs/stream", params=params or None):
                count += 1
                _print_event(ev, state.fmt)
                if limit and count >= limit:
                    break
        except KeyboardInterrupt:  # pragma: no cover - interactive
            pass

    @live_app.command("ws")
    def ws(ctx: typer.Context) -> None:
        """Print how to connect to the live WebSocket (the CLI streams over SSE instead)."""
        state = ctx.obj
        session = state.session()
        base = session.profile.base_url.replace("https://", "wss://").replace("http://", "ws://")
        creds = session.creds
        if not creds:
            raise ConfigError("not logged in")
        output.render(
            {
                "url": f"{base}/live/ws",
                "auth": "Authorization: Bearer <session token> (or the session cookie)",
                "note": "Use `gpudeck live events` for the same stream over SSE.",
            },
            fmt=state.fmt,
        )


def _print_event(ev: dict[str, str], fmt: str) -> None:
    data = ev.get("data", "")
    try:
        parsed = json.loads(data) if data else None
    except ValueError:
        parsed = None
    if fmt == "json":
        payload = {
            "event": ev.get("event"),
            "id": ev.get("id"),
            "data": parsed if parsed is not None else data,
        }
        output.console.print(json.dumps(payload, default=str), highlight=False)
        return
    label = ev.get("event") or "message"
    if isinstance(parsed, dict):
        summary = " ".join(f"{k}={v}" for k, v in parsed.items() if not isinstance(v, (dict, list)))
        output.console.print(f"[bold]{label}[/bold] {summary}")
    else:
        output.console.print(f"[bold]{label}[/bold] {data}")
