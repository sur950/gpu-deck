"""
Per-request serving context: what the pipeline, translators and the metering
hand-off need to know about one request (format, endpoint, API key, timing,
route target, usage).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.core.settings import settings
from app.services.serving.auth import ApiKeyContext
from app.services.serving.routing import RouteTarget
from fastapi import Request


def _uuid_or_new(value: Optional[str]) -> str:
    if value:
        try:
            return str(uuid.UUID(str(value)))
        except (ValueError, AttributeError):
            pass
    return str(uuid.uuid4())


@dataclass
class ServingContext:
    request: Request
    fmt: str
    endpoint: str
    api_key: ApiKeyContext
    request_id: str = ""
    started: float = field(default_factory=time.perf_counter)
    started_ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    model: str = ""
    stream: bool = False
    target: Optional[RouteTarget] = None
    ttft_ms: Optional[int] = None
    concurrency_at_start: int = 0
    anthropic_version: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    metered: bool = False
    # Phase durations in ms (app.services.serving.timing): route, queue, connect, rtt, net,
    # engine, upstream; ttft and total are derived when the breakdown is read.
    timing: Dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.request_id:
            self.request_id = _uuid_or_new(getattr(self.request.state, "request_id", None))
        if not self.anthropic_version:
            self.anthropic_version = (
                self.request.headers.get("anthropic-version")
                or settings.SERVING_ANTHROPIC_DEFAULT_VERSION
            )

    @property
    def client_ip(self) -> Optional[str]:
        return getattr(self.request.state, "client_ip", None) or (
            self.request.client.host if self.request.client else None
        )

    def elapsed_ms(self) -> int:
        return int((time.perf_counter() - self.started) * 1000)

    def mark_first_byte(self) -> None:
        if self.ttft_ms is None:
            self.ttft_ms = self.elapsed_ms()

    def timing_breakdown(self, final: bool = True) -> Dict[str, float]:
        """Known phases plus ttft and (``final``) total, in ms."""
        out = dict(self.timing)
        if self.ttft_ms is not None:
            out["ttft_ms"] = float(self.ttft_ms)
        if final:
            out["total_ms"] = round((time.perf_counter() - self.started) * 1000.0, 3)
        return out

    def _server_timing(self, streaming: bool) -> Optional[str]:
        from app.services.serving import timing

        if timing.mode() == "off" or (not self.timing and streaming):
            return None
        region = node_name = None
        if timing.mode() == "detailed":
            from app.services.latency.store import api_layer_identity

            region = api_layer_identity()[1]
            if self.target is not None:
                from app.services.serving.routing import get_routing_table

                node = get_routing_table().nodes.get(self.target.instance.node_id) or {}
                node_name = node.get("name")
        return timing.server_timing(
            self.timing_breakdown(final=not streaming),
            include_total=not streaming,
            region=region,
            node_name=node_name,
        )

    def response_headers(self, streaming: bool = False) -> Dict[str, str]:
        headers: Dict[str, str] = {
            "Cache-Control": "no-store",
            "request-id": self.request_id,
        }
        if self.fmt == "anthropic":
            headers["anthropic-version"] = self.anthropic_version
        elif self.fmt == "openai":
            headers["openai-processing-ms"] = str(self.elapsed_ms())
        if settings.SERVING_EXPOSE_ROUTING_HEADERS and self.target is not None:
            m, i = self.target.model, self.target.instance
            if m.model_id:
                headers["x-gpudeck-model"] = str(m.model_id)
            if m.deployment_id:
                headers["x-gpudeck-deployment"] = str(m.deployment_id)
            headers["x-gpudeck-instance"] = i.instance_id
            headers["x-gpudeck-node"] = i.node_id
        if streaming:
            headers["X-Accel-Buffering"] = "no"
        try:
            server_timing = self._server_timing(streaming)
        except Exception:  # noqa: BLE001 - never fail a response over a diagnostics header
            server_timing = None
        if server_timing:
            headers["Server-Timing"] = server_timing
        return headers
