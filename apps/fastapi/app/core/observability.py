"""
Observability setup with Prometheus and OpenTelemetry.

Provides:
- Prometheus metrics (HTTP counters/histograms) and a /metrics endpoint
- OpenTelemetry tracing + metrics export (OTLP gRPC) with auto-instrumentation
- Helpers for custom spans in services

Domain metrics (GPU samples, engine sessions, usage) are registered by their
owning services with ``prometheus_client`` directly; this module only owns the
HTTP layer. The GPU/engine exporter lives under services/metrics.
"""

import logging
import os
import sys
from time import perf_counter
from typing import Optional

from app.core.logger import get_logger
from app.core.settings import settings
from fastapi import FastAPI, Request
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import (
    DEPLOYMENT_ENVIRONMENT,
    SERVICE_NAME,
    SERVICE_VERSION,
    Resource,
)
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.trace import Span, Status, StatusCode
from prometheus_client import Counter, Gauge, Histogram
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = get_logger(__name__)

# Prometheus Metrics (HTTP layer)
http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["process", "method", "endpoint", "status_code"],
)
http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["process", "method", "endpoint"],
)
http_requests_in_progress = Gauge(
    "http_requests_in_progress",
    "HTTP requests currently in progress",
    ["process", "method", "endpoint"],
)
celery_tasks_total = Counter("celery_tasks_total", "Total Celery tasks", ["task", "state"])
cache_hits_total = Counter("cache_hits_total", "Total cache hits")
cache_misses_total = Counter("cache_misses_total", "Total cache misses")

_otel_configured = False


class PrometheusMiddleware:
    """Per-route request counts, latency and in-flight gauge (pure ASGI).

    ``endpoint`` is the matched route template (``/v1/chat/completions``,
    ``/api/v1/nodes/{node_id}``) so label cardinality stays bounded; requests
    that match no route are counted under ``unmatched``. The in-flight gauge is
    labelled before routing, so its ``endpoint`` is always ``*``.
    """

    def __init__(self, app: ASGIApp, process: str = "admin"):
        self.app = app
        self.process = process

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "")
        in_progress = http_requests_in_progress.labels(
            process=self.process, method=method, endpoint="*"
        )
        in_progress.inc()
        status_holder = [500]

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder[0] = message["status"]
            await send(message)

        start_time = perf_counter()
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration = perf_counter() - start_time
            route = scope.get("route")
            endpoint = getattr(route, "path", None) or "unmatched"
            http_request_duration_seconds.labels(
                process=self.process, method=method, endpoint=endpoint
            ).observe(duration)
            http_requests_total.labels(
                process=self.process,
                method=method,
                endpoint=endpoint,
                status_code=status_holder[0],
            ).inc()
            in_progress.dec()


def scrape_token_ok(authorization: Optional[str]) -> bool:
    """True when PROMETHEUS_SCRAPE_TOKEN is unset or the bearer token matches (constant time)."""
    import hmac

    token = settings.PROMETHEUS_SCRAPE_TOKEN
    if not token:
        return True
    scheme, _, value = (authorization or "").partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(value.strip(), token)


