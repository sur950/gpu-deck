"""
Text endpoints: OpenAI chat/completions and completions (pass-through),
Anthropic messages + count_tokens (translated unless the engine speaks
Anthropic), Ollama chat/generate/embed (translated) and the Responses API
(translated to chat completions, with background jobs stored in Redis).
"""

from __future__ import annotations

import asyncio
import copy
import json
import time
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from app.core.logger import get_logger
from app.core.settings import settings
from app.services.serving import compaction as comp
from app.services.serving.context import ServingContext
from app.services.serving.errors import ServingError
from app.services.serving.pipeline import (
    UpstreamResult,
    get_engine,
    json_response,
    passthrough_error_response,
    raise_for_upstream_status,
    stream_options_for_client,
    streaming_response,
)
from app.services.serving.sse import DONE, parse_sse_json, sse_event
from app.services.serving.translate import anthropic as tr_a
from app.services.serving.translate import ollama as tr_o
from app.services.serving.translate import responses as tr_r
from app.services.serving.usage import (
    Usage,
    UsageCollector,
    estimate_tokens,
    extract_anthropic_usage,
    extract_ollama_usage,
    extract_openai_usage,
)
from starlette.responses import Response

logger = get_logger(__name__)

SSE = "text/event-stream"
NDJSON = "application/x-ndjson"


async def _error_or_none(
    ctx: ServingContext, result: UpstreamResult, translated: bool = False
) -> Optional[Response]:
    """
    Engine 4xx/5xx. Same format as the client: passed through unchanged
    (serving.md §4). Translated requests (Anthropic / Ollama client on an
    OpenAI engine): raised as a ServingError so the client gets the error in
    its own format (status and engine message preserved).
    """
    if result.status_code < 400:
        return None
    if translated:
        await raise_for_upstream_status(result)
    data = await result.read_bytes()
    return passthrough_error_response(
        ctx,
        result.status_code,
        data,
        result.headers.get("content-type", "application/json"),
    )


# ---------------------------------------------------------------------------
# OpenAI chat / completions pass-through
# ---------------------------------------------------------------------------


async def passthrough_openai_frames(
    frames: AsyncIterator[bytes], collector: UsageCollector, strip_usage_chunk: bool
) -> AsyncIterator[bytes]:
    async for frame in frames:
        if b'"usage"' in frame:
            _, chunk, done = parse_sse_json(frame)
            if chunk is not None:
                collector.feed_openai_chunk(chunk)
                if strip_usage_chunk and not chunk.get("choices"):
                    continue
        else:
            collector.note_frame_bytes(frame)
        yield frame


async def openai_passthrough(
    ctx: ServingContext, body: Dict[str, Any], engine_path: str
) -> Response:
    engine = get_engine()
    stream = bool(body.get("stream"))
    ctx.stream = stream
    send_body, client_wants_usage = stream_options_for_client(body) if stream else (body, False)
    result = await engine.open(ctx, engine_path, send_body, stream=stream)
    err = await _error_or_none(ctx, result)
    if err is not None:
        return err
    if not stream:
        data = await result.read_json()
        return json_response(ctx, data, extract_openai_usage(data, ctx.endpoint))
    collector = UsageCollector("openai", ctx.endpoint)
    frames = passthrough_openai_frames(result.aiter_frames(), collector, not client_wants_usage)
    return streaming_response(ctx, frames, collector, SSE)


# ---------------------------------------------------------------------------
# Anthropic messages
# ---------------------------------------------------------------------------


def _anthropic_headers(ctx: ServingContext) -> Dict[str, str]:
    headers = {"anthropic-version": ctx.anthropic_version}
    beta = ctx.request.headers.get("anthropic-beta")
    if beta:
        headers["anthropic-beta"] = beta
    return headers


