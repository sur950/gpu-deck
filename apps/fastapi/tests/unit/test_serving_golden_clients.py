"""
Golden translation tests: the official ``openai``, ``anthropic`` and
``ollama`` Python clients talk to the serving app (in-process ASGI), which
routes to a fake OpenAI-compatible engine (in-process ASGI upstream).

Covered per format pair, stream and non-stream: plain text, tools (incl.
tool_choice / parallel / tool results), thinking (``<think>`` tags and
``reasoning_content``), images and documents, include_usage injection and
stripping, model-name rewrite, error shapes, compaction, count_tokens, and the
usage payload handed to metering.

No network, no DB, no Redis: auth, the IP checker and the metering enqueue are
replaced with in-memory fakes.
"""

from __future__ import annotations

import base64
import json
from typing import Any, Dict, List

import anthropic
import httpx
import httpx2
import ollama
import openai
import pytest
from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, Response, StreamingResponse

pytestmark = pytest.mark.unit

KEY_ID = "00000000-0000-4000-8000-000000000001"
SERVED = "qwen3-0.6b-q4_k_m"  # engine-side name; clients ask for "qwen3-test"


# ---------------------------------------------------------------------------
# Fake engine (OpenAI-compatible, llama.cpp-like)
# ---------------------------------------------------------------------------


