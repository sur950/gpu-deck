"""
Live smoke test: the serving app in front of a REAL engine.

Starts ``ollama serve`` on a random localhost port (models from
``~/.ollama/models``, no downloads), points the API layer at it in dev-engine
mode (``SERVING_DEV_ENGINE_URL``), and drives it with the official
``openai``, ``anthropic`` and ``ollama`` clients: OpenAI chat (stream +
non-stream), Anthropic messages (stream, thinking) translated to Ollama's
OpenAI endpoint, and Ollama chat translated the same way. The engine process
is stopped in teardown.

Skipped when the ``ollama`` binary or the ``qwen3:0.6b`` model is missing.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import anthropic
import httpx
import ollama
import openai
import pytest
import pytest_asyncio

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.slow,
    pytest.mark.asyncio(loop_scope="session"),
]

MODEL = "qwen3:0.6b"
MANIFEST = Path.home() / ".ollama/models/manifests/registry.ollama.ai/library/qwen3/0.6b"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def live(tmp_path_factory):
    if not shutil.which("ollama") or not MANIFEST.exists():
        pytest.skip("ollama binary or qwen3:0.6b not available")
    import app.workers.tasks.metering_tasks as metering_tasks
    from app.core.settings import settings
    from app.services.serving import auth, upstream
    from app.services.serving.auth import ApiKeyContext
    from app.services.serving.routing import dev_engine_table, get_routing_table

    port = _free_port()
    log = tmp_path_factory.mktemp("ollama") / "serve.log"
    env = {**os.environ, "OLLAMA_HOST": f"127.0.0.1:{port}", "OLLAMA_KEEP_ALIVE": "30s"}
    proc = subprocess.Popen(
        ["ollama", "serve"], env=env, stdout=open(log, "wb"), stderr=subprocess.STDOUT
    )
    original_enqueue = metering_tasks.enqueue_usage_event
    saved = {
        k: getattr(settings, k)
        for k in (
            "SERVING_DEV_ENGINE_URL",
            "SERVING_DEV_ENGINE_MODELS",
            "SERVING_DEV_ENGINE_FAMILY",
        )
    }
    try:
        deadline = time.time() + 30
        while True:
            try:
                if httpx.get(f"http://127.0.0.1:{port}/api/version", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.time() > deadline or proc.poll() is not None:
                pytest.fail(f"ollama serve did not start: {log.read_text()[-2000:]}")
            time.sleep(0.3)
        settings.SERVING_DEV_ENGINE_URL = f"http://127.0.0.1:{port}"
        settings.SERVING_DEV_ENGINE_MODELS = MODEL
        settings.SERVING_DEV_ENGINE_FAMILY = "qwen3"
        upstream.set_transport_factory(None)
        await upstream.close_upstream_client()
        get_routing_table().load_from_dict(dev_engine_table())
        auth.set_verifiers_for_tests(lambda key: ApiKeyContext(id="live", name="live"), None)
        usage = []
        metering_tasks.enqueue_usage_event = usage.append

        from app.api_layer_main import app

        yield {"app": app, "usage": usage}
    finally:
        metering_tasks.enqueue_usage_event = original_enqueue
        auth.set_verifiers_for_tests(None, None)
        auth._resolved = False
        await upstream.close_upstream_client()
        for k, v in saved.items():
            setattr(settings, k, v)
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def _http(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://live", timeout=180
    )


async def test_live_openai_chat(live):
    client = openai.AsyncOpenAI(
        base_url="http://live/v1", api_key="sk-live", http_client=_http(live["app"])
    )
    resp = await client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": "Reply with the single word: pong /no_think"}],
        max_tokens=400,
        temperature=0,
        reasoning_effort="none",  # passed through untouched; Ollama maps it to think=false
    )
    message = resp.choices[0].message
    assert resp.model == MODEL
    assert message.content or getattr(message, "reasoning", None)
    assert resp.usage.prompt_tokens > 0 and resp.usage.completion_tokens > 0
    stream = await client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": "Count: 1, 2, 3 /no_think"}],
        max_tokens=200,
        stream=True,
    )
    chunks = [c async for c in stream]
    assert any(
        c.choices and (c.choices[0].delta.content or getattr(c.choices[0].delta, "reasoning", None))
        for c in chunks
    )
    assert all(c.usage is None for c in chunks)  # injected usage chunk stripped
    assert live["usage"][-1]["output_tokens"] > 0 and not live["usage"][-1]["metadata"]


async def test_live_anthropic_messages_stream_and_thinking(live):
    client = anthropic.AsyncAnthropic(
        base_url="http://live/anthropic",
        api_key="sk-live",
        http_client=_http(live["app"]),
    )
    async with client.messages.stream(
        model=MODEL,
        max_tokens=1024,
        thinking={"type": "enabled", "budget_tokens": 1024},
        messages=[{"role": "user", "content": "What is 2+2? Answer with the number."}],
    ) as stream:
        final = await stream.get_final_message()
    kinds = [b.type for b in final.content]
    assert "text" in kinds and final.stop_reason in ("end_turn", "max_tokens")
    if "thinking" in kinds:
        assert final.content[kinds.index("thinking")].signature
    assert final.usage.output_tokens > 0
    msg = await client.messages.create(
        model=MODEL,
        max_tokens=300,
        system="You answer tersely.",
        messages=[{"role": "user", "content": "Say hi /no_think"}],
    )
    assert msg.content and msg.usage.input_tokens > 0


async def test_live_ollama_chat_translated(live):
    client = ollama.AsyncClient(
        host="http://live/ollama",
        transport=httpx.ASGITransport(app=live["app"]),
        headers={"Authorization": "Bearer sk-live"},
        timeout=180,
    )
    resp = await client.chat(
        model=MODEL,
        messages=[{"role": "user", "content": "Say ok /no_think"}],
        stream=False,
        options={"num_predict": 200},
    )
    assert resp.done and resp.message.content and resp.eval_count > 0
    parts = [
        p
        async for p in await client.chat(
            model=MODEL,
            messages=[{"role": "user", "content": "Say yes /no_think"}],
            options={"num_predict": 200},
            stream=True,
        )
    ]
    assert parts[-1].done and parts[-1].eval_count > 0
    assert any(p.message.content or p.message.thinking for p in parts)