async def _anthropic_native(ctx: ServingContext, body: Dict[str, Any]) -> Response:
    engine = get_engine()
    stream = bool(body.get("stream"))
    ctx.stream = stream
    result = await engine.open(
        ctx, "v1/messages", body, stream=stream, headers=_anthropic_headers(ctx)
    )
    err = await _error_or_none(ctx, result)
    if err is not None:
        return err
    if not stream:
        data = await result.read_json()
        return json_response(ctx, data, extract_anthropic_usage(data))
    collector = UsageCollector("anthropic", ctx.endpoint)

    async def frames() -> AsyncIterator[bytes]:
        async for frame in result.aiter_frames():
            _, event, _ = parse_sse_json(frame)
            if event is not None:
                collector.feed_anthropic_event(event)
            yield frame

    return streaming_response(ctx, frames(), collector, SSE)


async def _run_summary(
    ctx: ServingContext, body: Dict[str, Any], instructions: Optional[str]
) -> Tuple[Optional[str], str, Dict[str, int]]:
    """Run the compaction summariser on the same model. Returns (summary, stop_reason, usage)."""
    engine = get_engine()
    target = await engine.resolve(ctx, body["model"], "messages")
    req = comp.build_summary_request(
        body, target.instance.served_model_name or body["model"], instructions
    )
    result = await engine.open(
        ctx,
        "v1/chat/completions",
        req,
        stream=False,
        model=body["model"],
        endpoint="messages",
    )
    if result.status_code >= 400:
        data = await result.read_bytes()
        from app.services.serving.errors import upstream_status_to_error

        raise upstream_status_to_error(result.status_code, data)
    data = await result.read_json()
    usage = extract_openai_usage(data)
    summary, stop_reason = comp.summary_from_openai_response(data)
    return (
        summary,
        stop_reason,
        {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens},
    )


def _compaction_stream(ctx: ServingContext, payload: Dict[str, Any]) -> Response:
    """Stream a compaction-only message (block arrives whole, no deltas)."""
    collector = UsageCollector("anthropic", ctx.endpoint)
    collector.saw_usage = True
    it = (payload["usage"].get("iterations") or [{}])[0]
    collector.usage.input_tokens = int(it.get("input_tokens", 0))
    collector.usage.output_tokens = int(it.get("output_tokens", 0))

    async def frames() -> AsyncIterator[bytes]:
        msg = {**payload, "content": [], "stop_reason": None}
        yield sse_event("message_start", {"type": "message_start", "message": msg})
        for idx, block in enumerate(payload["content"]):
            yield sse_event(
                "content_block_start",
                {"type": "content_block_start", "index": idx, "content_block": block},
            )
            yield sse_event("content_block_stop", {"type": "content_block_stop", "index": idx})
        yield sse_event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": payload["stop_reason"], "stop_sequence": None},
                "usage": payload["usage"],
            },
        )
        yield sse_event("message_stop", {"type": "message_stop"})

    return streaming_response(ctx, frames(), collector, SSE)