class FakeEngine:
    def __init__(self) -> None:
        self.requests: List[Dict[str, Any]] = []
        self.app = self._build()
        self.reasoning_field = False  # True: reasoning_content deltas instead of <think> tags

    @property
    def last(self) -> Dict[str, Any]:
        return self.requests[-1]

    def _chat_plan(self, body: Dict[str, Any]):
        """(content, tool_calls, finish_reason, reasoning) for a chat request."""
        if body.get("tools") and not any(m.get("role") == "tool" for m in body["messages"]):
            calls = [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "arguments": '{"city": "Paris"}',
                    },
                }
            ]
            return "", calls, "tool_calls", ""
        thinking = (body.get("chat_template_kwargs") or {}).get("enable_thinking")
        if thinking:
            if self.reasoning_field:
                return "The answer is 4.", [], "stop", "Two plus two is four."
            return (
                "<think>\nTwo plus two is four.\n</think>\n\nThe answer is 4.",
                [],
                "stop",
                "",
            )
        if any(m.get("role") == "tool" for m in body["messages"]):
            return "It is sunny in Paris.", [], "stop", ""
        sysmsgs = [m["content"] for m in body["messages"] if m.get("role") == "system"]
        if sysmsgs and "compacting a conversation" in str(sysmsgs[0]):
            return (
                "SUMMARY: user asked about Paris weather; answer was sunny.",
                [],
                "stop",
                "",
            )
        return "Hello from the fake engine.", [], "stop", ""

    @staticmethod
    def _usage() -> Dict[str, Any]:
        return {
            "prompt_tokens": 12,
            "completion_tokens": 7,
            "total_tokens": 19,
            "prompt_tokens_details": {"cached_tokens": 4},
            "completion_tokens_details": {"reasoning_tokens": 3},
        }

    def _build(self) -> FastAPI:
        app = FastAPI()
        engine = self

        @app.post("/v1/chat/completions")
        async def chat(request: Request):
            body = await request.json()
            engine.requests.append({"path": "chat", "body": body, "headers": dict(request.headers)})
            if body.get("model") == "boom":
                return JSONResponse({"error": {"message": "engine exploded"}}, status_code=500)
            content, calls, finish, reasoning = engine._chat_plan(body)
            model = body.get("model")
            if not body.get("stream"):
                message: Dict[str, Any] = {
                    "role": "assistant",
                    "content": content or None,
                }
                if calls:
                    message["tool_calls"] = calls
                if reasoning:
                    message["reasoning_content"] = reasoning
                return {
                    "id": "chatcmpl-1",
                    "object": "chat.completion",
                    "created": 1,
                    "model": model,
                    "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                    "usage": engine._usage(),
                }

            async def frames():
                def chunk(delta, finish_reason=None):
                    payload = {
                        "id": "chatcmpl-1",
                        "object": "chat.completion.chunk",
                        "created": 1,
                        "model": model,
                        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
                    }
                    return f"data: {json.dumps(payload)}\n\n".encode()

                yield chunk({"role": "assistant", "content": ""})
                for i in range(0, len(reasoning), 5):
                    yield chunk({"reasoning_content": reasoning[i : i + 5]})
                for i in range(0, len(content), 3):  # small pieces split <think> tags
                    yield chunk({"content": content[i : i + 3]})
                for idx, call in enumerate(calls):
                    yield chunk(
                        {
                            "tool_calls": [
                                {
                                    "index": idx,
                                    "id": call["id"],
                                    "type": "function",
                                    "function": {
                                        "name": call["function"]["name"],
                                        "arguments": "",
                                    },
                                }
                            ]
                        }
                    )
                    args = call["function"]["arguments"]
                    for part in (args[:6], args[6:]):
                        yield chunk(
                            {"tool_calls": [{"index": idx, "function": {"arguments": part}}]}
                        )
                yield chunk({}, finish)
                if (body.get("stream_options") or {}).get("include_usage"):
                    usage = {
                        "id": "chatcmpl-1",
                        "object": "chat.completion.chunk",
                        "created": 1,
                        "model": model,
                        "choices": [],
                        "usage": engine._usage(),
                    }
                    yield f"data: {json.dumps(usage)}\n\n".encode()
                yield b"data: [DONE]\n\n"

            return StreamingResponse(frames(), media_type="text/event-stream")

        @app.post("/v1/completions")
        async def completions(request: Request):
            body = await request.json()
            engine.requests.append({"path": "completions", "body": body})
            return {
                "id": "cmpl-1",
                "object": "text_completion",
                "created": 1,
                "model": body["model"],
                "choices": [{"index": 0, "text": " world", "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 2,
                    "completion_tokens": 1,
                    "total_tokens": 3,
                },
            }

        @app.post("/v1/embeddings")
        async def embeddings(request: Request):
            body = await request.json()
            engine.requests.append({"path": "embeddings", "body": body})
            inputs = body["input"] if isinstance(body["input"], list) else [body["input"]]
            return {
                "object": "list",
                "model": body["model"],
                "data": [
                    {"object": "embedding", "index": i, "embedding": [0.1, 0.2, 0.3]}
                    for i in range(len(inputs))
                ],
                "usage": {
                    "prompt_tokens": 5 * len(inputs),
                    "total_tokens": 5 * len(inputs),
                },
            }

        @app.post("/v1/rerank")
        async def rerank(request: Request):
            body = await request.json()
            engine.requests.append({"path": "rerank", "body": body})
            return {
                "model": body["model"],
                "results": [
                    {"index": 1, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.1},
                ],
                "usage": {"prompt_tokens": 9, "total_tokens": 9},
            }

        @app.post("/v1/audio/speech")
        async def speech(request: Request):
            body = await request.json()
            engine.requests.append({"path": "speech", "body": body})
            return Response(content=b"\x00" * 16000, media_type="audio/mpeg")

        @app.post("/v1/audio/transcriptions")
        async def transcriptions(request: Request):
            raw = await request.body()
            engine.requests.append({"path": "transcriptions", "raw": raw})
            return {"text": "hello world", "duration": 2.5}

        @app.post("/v1/images/generations")
        async def images(request: Request):
            body = await request.json()
            engine.requests.append({"path": "images", "body": body})
            return {
                "created": 1,
                "data": [{"b64_json": "aGk="} for _ in range(body.get("n") or 1)],
            }

        @app.post("/tokenize")
        async def tokenize(request: Request):
            body = await request.json()
            engine.requests.append({"path": "tokenize", "body": body})
            return {"tokens": list(range(21))}

        return app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _table() -> Dict[str, Any]:
    def entry(mtype: str, served: str, family=None, native=("openai",)):
        return {
            "model_id": "11111111-1111-4111-8111-111111111111",
            "type": mtype,
            "deployment_id": "22222222-2222-4222-8222-222222222222",
            "family": family,
            "aliases": [],
            "traffic": {
                "strategy": "least_sessions",
                "failover": True,
                "max_retries": 1,
            },
            "native_formats": list(native),
            "gpu_count": 1,
            "engine": "llama_cpp",
            "instances": [
                {
                    "instance_id": f"33333333-3333-4333-8333-{abs(hash(served)) % 10**12:012d}",
                    "node_id": "44444444-4444-4444-8444-444444444444",
                    "proxy_url": "http://fake-engine",
                    "served_model_name": served,
                    "engine": "llama_cpp",
                    "state": "ready",
                    "health": "healthy",
                    "accepting": True,
                    "weight": 100,
                    "direct": True,
                }
            ],
        }

    return {
        "version": 7,
        "models": {
            "qwen3-test": {
                **entry("chat", SERVED, family="qwen3"),
                "aliases": ["qwen3"],
            },
            "embed-test": entry("embedding", "nomic-embed"),
            "rerank-test": entry("rerank", "bge-rerank"),
            "tts-test": entry("tts", "kokoro"),
            "stt-test": entry("stt", "whisper"),
            "img-test": entry("image", "sdxl"),
            "boom": entry("chat", "boom"),
        },
        "nodes": {"44444444-4444-4444-8444-444444444444": {"name": "n1", "status": "online"}},
    }


@pytest.fixture
def engine(monkeypatch):
    from app.services.serving import auth, upstream
    from app.services.serving.auth import ApiKeyContext
    from app.services.serving.routing import get_routing_table

    fake = FakeEngine()
    upstream.set_transport_factory(lambda: httpx.ASGITransport(app=fake.app))
    get_routing_table().load_from_dict(_table())

    def verify(key):
        if key == "bad-key":
            raise ValueError("invalid")
        return ApiKeyContext(id=KEY_ID, name="golden")

    auth.set_verifiers_for_tests(verify, lambda ip, scope: True)

    class Allowed:
        allowed = True
        reason = "test"

    async def allow(ip, scope):
        return Allowed()

    monkeypatch.setattr("app.middlewares.ip_rules.check_ip", allow)
    usage: List[Dict[str, Any]] = []
    monkeypatch.setattr(
        "app.workers.tasks.metering_tasks.enqueue_usage_event",
        lambda payload: usage.append(payload),
    )
    fake.usage = usage
    yield fake
    upstream.set_transport_factory(None)
    auth.set_verifiers_for_tests(None, None)
    auth._resolved = False


def _http():
    """HTTP client for the openai / anthropic SDKs, which are built on httpx2 (not httpx)."""
    from app.api_layer_main import app

    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def oai():
    return openai.AsyncOpenAI(base_url="http://test/v1", api_key="sk-test", http_client=_http())


@pytest.fixture
def oai_explicit():
    return openai.AsyncOpenAI(
        base_url="http://test/openai/v1", api_key="sk-test", http_client=_http()
    )


@pytest.fixture
def ant():
    return anthropic.AsyncAnthropic(
        base_url="http://test/anthropic",
        api_key="sk-test",
        http_client=_http(),
        max_retries=0,
    )


@pytest.fixture
def oll():
    from app.api_layer_main import app

    return ollama.AsyncClient(
        host="http://test/ollama",
        transport=httpx.ASGITransport(app=app),
        headers={"Authorization": "Bearer sk-test"},
    )


# ---------------------------------------------------------------------------
# OpenAI (pass-through)
# ---------------------------------------------------------------------------


async def test_openai_chat_non_stream_rewrites_model_and_meters(engine, oai):
    resp = await oai.chat.completions.create(
        model="qwen3-test",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.3,
        logit_bias={"42": -100},
        response_format={"type": "json_object"},
    )
    assert resp.choices[0].message.content == "Hello from the fake engine."
    assert resp.model == "qwen3-test"  # served name rewritten back
    sent = engine.last["body"]
    assert sent["model"] == SERVED
    assert sent["temperature"] == 0.3 and sent["logit_bias"] == {"42": -100}
    assert sent["response_format"] == {"type": "json_object"}
    assert engine.last["headers"]["x-gpudeck-format"] == "openai"
    u = engine.usage[-1]
    assert (
        u["input_tokens"],
        u["output_tokens"],
        u["cached_tokens"],
        u["reasoning_tokens"],
    ) == (
        12,
        7,
        4,
        3,
    )
    assert (
        u["format"] == "openai" and u["endpoint"] == "chat/completions" and u["status"] == "success"
    )
    assert u["api_key_id"] == KEY_ID and u["model_name"] == "qwen3-test"


async def test_openai_alias_and_explicit_prefix(engine, oai_explicit):
    resp = await oai_explicit.chat.completions.create(
        model="qwen3", messages=[{"role": "user", "content": "hi"}]
    )
    assert resp.choices[0].message.content


async def test_openai_stream_strips_injected_usage_chunk(engine, oai):
    stream = await oai.chat.completions.create(
        model="qwen3-test", messages=[{"role": "user", "content": "hi"}], stream=True
    )
    chunks = [c async for c in stream]
    text = "".join(c.choices[0].delta.content or "" for c in chunks if c.choices)
    assert text == "Hello from the fake engine."
    assert all(c.usage is None for c in chunks)  # client did not ask for usage
    assert engine.last["body"]["stream_options"] == {"include_usage": True}
    assert engine.usage[-1]["output_tokens"] == 7 and engine.usage[-1]["stream"] is True


async def test_openai_stream_forwards_usage_when_asked(engine, oai):
    stream = await oai.chat.completions.create(
        model="qwen3-test",
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
        stream_options={"include_usage": True},
    )
    chunks = [c async for c in stream]
    assert chunks[-1].usage is not None and chunks[-1].usage.prompt_tokens == 12
    assert chunks[-1].model == "qwen3-test"


async def test_openai_tools_pass_through(engine, oai):
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                },
            },
        }
    ]
    resp = await oai.chat.completions.create(
        model="qwen3-test",
        messages=[{"role": "user", "content": "weather?"}],
        tools=tools,
        tool_choice="auto",
        parallel_tool_calls=False,
    )
    call = resp.choices[0].message.tool_calls[0]
    assert call.function.name == "get_weather" and json.loads(call.function.arguments) == {
        "city": "Paris"
    }
    assert engine.last["body"]["parallel_tool_calls"] is False


