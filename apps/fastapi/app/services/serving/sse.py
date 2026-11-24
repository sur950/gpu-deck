"""
SSE / NDJSON frame helpers shared by the pass-through and translation paths.

``iter_sse_frames`` splits an upstream byte stream on blank lines and yields
complete event blocks as soon as they are complete (no extra buffering), so
a frame is forwarded the moment the engine finishes writing it.
"""

from __future__ import annotations

import json
import re
from typing import Any, AsyncIterator, Dict, Optional, Tuple

DONE = b"data: [DONE]\n\n"


async def iter_sse_frames(stream: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    """Yield complete SSE event blocks (bytes ending with a blank line) from a byte stream."""
    buffer = b""
    async for chunk in stream:
        if not chunk:
            continue
        buffer += chunk
        while True:
            idx = buffer.find(b"\n\n")
            if idx < 0:
                # Some servers use CRLF
                idx = buffer.find(b"\r\n\r\n")
                if idx < 0:
                    break
                frame, buffer = buffer[: idx + 4], buffer[idx + 4 :]
            else:
                frame, buffer = buffer[: idx + 2], buffer[idx + 2 :]
            yield frame
    tail = buffer.strip()
    if tail:
        yield tail + b"\n\n"


async def iter_ndjson_lines(stream: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    buffer = b""
    async for chunk in stream:
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            if line.strip():
                yield line.strip() + b"\n"
    if buffer.strip():
        yield buffer.strip() + b"\n"


def parse_sse_frame(frame: bytes) -> Tuple[Optional[str], Optional[str]]:
    """(event, data) of an SSE block; data lines are joined with newlines."""
    event: Optional[str] = None
    data_lines = []
    for line in frame.decode("utf-8", errors="replace").splitlines():
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        elif line.startswith("event:"):
            event = line[6:].strip()
    data = "\n".join(data_lines) if data_lines else None
    return event, data


def parse_sse_json(
    frame: bytes,
) -> Tuple[Optional[str], Optional[Dict[str, Any]], bool]:
    """(event, json, is_done) for a frame; json is None for non-JSON data."""
    event, data = parse_sse_frame(frame)
    if data is None:
        return event, None, False
    if data.strip() == "[DONE]":
        return event, None, True
    try:
        parsed = json.loads(data)
    except (json.JSONDecodeError, ValueError):
        return event, None, False
    return event, parsed if isinstance(parsed, dict) else None, False


def sse_data(obj: Dict[str, Any]) -> bytes:
    return b"data: " + json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode() + b"\n\n"


def sse_event(event: str, obj: Dict[str, Any]) -> bytes:
    return (
        f"event: {event}\ndata: ".encode()
        + json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode()
        + b"\n\n"
    )


def ndjson(obj: Dict[str, Any]) -> bytes:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode() + b"\n"


# Names engines answer with regardless of the served name (the node proxy rewrites the
# request model to them): mlx_lm.server reports ``default_model``.
ENGINE_UPSTREAM_NAMES = {"mlx": ("default_model",)}


def compile_model_rewrite(
    served: str, requested: str, also: Tuple[str, ...] = ()
) -> Optional[Tuple[re.Pattern, bytes]]:
    """Regex that rewrites ``"model": "<served>"`` (or an ``also`` name) to the requested name."""
    names = [n for n in (served, *also) if n and n != requested]
    if not names:
        return None
    alt = b"|".join(re.escape(n.encode()) for n in names)
    pattern = re.compile(rb'"model"\s*:\s*"(?:' + alt + rb')"')
    replacement = b'"model":"' + requested.encode() + b'"'
    return pattern, replacement


def rewrite_model_bytes(data: bytes, rewrite: Optional[Tuple[re.Pattern, bytes]]) -> bytes:
    if rewrite is None or b'"model"' not in data:
        return data
    pattern, replacement = rewrite
    return pattern.sub(replacement, data)