async def anthropic_messages(ctx: ServingContext, body: Dict[str, Any]) -> Response:
    engine = get_engine()
    if not body.get("model"):
        raise ServingError.invalid_request("model: field required", "model")
    ctx.model = body["model"]
    stream = bool(body.get("stream"))
    ctx.stream = stream
    entry = engine.table.get(body["model"])
    if entry is not None and entry.speaks("anthropic"):
        return await _anthropic_native(ctx, body)

    comp.validate_compaction_request(body)
    messages = body.get("messages") or []
    if not isinstance(messages, list) or not messages:
        raise ServingError.invalid_request("messages: field required", "messages")
    incoming_summary = comp.validate_incoming_compaction(messages)
    ctx.metadata["anthropic_betas"] = comp.requested_betas(ctx.request.headers) or None

    # 1. on-demand compaction: summarise and return the block alone
    if comp.wants_on_demand_compaction(body):
        instructions = (body.get("compaction") or {}).get("instructions")
        summary, stop_reason, it_usage = await _run_summary(ctx, body, instructions)
        ctx.metadata["compaction"] = "on_demand"
        payload = tr_a.compaction_only_response(body["model"], summary, stop_reason, it_usage)
        if stream:
            return _compaction_stream(ctx, payload)
        usage = Usage(
            input_tokens=it_usage["input_tokens"],
            output_tokens=it_usage["output_tokens"],
        )
        usage.extra["compaction_input_tokens"] = it_usage["input_tokens"]
        return json_response(ctx, payload, usage)

    # 2. context editing (clear_*) applied client-side
    applied_cm: Optional[Dict[str, Any]] = None
    approx_input = estimate_tokens(comp.render_conversation(messages, body.get("system")))
    clearing = comp.clearing_edits(body)
    if clearing:
        messages, applied = comp.apply_clearing_edits(messages, clearing, approx_input)
        body = {**body, "messages": messages}
        applied_cm = {"applied_edits": applied}

    # 3. threshold compaction
    prelude: List[Dict[str, Any]] = []
    iterations: Optional[List[Dict[str, Any]]] = None
    edit = comp.compaction_edit(body)
    if edit is not None:
        iterations = []
        if approx_input >= comp.trigger_tokens(edit):
            summary, stop_reason, it_usage = await _run_summary(ctx, body, edit.get("instructions"))
            iterations.append({"type": "compaction", **it_usage})
            ctx.metadata["compaction"] = "threshold"
            if summary:
                prelude.append(comp.make_compaction_block(summary))
                incoming_summary = summary
                last_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
                messages = [last_user] if last_user else []
                body = {**body, "messages": messages}
            if edit.get("pause_after_compaction") or not summary:
                payload = tr_a.compaction_only_response(
                    body["model"], summary, stop_reason, it_usage
                )
                if stream:
                    return _compaction_stream(ctx, payload)
                return json_response(ctx, payload, Usage(**{k: v for k, v in it_usage.items()}))

    openai_body = tr_a.anthropic_request_to_openai(
        body, family=entry.family if entry else None, summary=incoming_summary
    )
    openai_body["stream"] = stream
    if stream:
        openai_body, _ = stream_options_for_client(openai_body)
    result = await engine.open(
        ctx, "v1/chat/completions", openai_body, stream=stream, endpoint="messages"
    )
    err = await _error_or_none(ctx, result, translated=True)
    if err is not None:
        return err
    if not stream:
        data = await result.read_json()
        usage = extract_openai_usage(data)
        if iterations is not None:
            iterations.append(
                {
                    "type": "message",
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                }
            )
        payload = tr_a.openai_response_to_anthropic(
            data,
            body["model"],
            extra_blocks=prelude,
            context_management=applied_cm,
            iterations=iterations,
        )
        return json_response(ctx, payload, usage)
    collector = UsageCollector("openai", ctx.endpoint)
    builder = tr_a.AnthropicStreamBuilder(
        body["model"],
        prelude_blocks=prelude,
        context_management=applied_cm,
        iterations=iterations,
    )
    if iterations is not None:
        iterations.append({"type": "message", "input_tokens": 0, "output_tokens": 0})

    def on_finish() -> None:
        if iterations:
            iterations[-1]["input_tokens"] = builder.input_tokens
            iterations[-1]["output_tokens"] = builder.output_tokens

    frames = tr_a.openai_stream_to_anthropic(result.aiter_frames(), builder, collector)
    return streaming_response(ctx, frames, collector, SSE, on_finish=on_finish)


async def anthropic_count_tokens(ctx: ServingContext, body: Dict[str, Any]) -> Response:
    engine = get_engine()
    if not body.get("model"):
        raise ServingError.invalid_request("model: field required", "model")
    ctx.model = body["model"]
    entry = engine.table.get(body["model"])
    if entry is not None and entry.speaks("anthropic"):
        result = await engine.open(
            ctx,
            "v1/messages/count_tokens",
            body,
            stream=False,
            headers=_anthropic_headers(ctx),
        )
        err = await _error_or_none(ctx, result)
        if err is not None:
            return err
        return json_response(ctx, await result.read_json(), Usage())
    probe = {**body, "max_tokens": body.get("max_tokens") or 1}
    openai_body = tr_a.anthropic_request_to_openai(probe, family=entry.family if entry else None)
    count = await count_tokens_via_engine(ctx, openai_body["messages"], body["model"])
    if count is None:
        count = estimate_tokens(
            comp.render_conversation(body.get("messages") or [], body.get("system"))
        )
        ctx.metadata["estimated"] = True
    return json_response(ctx, {"input_tokens": int(count)}, Usage())