async def test_openai_completions_embeddings_rerank(engine, oai):
    comp = await oai.completions.create(model="qwen3-test", prompt="hello")
    assert comp.choices[0].text == " world"
    emb = await oai.embeddings.create(model="embed-test", input=["a", "b"])
    assert len(emb.data) == 2 and engine.usage[-1]["input_tokens"] == 10
    async with _http() as http:
        r = await http.post(
            "/v1/rerank",
            headers={"Authorization": "Bearer sk-test"},
            json={"model": "rerank-test", "query": "q", "documents": ["a", "b"]},
        )
    assert r.status_code == 200 and r.json()["results"][0]["index"] == 1
    assert engine.usage[-1]["endpoint"] == "rerank" and engine.usage[-1]["input_tokens"] == 9


async def test_openai_audio_and_images(engine, oai):
    speech = await oai.audio.speech.create(model="tts-test", voice="alloy", input="hello there")
    assert len(speech.content) == 16000
    assert engine.usage[-1]["media_units"] == pytest.approx(1.0)  # 16000 B at 128 kbit/s
    wav = _wav(seconds=2)
    tr = await oai.audio.transcriptions.create(model="stt-test", file=("a.wav", wav, "audio/wav"))
    assert tr.text == "hello world"
    assert engine.usage[-1]["media_units"] == pytest.approx(2.5)
    assert b'name="model"' in engine.last["raw"]
    img = await oai.images.generate(model="img-test", prompt="a cat", n=2)
    assert len(img.data) == 2 and engine.usage[-1]["media_units"] == 2