def setup_prometheus(app: FastAPI):
    """Expose /metrics (manual route — instrumentator.expose() conflicts with middlewares)."""
    from prometheus_client import REGISTRY, generate_latest
    from starlette.responses import Response as StarletteResponse

    @app.get("/metrics", include_in_schema=False)
    async def metrics_endpoint(request: Request):
        # PROMETHEUS_SCRAPE_TOKEN (env.md) guards every /metrics: the API layer's carries
        # per-API-key usage series and is reachable by whoever reaches the serving port.
        if not scrape_token_ok(request.headers.get("authorization")):
            return StarletteResponse(
                "scrape token required\n",
                status_code=401,
                media_type="text/plain; charset=utf-8",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return StarletteResponse(generate_latest(REGISTRY), media_type="text/plain; charset=utf-8")

    logger.info("Prometheus metrics enabled at /metrics")


def _resource(service_name: str) -> Resource:
    return Resource.create(
        {
            SERVICE_NAME: service_name,
            SERVICE_VERSION: settings.APP_VERSION,
            DEPLOYMENT_ENVIRONMENT: settings.APP_ENV,
        }
    )


def setup_opentelemetry(app: FastAPI, service_name: str):
    """Setup OpenTelemetry tracing and metrics (OTLP when configured, console optionally)."""
    global _otel_configured

    # Under pytest neither exporter is useful: OTLP retries against an
    # unreachable endpoint and the console exporter writes after stdout closes.
    if "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules:
        logger.info("Running under pytest — skipping OpenTelemetry setup")
        return

    if not settings.OTEL_EXPORTER_OTLP_ENDPOINT:
        if settings.OTEL_CONSOLE_EXPORT and not _otel_configured:
            tracer_provider = TracerProvider(resource=_resource(service_name))
            tracer_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
            trace.set_tracer_provider(tracer_provider)
            _otel_configured = True
            FastAPIInstrumentor.instrument_app(app)
            logger.info("OpenTelemetry enabled with console exporter (development mode)")
        else:
            logger.info("OpenTelemetry endpoint not configured — tracing disabled")
        return

    if not _otel_configured:
        resource = _resource(service_name)
        tracer_provider = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(
                    endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT,
                    insecure=settings.APP_ENV != "production",
                )
            )
        )
        trace.set_tracer_provider(tracer_provider)

        metric_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(
                endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT,
                insecure=settings.APP_ENV != "production",
            ),
            export_interval_millis=60000,
        )
        metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[metric_reader]))

        HTTPXClientInstrumentor().instrument()
        try:
            RedisInstrumentor().instrument()
        except Exception as e:  # pragma: no cover - best effort
            logger.warning(f"Failed to instrument Redis: {e}")
        try:
            from app.db.postgres.connection import get_async_engine

            SQLAlchemyInstrumentor().instrument(engine=get_async_engine().sync_engine)
        except Exception as e:  # pragma: no cover - best effort
            logger.warning(f"Failed to instrument SQLAlchemy: {e}")
        _otel_configured = True

    FastAPIInstrumentor.instrument_app(app)
    logger.info(f"OpenTelemetry enabled: exporting to {settings.OTEL_EXPORTER_OTLP_ENDPOINT}")


def get_tracer(name: str):
    """Tracer for custom spans: ``with get_tracer(__name__).start_as_current_span("x"):``."""
    return trace.get_tracer(name)


def add_span_attributes(span: Optional[Span], attributes: dict):
    if span and span.is_recording():
        for key, value in attributes.items():
            span.set_attribute(key, value)


def record_exception(span: Optional[Span], exception: Exception):
    if span and span.is_recording():
        span.record_exception(exception)
        span.set_status(Status(StatusCode.ERROR, str(exception)))


def get_trace_context() -> dict:
    span = trace.get_current_span()
    if span and span.is_recording():
        ctx = span.get_span_context()
        return {
            "trace_id": format(ctx.trace_id, "032x"),
            "span_id": format(ctx.span_id, "016x"),
        }
    return {}


def inject_trace_context_to_logs():
    class TraceContextFilter(logging.Filter):
        def filter(self, record):
            context = get_trace_context()
            record.trace_id = context.get("trace_id", "")
            record.span_id = context.get("span_id", "")
            return True

    logging.root.addFilter(TraceContextFilter())


def setup_observability(app: FastAPI, process: str = "admin"):
    """Setup all observability components for one FastAPI process."""
    logger.info(f"Setting up observability for process={process}...")
    if settings.METRICS_ENABLED:
        setup_prometheus(app)
        app.add_middleware(PrometheusMiddleware, process=process)
    setup_opentelemetry(app, f"{settings.OTEL_SERVICE_NAME}-{process}")
    inject_trace_context_to_logs()
    logger.info("Observability setup complete")


def start_process_metrics_server(port: int, process: str) -> bool:
    """
    Prometheus listener for processes without an HTTP app (worker, scheduler).
    With ``PROMETHEUS_MULTIPROC_DIR`` set (Celery prefork) the per-child files are aggregated.
    """
    if not settings.METRICS_ENABLED or port <= 0:
        return False
    from prometheus_client import CollectorRegistry, start_http_server

    registry = None
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        from prometheus_client import multiprocess

        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
    try:
        if registry is not None:
            start_http_server(port, addr=settings.METRICS_BIND_HOST, registry=registry)
        else:
            start_http_server(port, addr=settings.METRICS_BIND_HOST)
    except OSError as exc:
        logger.warning(f"{process} metrics listener on :{port} not started: {exc}")
        return False
    logger.info(f"{process} Prometheus metrics on {settings.METRICS_BIND_HOST}:{port}/metrics")
    return True