async def count_tokens_via_engine(
    ctx: ServingContext, messages: List[Dict[str, Any]], model: str
) -> Optional[int]:
    """Try the engine's /tokenize (vLLM messages form, then llama.cpp content form)."""
    engine = get_engine()
    flat = "\n".join(
        (
            m.get("content")
            if isinstance(m.get("content"), str)
            else json.dumps(m.get("content") or "")
        )
        for m in messages
    )
    attempts = [
        {"model": model, "messages": messages, "add_generation_prompt": True},
        {"content": flat, "add_special": True},
    ]
    for payload in attempts:
        try:
            result = await engine.open(
                ctx,
                "tokenize",
                payload,
                stream=False,
                model=model,
                endpoint="messages/count_tokens",
            )
        except ServingError:
            return None
        if result.status_code >= 400:
            await result.close()
            continue
        try:
            data = await result.read_json()
        except ServingError:
            continue
        if isinstance(data.get("count"), int):
            return data["count"]
        tokens = data.get("tokens")
        if isinstance(tokens, list):
            return len(tokens)
    return None


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------


def _ollama_native_path(ctx: ServingContext, entry) -> bool:
    return entry is not None and entry.speaks("ollama")


async def _ollama_passthrough(
    ctx: ServingContext, body: Dict[str, Any], engine_path: str, stream: bool
) -> Response:
    engine = get_engine()
    result = await engine.open(ctx, engine_path, body, stream=stream)
    err = await _error_or_none(ctx, result)
    if err is not None:
        return err
    if not stream:
        data = await result.read_json()
        return json_response(ctx, data, extract_ollama_usage(data))
    collector = UsageCollector("ollama", ctx.endpoint)

    async def lines() -> AsyncIterator[bytes]:
        async for line in result.aiter_lines():
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                obj = None
            if isinstance(obj, dict):
                collector.feed_ollama_object(obj)
            yield line

    return streaming_response(ctx, lines(), collector, NDJSON)


async def ollama_chat(ctx: ServingContext, body: Dict[str, Any]) -> Response:
    engine = get_engine()
    if not body.get("model"):
        raise ServingError.invalid_request("model is required", "model")
    ctx.model = body["model"]
    stream = bool(body.get("stream", True))
    ctx.stream = stream
    entry = engine.table.get(body["model"])
    if _ollama_native_path(ctx, entry):
        return await _ollama_passthrough(ctx, body, "api/chat", stream)
    openai_body = tr_o.ollama_chat_to_openai(body)
    started_ns = time.monotonic_ns()
    if stream:
        openai_body, _ = stream_options_for_client(openai_body)
    result = await engine.open(ctx, "v1/chat/completions", openai_body, stream=stream)
    err = await _error_or_none(ctx, result, translated=True)
    if err is not None:
        return err
    if not stream:
        data = await result.read_json()
        return json_response(
            ctx,
            tr_o.openai_chat_to_ollama_chat(data, body["model"], started_ns),
            extract_openai_usage(data),
        )
    collector = UsageCollector("openai", ctx.endpoint)
    builder = tr_o.OllamaStreamBuilder(body["model"], generate=False)
    return streaming_response(
        ctx,
        tr_o.openai_stream_to_ollama(result.aiter_frames(), builder, collector),
        collector,
        NDJSON,
    )