async def test_openai_type_mismatch_and_unknown_model(engine, oai):
    with pytest.raises(openai.BadRequestError) as exc:
        await oai.embeddings.create(model="qwen3-test", input="x")
    assert exc.value.body["code"] == "model_type_mismatch"
    with pytest.raises(openai.NotFoundError) as exc:
        await oai.chat.completions.create(model="nope", messages=[{"role": "user", "content": "x"}])
    assert exc.value.body["code"] == "model_not_found"


async def test_openai_auth_error_shape(engine):
    client = openai.AsyncOpenAI(base_url="http://test/v1", api_key="bad-key", http_client=_http())
    with pytest.raises(openai.AuthenticationError) as exc:
        await client.models.list()
    assert exc.value.body["code"] == "invalid_api_key"


async def test_openai_engine_5xx_passthrough(engine, oai):
    with pytest.raises(openai.InternalServerError):
        await oai.with_options(max_retries=0).chat.completions.create(
            model="boom", messages=[{"role": "user", "content": "x"}]
        )
    assert engine.usage[-1]["status"] == "error" and engine.usage[-1]["http_status"] == 500


async def test_openai_models_listing(engine, oai):
    models = await oai.models.list()
    ids = {m.id for m in models.data}
    assert {"qwen3-test", "qwen3", "embed-test"} <= ids
    one = await oai.models.retrieve("qwen3-test")
    assert one.owned_by == "gpudeck"


