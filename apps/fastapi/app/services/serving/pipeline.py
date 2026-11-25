"""
The proxy pipeline: route -> forward (with failover before the first byte) ->
stream back -> meter after the response.

``ProxyEngine.open`` resolves the model to an instance from the in-memory
routing table, rewrites the request ``model`` to ``served_model_name``, dials
the node-agent proxy (or the dev engine) and returns an ``UpstreamResult``
holding the open httpx response. Connection errors and ``503
instance_draining/instance_busy`` answers are retried on another instance
while no byte has been sent to the client; the failing instance is ejected
for ``SERVING_EJECT_SECONDS``.

Streaming responses are forwarded frame by frame; the usage collector reads
the final frame; the metering event is enqueued after the last byte through
``services.metering.service.record_request`` (never on the hot path).
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator, Callable, Dict, Optional, Tuple

import httpx
from app.core.logger import get_logger
from app.core.settings import settings
from app.services.serving import metrics as prom
from app.services.serving.context import ServingContext
from app.services.serving.errors import (
    ServingError,
    stream_error_frame,
    upstream_status_to_error,
)
from app.services.serving.inflight import get_inflight_tracker
from app.services.serving.latency import get_latency_tracker
from app.services.serving.latency import rtt_ms as latency_rtt_ms
from app.services.serving.routing import RouteTarget, get_routing_table
from app.services.serving.sse import (
    ENGINE_UPSTREAM_NAMES,
    compile_model_rewrite,
    iter_ndjson_lines,
    iter_sse_frames,
    rewrite_model_bytes,
)
from app.services.serving.timing import (
    PROXY_TIMING_HEADER,
    apply_proxy_timing,
    parse_proxy_timing,
)
from app.services.serving.upstream import (
    get_upstream_client,
    timeouts,
    upstream_headers,
)
from app.services.serving.usage import Usage, UsageCollector
from starlette.background import BackgroundTask
from starlette.responses import JSONResponse, Response, StreamingResponse

logger = get_logger(__name__)

_RETRYABLE_PROXY_CODES = {"instance_draining", "instance_busy"}
# Node-proxy error codes (protocol.md §6) -> (status, serving error code, failover allowed).
_PROXY_ERRORS = {
    "engine_timeout": (504, "upstream_timeout", False),
    "engine_unavailable": (502, "upstream_error", True),
    "instance_not_found": (502, "upstream_error", True),
    "path_not_allowed": (403, "path_not_allowed", False),
    "body_too_large": (413, "request_too_large", False),
    "bad_path": (400, "invalid_request_error", False),
}


class UpstreamResult:
    """An open upstream response plus the route it came from."""

    def __init__(self, ctx: ServingContext, response: httpx.Response, target: RouteTarget) -> None:
        self.ctx = ctx
        self.response = response
        self.target = target
        self.rewrite = (
            compile_model_rewrite(
                target.instance.served_model_name,
                target.requested_name,
                ENGINE_UPSTREAM_NAMES.get(target.instance.engine, ()),
            )
            if settings.SERVING_REWRITE_RESPONSE_MODEL
            else None
        )
        self._closed = False

    @property
    def status_code(self) -> int:
        return self.response.status_code

    @property
    def headers(self) -> httpx.Headers:
        return self.response.headers

    async def read_bytes(self) -> bytes:
        try:
            data = await self.response.aread()
            self.ctx.mark_first_byte()
            return rewrite_model_bytes(data, self.rewrite)
        except httpx.TimeoutException:
            raise ServingError.upstream_timeout("upstream response timed out")
        except httpx.HTTPError as exc:
            raise ServingError.upstream_error(f"upstream response broken: {exc}")
        finally:
            await self.close()

    async def read_json(self) -> Dict[str, Any]:
        data = await self.read_bytes()
        try:
            parsed = json.loads(data)
        except (json.JSONDecodeError, ValueError):
            raise ServingError.upstream_error("upstream returned non-JSON body")
        return parsed if isinstance(parsed, dict) else {"data": parsed}

    async def aiter_raw(self) -> AsyncIterator[bytes]:
        """Raw upstream chunks (no model rewrite: a name may straddle two chunks)."""
        deadline = time.perf_counter() + settings.SERVING_REQUEST_TIMEOUT_SECONDS
        try:
            async for chunk in self.response.aiter_raw():
                if chunk:
                    self.ctx.mark_first_byte()
                    yield chunk
                if time.perf_counter() > deadline:
                    raise ServingError.upstream_timeout("request exceeded the time limit")
        except httpx.ReadTimeout:
            raise ServingError.upstream_timeout("upstream stream stalled")
        except (httpx.RemoteProtocolError, httpx.ReadError) as exc:
            raise ServingError.upstream_error(f"upstream stream broken: {exc}")
        finally:
            await self.close()

    async def aiter_frames(self) -> AsyncIterator[bytes]:
        """Complete SSE event blocks, model name rewritten per frame."""
        async for frame in iter_sse_frames(self.aiter_raw()):
            yield rewrite_model_bytes(frame, self.rewrite) if self.rewrite else frame

    async def aiter_lines(self) -> AsyncIterator[bytes]:
        """Complete NDJSON lines, model name rewritten per line."""
        async for line in iter_ndjson_lines(self.aiter_raw()):
            yield rewrite_model_bytes(line, self.rewrite) if self.rewrite else line

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                await self.response.aclose()
            except Exception:  # noqa: BLE001
                pass


class ProxyEngine:
    def __init__(self) -> None:
        self.table = get_routing_table()
        self.inflight = get_inflight_tracker()

    # -- routing ----------------------------------------------------------

    async def resolve(
        self, ctx: ServingContext, model: str, endpoint: Optional[str] = None
    ) -> RouteTarget:
        endpoint = endpoint or ctx.endpoint
        if not self.table.loaded:
            raise ServingError(503, "model_not_ready", "routing table not loaded", retry_after=2)
        started = time.perf_counter()
        try:
            target = self.table.resolve(model, endpoint, self.inflight.counts)
            ctx.timing["route_ms"] = _ms_since(started)
            return target
        except ServingError as err:
            ctx.timing["route_ms"] = _ms_since(started)
            if err.code != "model_not_ready":
                raise
            waited = time.perf_counter()
            try:
                return await self._wait_for_instance(ctx, model, endpoint, err)
            finally:
                ctx.timing["queue_ms"] = ctx.timing.get("queue_ms", 0.0) + _ms_since(waited)

    async def _wait_for_instance(
        self, ctx: ServingContext, model: str, endpoint: str, err: ServingError
    ) -> RouteTarget:
        entry = self.table.get(model)
        if entry is None:
            raise err
        waiting = bool(entry.loading_instances) and entry.desired_running
        if not waiting and settings.SERVING_ON_DEMAND_LOAD and entry.deployment_id:
            await request_on_demand_load(entry.deployment_id)
            waiting = True
        if not waiting:
            raise err
        deadline = time.perf_counter() + settings.SERVING_NO_INSTANCE_WAIT_SECONDS
        while time.perf_counter() < deadline:
            await asyncio.sleep(0.5)
            try:
                return self.table.resolve(model, endpoint, self.inflight.counts)
            except ServingError as retry_err:
                if retry_err.code != "model_not_ready":
                    raise
        raise ServingError.model_not_ready(
            model, retry_after=settings.SERVING_NO_INSTANCE_WAIT_SECONDS
        )

    # -- forwarding -------------------------------------------------------

    async def open(
        self,
        ctx: ServingContext,
        engine_path: str,
        body: Optional[Dict[str, Any]] = None,
        *,
        content: Optional[bytes] = None,
        headers: Optional[Dict[str, str]] = None,
        stream: bool = False,
        method: str = "POST",
        model: Optional[str] = None,
        endpoint: Optional[str] = None,
        rewrite_model: bool = True,
    ) -> UpstreamResult:
        """Route and send; returns the open upstream response (status may be non-2xx)."""
        if self.inflight.total >= settings.SERVING_MAX_CONCURRENT_REQUESTS:
            raise ServingError(503, "instance_busy", "too many concurrent requests", retry_after=1)
        requested = model or ctx.model or (body or {}).get("model") or ""
        if not requested:
            raise ServingError.invalid_request("model: field required", "model")
        ctx.model = requested
        target = await self.resolve(ctx, requested, endpoint)
        get_latency_tracker().count_request(
            target.instance.deployment_id or target.model.deployment_id
        )
        tried: set = set()
        attempts = 0
        max_retries = target.model.max_retries if target.model.failover else 0
        last_error: Optional[ServingError] = None
        first_attempt = time.perf_counter()
        while True:
            attempts += 1
            if attempts > 1:
                # Failed attempts that were retried elsewhere count as waiting in the API layer.
                ctx.timing["queue_ms"] = ctx.timing.get("queue_ms", 0.0) + _ms_since(first_attempt)
                first_attempt = time.perf_counter()
            tried.add(target.instance.instance_id)
            ctx.target = target
            try:
                result = await self._send(
                    ctx,
                    target,
                    engine_path,
                    body,
                    content,
                    headers,
                    stream,
                    method,
                    rewrite_model,
                )
            except ServingError as err:
                last_error = err
                if err.code in ("upstream_error",) and err.status_code == 502:
                    self.table.eject(target.instance.instance_id)
                if attempts > max_retries or not target.model.failover:
                    raise
                target = self._failover(target, tried)
                if target is None:
                    raise last_error
                continue
            if result.status_code == 503 and attempts <= max_retries:
                code = await _proxy_error_code(result)
                if code in _RETRYABLE_PROXY_CODES:
                    await result.close()
                    nxt = self._failover(target, tried)
                    if nxt is None:
                        raise ServingError(
                            429 if code == "instance_busy" else 503,
                            code,
                            f"upstream: {code}",
                            retry_after=2,
                        )
                    target = nxt
                    continue
            if await _router_child_gone(result):
                # llama.cpp router whose model process exited: the instance is down, not the
                # request bad. Eject it and retry elsewhere before the agent's health check
                # marks it unhealthy (else every request in that window fails with a 400).
                await result.close()
                self.table.eject(target.instance.instance_id)
                nxt = self._failover(target, tried) if attempts <= max_retries else None
                if nxt is not None:
                    target = nxt
                    continue
                raise ServingError(503, "model_not_ready", "instance is not loaded", retry_after=2)
            if result.status_code >= 400 and "x-gpudeck-instance" not in result.response.headers:
                # Error generated by the node proxy itself (not the engine): answer in the
                # client's format instead of passing the agent's error body through.
                code = await _proxy_error_code(result)
                mapped = _PROXY_ERRORS.get(code or "")
                if mapped is not None:
                    status, our_code, can_failover = mapped
                    await result.close()
                    err = ServingError(status, our_code, f"node proxy: {code}")
                    if can_failover:
                        self.table.eject(target.instance.instance_id)
                        nxt = self._failover(target, tried) if attempts <= max_retries else None
                        if nxt is not None:
                            target = nxt
                            continue
                    raise err
            return result

    def _failover(self, target: RouteTarget, tried: set) -> Optional[RouteTarget]:
        nxt = self.table.pick(target.model, self.inflight.counts, exclude=tried)
        if nxt is None:
            return None
        return RouteTarget(model=target.model, instance=nxt, requested_name=target.requested_name)

    async def _send(
        self,
        ctx: ServingContext,
        target: RouteTarget,
        engine_path: str,
        body: Optional[Dict[str, Any]],
        content: Optional[bytes],
        headers: Optional[Dict[str, str]],
        stream: bool,
        method: str,
        rewrite_model: bool,
    ) -> UpstreamResult:
        inst = target.instance
        url = inst.engine_url(engine_path)
        req_headers = upstream_headers(
            ctx.request_id,
            ctx.api_key.id if ctx.api_key else None,
            target.model.deployment_id,
            ctx.fmt,
        )
        if headers:
            req_headers.update(headers)
        data: Optional[bytes] = content
        if body is not None:
            if rewrite_model and inst.served_model_name:
                body = {**body, "model": inst.served_model_name}
            data = json.dumps(body, ensure_ascii=False).encode()
            req_headers.setdefault("Content-Type", "application/json")
        client = get_upstream_client(self.table.ca_bundle_pem, url)
        marks: Dict[str, float] = {}
        request = client.build_request(
            method,
            url,
            content=data,
            headers=req_headers,
            timeout=timeouts(stream),
            extensions={"trace": _timing_trace(marks)},
        )
        ctx.concurrency_at_start = self.inflight.acquire(inst.instance_id)
        prom.inflight_gauge.inc()
        try:
            sent = time.perf_counter()
            response = await client.send(request, stream=True)
            _record_upstream_timing(ctx, inst.node_id, marks, sent, response)
        except (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.RemoteProtocolError,
        ) as exc:
            self._release(inst.instance_id)
            raise ServingError.upstream_error(f"cannot reach instance {inst.instance_id}: {exc}")
        except httpx.TimeoutException as exc:
            self._release(inst.instance_id)
            raise ServingError.upstream_timeout(f"upstream timed out: {exc}")
        except httpx.HTTPError as exc:
            self._release(inst.instance_id)
            raise ServingError.upstream_error(f"upstream error: {exc}")
        result = UpstreamResult(ctx, response, target)
        original_close = result.close

        async def close_and_release() -> None:
            await original_close()
            self._release(inst.instance_id)

        result.close = close_and_release  # type: ignore[method-assign]
        return result

    def _release(self, instance_id: str) -> None:
        self.inflight.release(instance_id)
        prom.inflight_gauge.dec()


def _ms_since(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 3)


def _timing_trace(marks: Dict[str, float]) -> Callable[[str, Dict[str, Any]], Any]:
    """httpcore ``trace`` callback: connection setup and request/response header times."""

    async def trace(event: str, info: Dict[str, Any]) -> None:
        now = time.perf_counter()
        if event == "connection.connect_tcp.started":
            marks.setdefault("connect_started", now)
        elif event in ("connection.connect_tcp.complete", "connection.start_tls.complete"):
            marks["connect_done"] = now
        elif event.endswith("send_request_headers.started"):
            marks.setdefault("sent", now)
        elif event.endswith("receive_response_headers.complete"):
            marks["received"] = now

    return trace


def _record_upstream_timing(
    ctx: ServingContext,
    node_id: str,
    marks: Dict[str, float],
    sent: float,
    response: httpx.Response,
) -> None:
    """Connect / upstream / rtt / engine / net of the attempt that answered (never raises)."""
    try:
        now = time.perf_counter()
        connect = 0.0
        if "connect_started" in marks and "connect_done" in marks:
            connect = max(0.0, marks["connect_done"] - marks["connect_started"]) * 1000.0
        if "sent" in marks and "received" in marks:
            upstream = max(0.0, marks["received"] - marks["sent"]) * 1000.0
        else:
            upstream = max(0.0, (now - sent) * 1000.0 - connect)
        ctx.timing["connect_ms"] = round(connect, 3)
        ctx.timing["upstream_ms"] = round(upstream, 3)
        rtt = latency_rtt_ms(node_id)
        if rtt is not None:
            ctx.timing["rtt_ms"] = round(rtt, 3)
        reported = response.headers.get(PROXY_TIMING_HEADER)
        if reported is not None:
            del response.headers[PROXY_TIMING_HEADER]
            apply_proxy_timing(ctx.timing, parse_proxy_timing(reported))
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"upstream timing not recorded: {exc}")


_ROUTER_CHILD_GONE = b"model is not loaded"


async def _router_child_gone(result: UpstreamResult) -> bool:
    """
    A llama.cpp router answered ``400 model is not loaded`` for an instance GPU Deck loaded:
    its child process exited (crash / OOM / killed). Reads only small 400 bodies from
    llama.cpp; the body stays readable for the normal error path.
    """
    if result.status_code != 400 or getattr(result.target.instance, "engine", None) != "llama_cpp":
        return False
    old = result.response
    try:
        raw = await old.aread()
    except Exception:  # noqa: BLE001
        return False
    finally:
        await old.aclose()
    headers = [
        (k, v)
        for k, v in old.headers.multi_items()
        if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")
    ]
    result.response = httpx.Response(
        old.status_code, headers=headers, content=raw, request=old.request
    )
    return _ROUTER_CHILD_GONE in raw


async def _proxy_error_code(result: UpstreamResult) -> Optional[str]:
    try:
        raw = await result.response.aread()
        parsed = json.loads(raw)
        return ((parsed.get("error") or {}).get("code")) if isinstance(parsed, dict) else None
    except Exception:  # noqa: BLE001
        return None


async def request_on_demand_load(deployment_id: str) -> None:
    """Ask the control plane to start a deployment (Redis signal the jobs/admin process acts on)."""
    from app.db.redis.client import get_redis, redis_key

    try:
        client = await get_redis()
        await client.publish(
            redis_key("deployments", "start-requested"),
            json.dumps({"deployment_id": deployment_id, "source": "api_layer"}),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"on-demand load signal failed: {exc}")


_engine: Optional[ProxyEngine] = None


def get_engine() -> ProxyEngine:
    global _engine
    if _engine is None:
        _engine = ProxyEngine()
    return _engine


# ---------------------------------------------------------------------------
# Response helpers used by the routes
# ---------------------------------------------------------------------------


def meter_task(
    ctx: ServingContext, usage: Usage, status: str, http_status: int, error_code=None
) -> BackgroundTask:
    from app.services.metering.service import record_request

    return BackgroundTask(record_request, ctx, usage, status, http_status, error_code)


def json_response(
    ctx: ServingContext, payload: Any, usage: Usage, status_code: int = 200
) -> JSONResponse:
    headers = ctx.response_headers()
    status = "success" if status_code < 400 else "error"
    prom.observe(ctx, status_code)
    return JSONResponse(
        content=payload,
        status_code=status_code,
        headers=headers,
        background=meter_task(ctx, usage, status, status_code),
    )


def bytes_response(
    ctx: ServingContext,
    data: bytes,
    media_type: str,
    usage: Usage,
    status_code: int = 200,
) -> Response:
    headers = ctx.response_headers()
    prom.observe(ctx, status_code)
    return Response(
        content=data,
        status_code=status_code,
        media_type=media_type,
        headers=headers,
        background=meter_task(ctx, usage, "success" if status_code < 400 else "error", status_code),
    )


async def raise_for_upstream_status(result: UpstreamResult) -> None:
    """Engine 4xx/5xx -> ServingError carrying the body (after closing the response)."""
    if result.status_code < 400:
        return
    data = await result.read_bytes()
    raise upstream_status_to_error(result.status_code, data)


def passthrough_error_response(
    ctx: ServingContext, result_status: int, body: bytes, content_type: str
) -> Response:
    """Engine 4xx passed through unchanged (serving.md §4) with our headers and metering."""
    headers = ctx.response_headers()
    code = "upstream_error" if result_status >= 500 else "invalid_request_error"
    headers["x-gpudeck-error-code"] = code
    prom.observe(ctx, result_status)
    return Response(
        content=body,
        status_code=result_status,
        media_type=content_type or "application/json",
        headers=headers,
        background=meter_task(ctx, Usage(), "error", result_status, code),
    )


def streaming_response(
    ctx: ServingContext,
    frames: AsyncIterator[bytes],
    collector: UsageCollector,
    media_type: str,
    *,
    on_finish: Optional[Callable[[], None]] = None,
) -> StreamingResponse:
    """Wrap a translated/forwarded frame iterator: errors mid-stream, usage, metering."""

    async def generator() -> AsyncIterator[bytes]:
        status, http_status, error_code = "success", 200, None
        try:
            async for frame in frames:
                yield frame
        except asyncio.CancelledError:
            status, error_code = "cancelled", "client_disconnected"
            raise
        except ServingError as err:
            status, http_status, error_code = (
                "timeout" if err.code == "upstream_timeout" else "error",
                err.status_code,
                err.code,
            )
            yield stream_error_frame(ctx.fmt, err)
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).error(f"stream failed for {ctx.request_id}")
            broken = ServingError.upstream_error("stream interrupted")
            status, http_status, error_code = "error", 502, broken.code
            yield stream_error_frame(ctx.fmt, broken)
        finally:
            usage = collector.finalize()
            prom.observe(ctx, http_status)
            if on_finish:
                try:
                    on_finish()
                except Exception:  # noqa: BLE001
                    pass
            from app.services.metering.service import record_request

            await record_request(ctx, usage, status, http_status, error_code)

    return StreamingResponse(
        generator(), media_type=media_type, headers=ctx.response_headers(streaming=True)
    )


def stream_options_for_client(body: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    """Inject stream_options.include_usage for the engine; report if the client asked for it."""
    opts = body.get("stream_options") or {}
    client_wants = bool(opts.get("include_usage"))
    new_body = dict(body)
    new_body["stream_options"] = {**opts, "include_usage": True}
    return new_body, client_wants