async def ollama_generate(ctx: ServingContext, body: Dict[str, Any]) -> Response:
    engine = get_engine()
    if not body.get("model"):
        raise ServingError.invalid_request("model is required", "model")
    ctx.model = body["model"]
    stream = bool(body.get("stream", True))
    ctx.stream = stream
    entry = engine.table.get(body["model"])
    if body.get("prompt") is None and not body.get("suffix"):
        # Ollama uses an empty generate to load a model; nothing to do here.
        return json_response(
            ctx,
            {
                "model": body["model"],
                "created_at": tr_o.now_iso(),
                "response": "",
                "done": True,
                "done_reason": "load",
            },
            Usage(),
        )
    if _ollama_native_path(ctx, entry):
        return await _ollama_passthrough(ctx, body, "api/generate", stream)
    engine_path, openai_body = tr_o.ollama_generate_to_openai(body)
    completions = engine_path.endswith("completions") and not engine_path.endswith(
        "chat/completions"
    )
    started_ns = time.monotonic_ns()
    if stream:
        openai_body, _ = stream_options_for_client(openai_body)
    result = await engine.open(ctx, engine_path, openai_body, stream=stream)
    err = await _error_or_none(ctx, result, translated=True)
    if err is not None:
        return err
    if not stream:
        data = await result.read_json()
        payload = (
            tr_o.openai_completion_to_ollama_generate(data, body["model"], started_ns)
            if completions
            else tr_o.openai_chat_to_ollama_chat(data, body["model"], started_ns, generate=True)
        )
        return json_response(ctx, payload, extract_openai_usage(data))
    collector = UsageCollector("openai", ctx.endpoint)
    builder = tr_o.OllamaStreamBuilder(body["model"], generate=True, completions=completions)
    return streaming_response(
        ctx,
        tr_o.openai_stream_to_ollama(result.aiter_frames(), builder, collector),
        collector,
        NDJSON,
    )


async def ollama_embed(ctx: ServingContext, body: Dict[str, Any], legacy: bool = False) -> Response:
    engine = get_engine()
    if not body.get("model"):
        raise ServingError.invalid_request("model is required", "model")
    ctx.model = body["model"]
    entry = engine.table.get(body["model"])
    if _ollama_native_path(ctx, entry):
        return await _ollama_passthrough(
            ctx, body, "api/embeddings" if legacy else "api/embed", False
        )
    openai_body = tr_o.ollama_embed_to_openai(body, legacy=legacy)
    _check_input_items(openai_body.get("input"))
    started_ns = time.monotonic_ns()
    result = await engine.open(ctx, "v1/embeddings", openai_body, stream=False)
    err = await _error_or_none(ctx, result, translated=True)
    if err is not None:
        return err
    data = await result.read_json()
    return json_response(
        ctx,
        tr_o.openai_embeddings_to_ollama(data, body["model"], started_ns, legacy=legacy),
        extract_openai_usage(data, "embeddings"),
    )


def _check_input_items(value: Any, param: str = "input") -> None:
    if isinstance(value, list) and len(value) > settings.SERVING_MAX_INPUT_ITEMS:
        raise ServingError(
            413,
            "request_too_large",
            f"{param} has more than {settings.SERVING_MAX_INPUT_ITEMS} items",
            param=param,
        )


# ---------------------------------------------------------------------------
# Responses API
# ---------------------------------------------------------------------------

RESPONSE_STORE_TTL = 3600


def _response_key(response_id: str) -> str:
    from app.db.redis.client import redis_key

    return redis_key("serving", "responses", response_id)


async def store_response(response: Dict[str, Any], api_key_id: Optional[str] = None) -> None:
    """Keep a Response object (store=true / background) for GET/DELETE/cancel, per API key."""
    from app.db.redis.client import get_redis

    try:
        client = await get_redis()
        record = {"api_key_id": api_key_id, "response": response}
        await client.set(_response_key(response["id"]), json.dumps(record), ex=RESPONSE_STORE_TTL)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"response store failed: {exc}")