# ---------------------------------------------------------------------------
# OpenAI Responses -> chat
# ---------------------------------------------------------------------------


async def test_responses_non_stream_with_tools_and_reasoning(engine, oai):
    resp = await oai.responses.create(
        model="qwen3-test",
        instructions="be brief",
        input=[{"role": "user", "content": [{"type": "input_text", "text": "2+2?"}]}],
        reasoning={"effort": "low"},
        max_output_tokens=50,
    )
    assert resp.output_text == "The answer is 4."
    kinds = [o.type for o in resp.output]
    assert kinds == ["reasoning", "message"]
    sent = engine.last["body"]
    assert sent["messages"][0] == {"role": "system", "content": "be brief"}
    assert sent["max_tokens"] == 50 and sent["reasoning_effort"] == "low"
    assert resp.usage.input_tokens == 12 and resp.usage.output_tokens_details.reasoning_tokens == 3

    tools = [{"type": "function", "name": "get_weather", "parameters": {"type": "object"}}]
    resp = await oai.responses.create(model="qwen3-test", input="weather?", tools=tools)
    call = [o for o in resp.output if o.type == "function_call"][0]
    assert call.name == "get_weather" and call.call_id == "call_1"
    follow = await oai.responses.create(
        model="qwen3-test",
        tools=tools,
        input=[
            {"role": "user", "content": "weather?"},
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "get_weather",
                "arguments": "{}",
            },
            {"type": "function_call_output", "call_id": "call_1", "output": "sunny"},
        ],
    )
    assert follow.output_text == "It is sunny in Paris."
    roles = [m["role"] for m in engine.last["body"]["messages"]]
    assert roles == ["user", "assistant", "tool"]


async def test_responses_stream_events(engine, oai):
    stream = await oai.responses.create(model="qwen3-test", input="hi", stream=True)
    events = [e async for e in stream]
    types = [e.type for e in events]
    assert types[0] == "response.created" and types[-1] == "response.completed"
    text = "".join(e.delta for e in events if e.type == "response.output_text.delta")
    assert text == "Hello from the fake engine."
    assert events[-1].response.usage.output_tokens == 7
    assert engine.usage[-1]["endpoint"] == "responses"


# ---------------------------------------------------------------------------
# Anthropic <-> OpenAI
# ---------------------------------------------------------------------------


