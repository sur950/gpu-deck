"""
Usage extraction per format and endpoint (serving.md §6).

``UsageCollector`` accumulates what the metering task needs while a response
streams by; ``extract_*`` helpers read usage out of complete JSON bodies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

_CONTENT_RE = re.compile(rb'"(?:content|reasoning_content|reasoning|text)"\s*:\s*"[^"]')


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    media_units: float = 0.0
    estimated: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "input_tokens": int(self.input_tokens or 0),
            "output_tokens": int(self.output_tokens or 0),
            "cached_tokens": int(self.cached_tokens or 0),
            "reasoning_tokens": int(self.reasoning_tokens or 0),
            "media_units": float(self.media_units or 0),
            "estimated": bool(self.estimated),
            "extra": dict(self.extra),
        }


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def extract_openai_usage(body: Dict[str, Any], endpoint: str = "chat/completions") -> Usage:
    usage = body.get("usage") or {}
    u = Usage()
    if endpoint == "responses":
        u.input_tokens = _int(usage.get("input_tokens"))
        u.output_tokens = _int(usage.get("output_tokens"))
        u.cached_tokens = _int((usage.get("input_tokens_details") or {}).get("cached_tokens"))
        u.reasoning_tokens = _int(
            (usage.get("output_tokens_details") or {}).get("reasoning_tokens")
        )
        return u
    u.input_tokens = _int(usage.get("prompt_tokens", usage.get("input_tokens")))
    u.output_tokens = _int(usage.get("completion_tokens", usage.get("output_tokens")))
    details = usage.get("prompt_tokens_details") or {}
    if details.get("cached_tokens") is not None:
        u.cached_tokens = _int(details.get("cached_tokens"))
    else:
        timings = body.get("timings") or {}
        if timings.get("cache_n") is not None:
            u.cached_tokens = _int(timings.get("cache_n"))
    u.reasoning_tokens = _int(
        (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
    )
    if endpoint == "rerank" and not u.input_tokens:
        billed = ((body.get("meta") or {}).get("billed_units") or {}).get("search_units")
        if billed:
            u.input_tokens = _int(billed)
    return u


def extract_anthropic_usage(body: Dict[str, Any]) -> Usage:
    usage = body.get("usage") or {}
    u = Usage()
    cache_create = _int(usage.get("cache_creation_input_tokens"))
    cache_read = _int(usage.get("cache_read_input_tokens"))
    u.input_tokens = _int(usage.get("input_tokens")) + cache_create + cache_read
    u.output_tokens = _int(usage.get("output_tokens"))
    u.cached_tokens = cache_read
    if cache_create:
        u.extra["cache_creation_input_tokens"] = cache_create
    for it in usage.get("iterations") or []:
        if isinstance(it, dict) and it.get("type") == "compaction":
            u.extra["compaction_input_tokens"] = _int(it.get("input_tokens"))
            u.extra["compaction_output_tokens"] = _int(it.get("output_tokens"))
    return u


def extract_ollama_usage(body: Dict[str, Any]) -> Usage:
    u = Usage()
    u.input_tokens = _int(body.get("prompt_eval_count"))
    u.output_tokens = _int(body.get("eval_count"))
    if body.get("total_duration"):
        u.extra["latency_ms"] = _int(body.get("total_duration")) // 1_000_000
    if body.get("prompt_eval_duration"):
        u.extra["ttft_ms"] = _int(body.get("prompt_eval_duration")) // 1_000_000
    return u


class UsageCollector:
    """Accumulates usage from streamed frames of any format."""

    def __init__(self, fmt: str, endpoint: str) -> None:
        self.fmt = fmt
        self.endpoint = endpoint
        self.usage = Usage()
        self.saw_usage = False
        self.content_frames = 0
        self.finish_reason: Optional[str] = None

    def note_frame_bytes(self, frame: bytes) -> None:
        """Cheap per-frame bookkeeping when the frame is not parsed as JSON."""
        if _CONTENT_RE.search(frame):
            self.content_frames += 1

    def feed_openai_chunk(self, chunk: Dict[str, Any]) -> None:
        usage = chunk.get("usage")
        if usage:
            self.saw_usage = True
            self.usage = extract_openai_usage(chunk, "chat/completions")
        choices = chunk.get("choices") or []
        for ch in choices:
            delta = ch.get("delta") or {}
            if delta.get("content") or delta.get("reasoning_content") or delta.get("reasoning"):
                self.content_frames += 1
            if ch.get("finish_reason"):
                self.finish_reason = ch["finish_reason"]
        timings = chunk.get("timings")
        if timings and timings.get("cache_n") is not None and not self.usage.cached_tokens:
            self.usage.cached_tokens = _int(timings.get("cache_n"))

    def feed_responses_event(self, event: Dict[str, Any]) -> None:
        if event.get("type") == "response.completed":
            resp = event.get("response") or {}
            if resp.get("usage"):
                self.saw_usage = True
                self.usage = extract_openai_usage(resp, "responses")
        elif event.get("type") in (
            "response.output_text.delta",
            "response.reasoning_text.delta",
        ):
            self.content_frames += 1

    def feed_anthropic_event(self, event: Dict[str, Any]) -> None:
        etype = event.get("type")
        if etype == "message_start":
            msg = event.get("message") or {}
            if msg.get("usage"):
                self.saw_usage = True
                base = extract_anthropic_usage(msg)
                self.usage.input_tokens = base.input_tokens
                self.usage.cached_tokens = base.cached_tokens
                self.usage.extra.update(base.extra)
        elif etype == "message_delta":
            usage = event.get("usage") or {}
            if usage.get("output_tokens") is not None:
                self.saw_usage = True
                self.usage.output_tokens = _int(usage.get("output_tokens"))
            if usage.get("input_tokens") is not None:
                self.usage.input_tokens = (
                    _int(usage.get("input_tokens"))
                    + _int(usage.get("cache_creation_input_tokens"))
                    + _int(usage.get("cache_read_input_tokens"))
                )
            stop = (event.get("delta") or {}).get("stop_reason")
            if stop:
                self.finish_reason = stop
        elif etype == "content_block_delta":
            self.content_frames += 1

    def feed_ollama_object(self, obj: Dict[str, Any]) -> None:
        if obj.get("done"):
            self.saw_usage = bool(obj.get("eval_count") or obj.get("prompt_eval_count"))
            self.usage = extract_ollama_usage(obj)
            if obj.get("done_reason"):
                self.finish_reason = obj["done_reason"]
        else:
            msg = obj.get("message") or {}
            if obj.get("response") or msg.get("content") or msg.get("thinking"):
                self.content_frames += 1

    def finalize(self) -> Usage:
        if not self.saw_usage:
            self.usage.output_tokens = self.content_frames
            self.usage.estimated = True
        return self.usage


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4) if text else 0


def audio_seconds_from_bytes(data: bytes, response_format: Optional[str]) -> float:
    """Rough output seconds for TTS from byte size and container bitrate."""
    bitrate = {
        "mp3": 128_000,
        "opus": 64_000,
        "aac": 128_000,
        "flac": 700_000,
        "wav": 16_000 * 16,
        "pcm": 24_000 * 16,
    }.get((response_format or "mp3").lower(), 128_000)
    return round(len(data) * 8 / bitrate, 3) if data else 0.0


def wav_duration_seconds(data: bytes) -> Optional[float]:
    """Duration of a WAV file from its header (for uploaded transcription audio)."""
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    try:
        byte_rate = int.from_bytes(data[28:32], "little")
        idx = data.find(b"data")
        if idx < 0 or byte_rate <= 0:
            return None
        size = int.from_bytes(data[idx + 4 : idx + 8], "little")
        return round(size / byte_rate, 3)
    except Exception:  # noqa: BLE001
        return None