async def _load_record(response_id: str) -> Optional[Dict[str, Any]]:
    from app.db.redis.client import get_redis

    try:
        client = await get_redis()
        raw = await client.get(_response_key(response_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"response load failed: {exc}")
        return None
    if not raw:
        return None
    record = json.loads(raw)
    return record if isinstance(record, dict) and "response" in record else None


def _owned(record: Optional[Dict[str, Any]], api_key_id: Optional[str]) -> bool:
    if record is None:
        return False
    owner = record.get("api_key_id")
    return owner is None or api_key_id is None or str(owner) == str(api_key_id)


async def load_response(
    response_id: str, api_key_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    record = await _load_record(response_id)
    return record["response"] if _owned(record, api_key_id) else None


async def delete_response(response_id: str, api_key_id: Optional[str] = None) -> bool:
    from app.db.redis.client import get_redis

    if not _owned(await _load_record(response_id), api_key_id):
        return False
    try:
        client = await get_redis()
        return bool(await client.delete(_response_key(response_id)))
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"response delete failed: {exc}")
        return False


_background_jobs: Dict[str, asyncio.Task] = {}


async def openai_responses(ctx: ServingContext, body: Dict[str, Any]) -> Response:
    engine = get_engine()
    if not body.get("model"):
        raise ServingError.invalid_request("model: field required", "model")
    ctx.model = body["model"]
    stream = bool(body.get("stream"))
    ctx.stream = stream
    if body.get("background"):
        return await _responses_background(ctx, body)
    chat_body = tr_r.responses_request_to_chat(body)
    chat_body["stream"] = stream
    if stream:
        chat_body, _ = stream_options_for_client(chat_body)
    result = await engine.open(ctx, "v1/chat/completions", chat_body, stream=stream)
    err = await _error_or_none(ctx, result)
    if err is not None:
        return err
    if not stream:
        data = await result.read_json()
        payload = tr_r.chat_response_to_responses(data, body)
        if body.get("store"):
            await store_response(payload, ctx.api_key.id)
        return json_response(ctx, payload, extract_openai_usage(data))
    collector = UsageCollector("openai", "responses")
    builder = tr_r.ResponsesStreamBuilder(body)

    def on_finish() -> None:
        if body.get("store"):
            asyncio.ensure_future(store_response(builder.response, ctx.api_key.id))

    return streaming_response(
        ctx,
        tr_r.chat_stream_to_responses(result.aiter_frames(), builder, collector),
        collector,
        SSE,
        on_finish=on_finish,
    )


async def _responses_background(ctx: ServingContext, body: Dict[str, Any]) -> Response:
    response_id = tr_r.new_id("resp")
    skeleton = tr_r.response_skeleton({**body, "stream": False}, response_id, "queued")
    key_id = ctx.api_key.id
    await store_response(skeleton, key_id)

    # The background job meters itself on its own context copy; the skeleton reply is free.
    job_ctx = copy.copy(ctx)
    job_ctx.metadata = {**ctx.metadata, "background": True}
    ctx.metered = True

    async def run() -> None:
        engine = get_engine()
        current = {**skeleton, "status": "in_progress"}
        await store_response(current, key_id)
        try:
            chat_body = tr_r.responses_request_to_chat(
                {**body, "stream": False, "background": False}
            )
            result = await engine.open(job_ctx, "v1/chat/completions", chat_body, stream=False)
            if result.status_code >= 400:
                raw = await result.read_bytes()
                raise ServingError.upstream_error(
                    raw.decode("utf-8", "replace")[:300], result.status_code
                )
            data = await result.read_json()
            payload = tr_r.chat_response_to_responses(data, body)
            payload["id"] = response_id
            payload["background"] = True
            stored = await load_response(response_id)
            if stored and stored.get("status") == "cancelled":
                return
            await store_response(payload, key_id)
            from app.services.metering.service import record_request

            await record_request(job_ctx, extract_openai_usage(data), "success", 200)
        except asyncio.CancelledError:
            await store_response({**current, "status": "cancelled"}, key_id)
            raise
        except ServingError as err:
            failed = {"code": err.code, "message": err.message}
            await store_response({**current, "status": "failed", "error": failed}, key_id)
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).error("background response failed")
            failed = {"code": "internal_error", "message": "internal error"}
            await store_response({**current, "status": "failed", "error": failed}, key_id)
        finally:
            _background_jobs.pop(response_id, None)

    _background_jobs[response_id] = asyncio.create_task(run())
    return json_response(ctx, skeleton, Usage(), status_code=200)


async def cancel_background_response(
    response_id: str, api_key_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    task = _background_jobs.get(response_id)
    stored = await load_response(response_id, api_key_id)
    if stored is None:
        return None
    if task is not None and not task.done():
        task.cancel()
    if stored.get("status") in ("queued", "in_progress"):
        stored["status"] = "cancelled"
        await store_response(stored, api_key_id)
    return stored


__all__ = [
    "DONE",
    "anthropic_count_tokens",
    "anthropic_messages",
    "cancel_background_response",
    "delete_response",
    "load_response",
    "ollama_chat",
    "ollama_embed",
    "ollama_generate",
    "openai_passthrough",
    "openai_responses",
    "uuid",
]