async def test_anthropic_text_non_stream(engine, ant):
    msg = await ant.messages.create(
        model="qwen3-test",
        max_tokens=64,
        system=[{"type": "text", "text": "You are terse."}],
        stop_sequences=["END"],
        metadata={"user_id": "u-1"},
        messages=[{"role": "user", "content": "hi"}],
        # anthropic 2.x dropped the sampling keyword arguments; older clients still send them.
        extra_body={"temperature": 0.2, "top_k": 5},
    )
    assert msg.content[0].type == "text" and msg.content[0].text == "Hello from the fake engine."
    assert msg.stop_reason == "end_turn" and msg.model == "qwen3-test"
    assert msg.usage.input_tokens == 8 and msg.usage.cache_read_input_tokens == 4
    sent = engine.last["body"]
    assert sent["messages"][0] == {"role": "system", "content": "You are terse."}
    assert sent["stop"] == ["END"] and sent["max_tokens"] == 64 and sent["top_k"] == 5
    assert sent["temperature"] == 0.2
    assert sent["user"] == "u-1"
    u = engine.usage[-1]
    assert u["format"] == "anthropic" and u["endpoint"] == "messages" and u["input_tokens"] == 12


async def test_anthropic_text_stream_named_events(engine, ant):
    async with ant.messages.stream(
        model="qwen3-test", max_tokens=32, messages=[{"role": "user", "content": "hi"}]
    ) as stream:
        events = [e.type async for e in stream]
        final = await stream.get_final_message()
    assert events[0] == "message_start"
    assert "content_block_delta" in events and events[-1] == "message_stop"
    assert final.content[0].text == "Hello from the fake engine."
    assert final.usage.output_tokens == 7


async def test_anthropic_tools_round_trip(engine, ant):
    tools = [
        {
            "name": "get_weather",
            "description": "Weather by city",
            "input_schema": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
            },
        }
    ]
    msg = await ant.messages.create(
        model="qwen3-test",
        max_tokens=64,
        tools=tools,
        tool_choice={"type": "any", "disable_parallel_tool_use": True},
        messages=[{"role": "user", "content": "weather in Paris?"}],
    )
    assert msg.stop_reason == "tool_use"
    block = msg.content[0]
    assert (
        block.type == "tool_use"
        and block.name == "get_weather"
        and block.input == {"city": "Paris"}
    )
    sent = engine.last["body"]
    assert sent["tool_choice"] == "required" and sent["parallel_tool_calls"] is False
    assert sent["tools"][0]["function"]["parameters"]["properties"]["city"]["type"] == "string"

    msg2 = await ant.messages.create(
        model="qwen3-test",
        max_tokens=64,
        tools=tools,
        messages=[
            {"role": "user", "content": "weather in Paris?"},
            {"role": "assistant", "content": [b.model_dump() for b in msg.content]},
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": "sunny, 21C",
                    }
                ],
            },
        ],
    )
    assert msg2.content[0].text == "It is sunny in Paris."
    sent = engine.last["body"]["messages"]
    assert sent[1]["tool_calls"][0]["id"] == block.id
    assert sent[2] == {
        "role": "tool",
        "tool_call_id": block.id,
        "content": "sunny, 21C",
    }


async def test_anthropic_tools_stream(engine, ant):
    tools = [{"name": "get_weather", "input_schema": {"type": "object"}}]
    async with ant.messages.stream(
        model="qwen3-test",
        max_tokens=64,
        tools=tools,
        messages=[{"role": "user", "content": "weather?"}],
    ) as stream:
        final = await stream.get_final_message()
    assert final.stop_reason == "tool_use"
    assert final.content[-1].type == "tool_use" and final.content[-1].input == {"city": "Paris"}


@pytest.mark.parametrize("reasoning_field", [False, True])
async def test_anthropic_thinking(engine, ant, reasoning_field):
    engine.reasoning_field = reasoning_field
    msg = await ant.messages.create(
        model="qwen3-test",
        max_tokens=2048,
        thinking={"type": "enabled", "budget_tokens": 1024},
        messages=[{"role": "user", "content": "2+2?"}],
    )
    kinds = [b.type for b in msg.content]
    assert kinds == ["thinking", "text"]
    assert msg.content[0].thinking.strip() == "Two plus two is four."
    assert msg.content[0].signature.startswith("gd1:")
    assert msg.content[1].text == "The answer is 4."
    sent = engine.last["body"]
    assert sent["chat_template_kwargs"] == {"enable_thinking": True}
    assert sent["reasoning_effort"] == "low"

    async with ant.messages.stream(
        model="qwen3-test",
        max_tokens=2048,
        thinking={"type": "enabled", "budget_tokens": 20000},
        messages=[{"role": "user", "content": "2+2?"}],
    ) as stream:
        final = await stream.get_final_message()
    assert [b.type for b in final.content] == ["thinking", "text"]
    assert final.content[0].thinking.strip() == "Two plus two is four."
    assert final.content[1].text == "The answer is 4."
    assert engine.last["body"]["reasoning_effort"] == "high"


