"""
Load generator for benchmarks: drives the serving API (API layer URL) with a
fixed number of concurrent workers and records per-request TTFT, end-to-end
latency and token counts.

* chat / thinking / vision -> ``POST /v1/chat/completions`` streamed (SSE),
  ``stream_options.include_usage`` for exact token counts; TTFT = first
  content or reasoning delta.
* embedding -> ``POST /v1/embeddings``; rerank -> ``POST /v1/rerank``
  (TTFT = e2e, no output tokens).

``transport`` can be injected (``httpx.MockTransport``) for tests.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

import httpx
from app.core.logger import get_logger
from app.services.benchmark.stats import LevelResult, RequestSample

logger = get_logger(__name__)

_WORDS = (
    "the quick brown fox jumps over the lazy dog while a curious engineer measures "
    "latency throughput and tail percentiles across many concurrent requests"
).split()
CHAT_TYPES = ("chat", "thinking", "vision")


def synthetic_prompt(input_tokens: int, seed: int = 0) -> str:
    """Roughly ``input_tokens`` tokens of filler text (~0.75 words per token)."""
    words = max(int(input_tokens * 0.75), 1)
    start = seed % len(_WORDS)
    out = [_WORDS[(start + i) % len(_WORDS)] for i in range(words)]
    return "Summarize the following text in detail. " + " ".join(out)


def endpoint_for(model_type: str) -> str:
    if model_type in CHAT_TYPES:
        return "/v1/chat/completions"
    if model_type == "embedding":
        return "/v1/embeddings"
    if model_type == "rerank":
        return "/v1/rerank"
    raise ValueError(
        f"benchmarks support chat, thinking, vision, embedding and rerank models, not {model_type}"
    )


class LoadGenerator:
    """Runs one concurrency level at a time against ``base_url``."""

    def __init__(
        self,
        base_url: str,
        api_key: Optional[str],
        model: str,
        model_type: str,
        *,
        timeout_seconds: float = 300.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        verify: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.model_type = model_type
        self.endpoint = endpoint_for(model_type)
        self.timeout = timeout_seconds
        self.transport = transport
        self.verify = verify

    def _headers(self) -> Dict[str, str]:
        headers = {
            "content-type": "application/json",
            "user-agent": "gpudeck-benchmark/1",
        }
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        return headers

    def _body(self, workload: Dict[str, Any], i: int) -> Dict[str, Any]:
        prompts = workload.get("prompts") or []
        prompt = (
            prompts[i % len(prompts)]
            if prompts
            else synthetic_prompt(int(workload.get("input_tokens") or 512), seed=i)
        )
        if self.model_type in CHAT_TYPES:
            stream = bool(workload.get("stream", True))
            body: Dict[str, Any] = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": int(workload.get("output_tokens") or 128),
                "temperature": 0,
                "stream": stream,
            }
            if stream:
                body["stream_options"] = {"include_usage": True}
            return body
        if self.model_type == "embedding":
            return {"model": self.model, "input": prompt}
        return {
            "model": self.model,
            "query": prompt[:200],
            "documents": [prompt, prompt[::-1]],
        }

    async def run_level(
        self,
        concurrency: int,
        workload: Dict[str, Any],
        *,
        time_budget_seconds: Optional[float] = None,
        should_stop: Optional[Callable[[], Awaitable[bool]]] = None,
        launch_flags: Optional[Dict[str, Any]] = None,
    ) -> LevelResult:
        total = max(int(workload.get("requests_per_level") or 32), concurrency)
        counter = {"next": 0}
        samples: List[RequestSample] = []
        deadline = time.monotonic() + time_budget_seconds if time_budget_seconds else None
        stop = {"flag": False}
        # should_stop usually reads the run row through one AsyncSession, which must never
        # be used by two coroutines at once (concurrent workers hung the auto-tune run).
        stop_lock = asyncio.Lock()

        async def _should_stop() -> bool:
            async with stop_lock:
                return bool(await should_stop()) if should_stop is not None else False

        limits = httpx.Limits(
            max_connections=concurrency + 4, max_keepalive_connections=concurrency + 4
        )
        async with httpx.AsyncClient(
            base_url=self.base_url,
            headers=self._headers(),
            timeout=httpx.Timeout(self.timeout, connect=10.0),
            transport=self.transport,
            limits=limits,
            verify=self.verify,
        ) as client:

            async def worker() -> None:
                while not stop["flag"]:
                    i = counter["next"]
                    if i >= total:
                        return
                    if deadline is not None and time.monotonic() > deadline and samples:
                        return
                    counter["next"] = i + 1
                    samples.append(await self._one(client, workload, i))
                    if (
                        should_stop is not None
                        and i % max(concurrency, 1) == 0
                        and await _should_stop()
                    ):
                        stop["flag"] = True

            started = time.monotonic()
            await asyncio.gather(*(worker() for _ in range(max(concurrency, 1))))
            wall = time.monotonic() - started
        return LevelResult(
            concurrency=concurrency,
            samples=samples,
            wall_seconds=wall,
            launch_flags=launch_flags,
        )

    async def _one(
        self, client: httpx.AsyncClient, workload: Dict[str, Any], i: int
    ) -> RequestSample:
        body = self._body(workload, i)
        t0 = time.perf_counter()
        try:
            if self.model_type in CHAT_TYPES and body.get("stream"):
                return await self._stream(client, body, t0)
            resp = await client.post(self.endpoint, json=body)
            e2e = (time.perf_counter() - t0) * 1000
            if resp.status_code >= 400:
                return RequestSample(False, e2e, error=_error_text(resp.status_code, resp.text))
            data = resp.json()
            usage = data.get("usage") or {}
            out_tokens = int(usage.get("completion_tokens") or 0)
            if self.model_type in CHAT_TYPES and not out_tokens:
                text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
                out_tokens = max(len(text.split()), 1) if text else 0
            return RequestSample(
                True,
                e2e,
                ttft_ms=e2e,
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                output_tokens=out_tokens,
            )
        except (httpx.HTTPError, ValueError) as exc:
            return RequestSample(False, (time.perf_counter() - t0) * 1000, error=type(exc).__name__)

    async def _stream(
        self, client: httpx.AsyncClient, body: Dict[str, Any], t0: float
    ) -> RequestSample:
        ttft: Optional[float] = None
        deltas = 0
        usage: Dict[str, Any] = {}
        async with client.stream("POST", self.endpoint, json=body) as resp:
            if resp.status_code >= 400:
                text = (await resp.aread()).decode(errors="replace")
                return RequestSample(
                    False,
                    (time.perf_counter() - t0) * 1000,
                    error=_error_text(resp.status_code, text),
                )
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except ValueError:
                    continue
                if chunk.get("error"):
                    return RequestSample(
                        False,
                        (time.perf_counter() - t0) * 1000,
                        error=str(chunk["error"])[:200],
                    )
                if chunk.get("usage"):
                    usage = chunk["usage"]
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    if (
                        delta.get("content")
                        or delta.get("reasoning_content")
                        or delta.get("reasoning")
                    ):
                        deltas += 1
                        if ttft is None:
                            ttft = (time.perf_counter() - t0) * 1000
        e2e = (time.perf_counter() - t0) * 1000
        out_tokens = int(usage.get("completion_tokens") or 0) or deltas
        return RequestSample(
            True,
            e2e,
            ttft_ms=ttft if ttft is not None else e2e,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=out_tokens,
        )


def _error_text(status: int, text: str) -> str:
    try:
        data = json.loads(text)
        err = data.get("error")
        if isinstance(err, dict):
            return f"{status} {err.get('code') or err.get('type') or ''}".strip()
        if isinstance(err, str):
            return f"{status} {err[:80]}"
    except ValueError:
        pass
    return f"HTTP {status}"
