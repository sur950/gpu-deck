"""
Trace index lifecycle hooks.

On startup of every process, when ``TRACE_INDEX_ENABLED``: make sure an SDK
``TracerProvider`` is installed (``app/core/observability.py`` installs one
only when an OTLP endpoint or console export is configured; otherwise this
hook installs one with the ``OTEL_TRACES_SAMPLER``/``_ARG`` sampler) and attach
a ``BatchSpanProcessor(TraceIndexExporter())`` so spans land in ``traces``.
Skipped under pytest (tests attach the exporter explicitly).
"""

from __future__ import annotations

import os
import sys
from typing import Optional

from app.core.lifecycle import on_shutdown, on_startup
from app.core.logger import get_logger
from app.core.settings import settings

logger = get_logger(__name__)

_processor = None


def build_sampler(name: Optional[str], arg: float):
    from opentelemetry.sdk.trace import sampling

    name = (name or "parentbased_always_on").lower()
    ratio = max(0.0, min(float(arg), 1.0))
    if name == "always_on":
        return sampling.ALWAYS_ON
    if name == "always_off":
        return sampling.ALWAYS_OFF
    if name == "traceidratio":
        return sampling.TraceIdRatioBased(ratio)
    if name == "parentbased_always_off":
        return sampling.ParentBased(sampling.ALWAYS_OFF)
    if name == "parentbased_traceidratio":
        return sampling.ParentBased(sampling.TraceIdRatioBased(ratio))
    return sampling.ParentBased(sampling.ALWAYS_ON)


def install_trace_index(process: str) -> bool:
    """Attach the index exporter to the global SDK provider (creating one if needed)."""
    global _processor
    if _processor is not None or not settings.TRACE_INDEX_ENABLED:
        return False
    from app.services.traces.exporter import TraceIndexExporter
    from opentelemetry import trace
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider(
            resource=Resource.create({SERVICE_NAME: f"{settings.OTEL_SERVICE_NAME}-{process}"}),
            sampler=build_sampler(settings.OTEL_TRACES_SAMPLER, settings.OTEL_TRACES_SAMPLER_ARG),
        )
        trace.set_tracer_provider(provider)
    _processor = BatchSpanProcessor(TraceIndexExporter())
    provider.add_span_processor(_processor)
    logger.info(f"trace index enabled for process={process}")
    return True


def _under_pytest() -> bool:
    return "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules


def _make_startup(process: str):
    async def _start() -> None:
        if not _under_pytest():
            install_trace_index(process)

    _start.__name__ = f"install_trace_index_{process}"
    return _start


for _proc in ("admin", "serving", "worker", "scheduler"):
    on_startup(_proc)(_make_startup(_proc))


@on_shutdown("*")
async def flush_trace_index() -> None:
    global _processor
    if _processor is not None:
        _processor.shutdown()
        _processor = None