async def test_anthropic_images_and_documents(engine, ant):
    png = base64.b64encode(b"\x89PNG fake").decode()
    await ant.messages.create(
        model="qwen3-test",
        max_tokens=16,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": png,
                        },
                    },
                    {
                        "type": "image",
                        "source": {"type": "url", "url": "https://example.com/a.jpg"},
                    },
                    {
                        "type": "document",
                        "title": "notes",
                        "source": {
                            "type": "text",
                            "media_type": "text/plain",
                            "data": "alpha",
                        },
                    },
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": "JVBERg==",
                        },
                    },
                    {"type": "text", "text": "describe"},
                ],
            }
        ],
    )
    parts = engine.last["body"]["messages"][0]["content"]
    assert parts[0] == {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{png}"},
    }
    assert parts[1]["image_url"]["url"] == "https://example.com/a.jpg"
    assert parts[2]["type"] == "text" and "alpha" in parts[2]["text"]
    assert parts[3]["type"] == "file" and parts[3]["file"]["file_data"].startswith(
        "data:application/pdf;base64,"
    )
    assert parts[4] == {"type": "text", "text": "describe"}


async def test_anthropic_count_tokens_and_errors(engine, ant):
    count = await ant.messages.count_tokens(
        model="qwen3-test", messages=[{"role": "user", "content": "hello"}]
    )
    assert count.input_tokens == 21
    assert engine.usage[-1]["endpoint"] == "messages/count_tokens"
    with pytest.raises(anthropic.NotFoundError) as exc:
        await ant.messages.create(
            model="nope", max_tokens=5, messages=[{"role": "user", "content": "x"}]
        )
    assert exc.value.body["error"]["type"] == "not_found_error"
    assert "[code=model_not_found]" in exc.value.body["error"]["message"]
    with pytest.raises(anthropic.BadRequestError):
        await ant.messages.create(model="qwen3-test", max_tokens=5, messages=[])


async def test_anthropic_models_listing_bare_and_prefixed(engine, ant):
    page = await ant.models.list()
    assert "qwen3-test" in {m.id for m in page.data}
    async with _http() as http:
        r = await http.get(
            "/v1/models",
            headers={"x-api-key": "sk-test", "anthropic-version": "2023-06-01"},
        )
    assert r.status_code == 200 and "has_more" in r.json()
    assert r.headers["anthropic-version"] == "2023-06-01"


async def test_anthropic_on_demand_compaction_round_trip(engine, ant):
    history = [
        {"role": "user", "content": "weather in Paris?"},
        {"role": "assistant", "content": "Sunny."},
        {"role": "user", "content": "thanks"},
    ]
    async with _http() as http:
        r = await http.post(
            "/anthropic/v1/messages",
            headers={
                "x-api-key": "sk-test",
                "anthropic-version": "2023-06-01",
                "anthropic-beta": "compact-2026-09-04",
            },
            json={
                "model": "qwen3-test",
                "max_tokens": 512,
                "messages": history,
                "compaction": {"type": "summarize"},
            },
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stop_reason"] == "compaction"
    block = body["content"][0]
    assert block["type"] == "compaction" and block["content"].startswith("SUMMARY")
    assert body["usage"]["iterations"][0]["type"] == "compaction"

    msg = await ant.messages.create(
        model="qwen3-test",
        max_tokens=32,
        messages=[
            {"role": "user", "content": [block]},
            {"role": "user", "content": "and now?"},
        ],
    )
    assert msg.content[0].text
    sent = engine.last["body"]["messages"]
    assert sent[0]["role"] == "system" and "SUMMARY" in sent[0]["content"]

    tampered = dict(block, content="SUMMARY: something else")
    with pytest.raises(anthropic.BadRequestError):
        await ant.messages.create(
            model="qwen3-test",
            max_tokens=8,
            messages=[{"role": "user", "content": [tampered]}],
        )


# ---------------------------------------------------------------------------
# Ollama <-> OpenAI
# ---------------------------------------------------------------------------


async def test_ollama_chat_non_stream_and_stream(engine, oll):
    resp = await oll.chat(
        model="qwen3-test",
        messages=[{"role": "user", "content": "hi"}],
        stream=False,
        options={"temperature": 0.1, "num_predict": 20, "stop": ["x"]},
    )
    assert resp.message.content == "Hello from the fake engine."
    assert resp.done and resp.prompt_eval_count == 12 and resp.eval_count == 7
    sent = engine.last["body"]
    assert sent["max_tokens"] == 20 and sent["temperature"] == 0.1 and sent["stop"] == ["x"]

    parts = []
    async for chunk in await oll.chat(
        model="qwen3-test", messages=[{"role": "user", "content": "hi"}], stream=True
    ):
        parts.append(chunk)
    assert "".join(p.message.content for p in parts) == "Hello from the fake engine."
    assert parts[-1].done and parts[-1].eval_count == 7
    assert engine.usage[-1]["format"] == "ollama" and engine.usage[-1]["endpoint"] == "api/chat"


async def test_ollama_think_tools_generate_embed(engine, oll):
    resp = await oll.chat(
        model="qwen3-test",
        messages=[{"role": "user", "content": "2+2?"}],
        think=True,
        stream=False,
    )
    assert resp.message.thinking.strip() == "Two plus two is four."
    assert resp.message.content == "The answer is 4."
    tools = [
        {
            "type": "function",
            "function": {"name": "get_weather", "parameters": {"type": "object"}},
        }
    ]
    resp = await oll.chat(
        model="qwen3-test",
        messages=[{"role": "user", "content": "w?"}],
        tools=tools,
        stream=False,
    )
    assert resp.message.tool_calls[0].function.name == "get_weather"
    assert resp.message.tool_calls[0].function.arguments == {"city": "Paris"}
    gen = await oll.generate(model="qwen3-test", prompt="hi", system="sys", stream=False)
    assert gen.response == "Hello from the fake engine."
    assert engine.last["body"]["messages"][0] == {"role": "system", "content": "sys"}
    raw = await oll.generate(model="qwen3-test", prompt="hello", raw=True, stream=False)
    assert raw.response == " world" and engine.last["path"] == "completions"
    emb = await oll.embed(model="embed-test", input=["a", "b"])
    assert len(emb.embeddings) == 2 and emb.prompt_eval_count == 10
    tags = await oll.list()
    assert "qwen3-test" in {m.model for m in tags.models}
    ps = await oll.ps()
    assert ps.models


async def test_ollama_unsupported_and_errors(engine):
    async with _http() as http:
        r = await http.post("/api/pull", headers={"Authorization": "Bearer sk-test"}, json={})
        assert r.status_code == 404 and "not supported" in r.json()["error"]
        r = await http.post(
            "/api/chat",
            headers={"Authorization": "Bearer sk-test"},
            json={"model": "nope", "messages": []},
        )
        assert r.status_code == 404 and r.headers["x-gpudeck-error-code"] == "model_not_found"
        assert set(r.json()) == {"error"}


def _wav(seconds: int) -> bytes:
    rate, width = 8000, 2
    data = b"\x00" * rate * width * seconds
    header = (
        b"RIFF"
        + (36 + len(data)).to_bytes(4, "little")
        + b"WAVEfmt "
        + (16).to_bytes(4, "little")
        + (1).to_bytes(2, "little")
        + (1).to_bytes(2, "little")
        + rate.to_bytes(4, "little")
        + (rate * width).to_bytes(4, "little")
        + width.to_bytes(2, "little")
        + (16).to_bytes(2, "little")
        + b"data"
        + len(data).to_bytes(4, "little")
    )
    return header + data
