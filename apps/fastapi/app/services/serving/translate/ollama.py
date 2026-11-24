"""
Ollama API <-> OpenAI.

``/api/chat`` and ``/api/generate`` map to chat completions (``/api/generate``
with ``raw=true`` maps to text completions), ``/api/embed`` and the legacy
``/api/embeddings`` map to embeddings. Streaming replies are NDJSON objects;
the final object carries ``done: true`` with the Ollama timing/usage fields
measured at the API layer.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from app.services.serving.errors import ServingError
from app.services.serving.sse import ndjson, parse_sse_json
from app.services.serving.thinking import (
    ThinkTagSplitter,
    apply_thinking_request,
    split_think_tags,
)
from app.services.serving.usage import extract_openai_usage

OPTION_MAP = {
    "temperature": "temperature",
    "top_p": "top_p",
    "top_k": "top_k",
    "min_p": "min_p",
    "seed": "seed",
    "num_predict": "max_tokens",
    "stop": "stop",
    "presence_penalty": "presence_penalty",
    "frequency_penalty": "frequency_penalty",
    "repeat_penalty": "repetition_penalty",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _apply_options(out: Dict[str, Any], options: Optional[Dict[str, Any]]) -> None:
    for key, value in (options or {}).items():
        target = OPTION_MAP.get(key)
        if target is None or value is None:
            continue
        if key == "stop" and isinstance(value, str):
            value = [value]
        out[target] = value


def _apply_format(out: Dict[str, Any], fmt: Any) -> None:
    if fmt == "json":
        out["response_format"] = {"type": "json_object"}
    elif isinstance(fmt, dict):
        out["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "response", "schema": fmt, "strict": True},
        }


def _apply_think(out: Dict[str, Any], think: Any) -> None:
    if think is None:
        return
    if isinstance(think, bool):
        apply_thinking_request(out, enabled=think)
    elif isinstance(think, str):
        apply_thinking_request(out, enabled=True, effort=think)


def _user_content(text: str, images: Optional[List[str]]) -> Any:
    if not images:
        return text
    parts: List[Dict[str, Any]] = [{"type": "text", "text": text}] if text else []
    for img in images:
        url = img if str(img).startswith("data:") else f"data:image/png;base64,{img}"
        parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts


def ollama_chat_to_openai(body: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(body.get("messages"), list):
        raise ServingError.invalid_request("messages: field required", "messages")
    messages: List[Dict[str, Any]] = []
    for msg in body["messages"]:
        role = msg.get("role", "user")
        if role == "tool":
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": msg.get("tool_call_id") or msg.get("tool_name") or "call_0",
                    "content": str(msg.get("content") or ""),
                }
            )
            continue
        entry: Dict[str, Any] = {
            "role": role,
            "content": _user_content(str(msg.get("content") or ""), msg.get("images")),
        }
        calls = []
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function") or {}
            args = fn.get("arguments")
            calls.append(
                {
                    "id": tc.get("id") or f"call_{i}",
                    "type": "function",
                    "function": {
                        "name": fn.get("name"),
                        "arguments": (args if isinstance(args, str) else json.dumps(args or {})),
                    },
                }
            )
        if calls:
            entry["tool_calls"] = calls
            if entry["content"] == "":
                entry["content"] = None
        messages.append(entry)
    out: Dict[str, Any] = {
        "model": body.get("model"),
        "messages": messages,
        "stream": bool(body.get("stream", True)),
    }
    if body.get("tools"):
        out["tools"] = body["tools"]
    _apply_options(out, body.get("options"))
    _apply_format(out, body.get("format"))
    _apply_think(out, body.get("think"))
    return out


def ollama_generate_to_openai(body: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Returns (engine_path, openai_body); raw=true uses /v1/completions."""
    prompt = body.get("prompt")
    if prompt is None and not body.get("suffix"):
        raise ServingError.invalid_request("prompt: field required", "prompt")
    stream = bool(body.get("stream", True))
    if body.get("raw"):
        out: Dict[str, Any] = {
            "model": body.get("model"),
            "prompt": prompt or "",
            "stream": stream,
        }
        if body.get("suffix"):
            out["suffix"] = body["suffix"]
        _apply_options(out, body.get("options"))
        return "v1/completions", out
    messages: List[Dict[str, Any]] = []
    if body.get("system"):
        messages.append({"role": "system", "content": str(body["system"])})
    messages.append(
        {
            "role": "user",
            "content": _user_content(str(prompt or ""), body.get("images")),
        }
    )
    out = {"model": body.get("model"), "messages": messages, "stream": stream}
    _apply_options(out, body.get("options"))
    _apply_format(out, body.get("format"))
    _apply_think(out, body.get("think"))
    return "v1/chat/completions", out


def ollama_embed_to_openai(body: Dict[str, Any], legacy: bool = False) -> Dict[str, Any]:
    inp = body.get("prompt") if legacy else body.get("input")
    if inp is None:
        raise ServingError.invalid_request("input: field required", "prompt" if legacy else "input")
    out: Dict[str, Any] = {"model": body.get("model"), "input": inp}
    if body.get("dimensions"):
        out["dimensions"] = body["dimensions"]
    if body.get("truncate") is not None:
        out["truncate"] = body["truncate"]
    return out


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


def _stats(usage, started_ns: int, first_token_ns: Optional[int]) -> Dict[str, Any]:
    now = time.monotonic_ns()
    total = max(0, now - started_ns)
    prompt_dur = max(0, (first_token_ns or now) - started_ns)
    eval_dur = max(0, now - (first_token_ns or now))
    return {
        "total_duration": total,
        "load_duration": 0,
        "prompt_eval_count": int(usage.input_tokens),
        "prompt_eval_duration": prompt_dur,
        "eval_count": int(usage.output_tokens),
        "eval_duration": eval_dur,
    }


def _done_reason(finish: Optional[str], has_tools: bool) -> str:
    if finish == "length":
        return "length"
    if has_tools or finish == "tool_calls":
        return "stop"
    return "stop"


def _tool_calls_to_ollama(calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for call in calls or []:
        fn = call.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except (json.JSONDecodeError, ValueError):
            args = {"_raw": fn.get("arguments")}
        out.append({"function": {"name": fn.get("name"), "arguments": args}})
    return out


def openai_chat_to_ollama_chat(
    data: Dict[str, Any], model: str, started_ns: int, generate: bool = False
) -> Dict[str, Any]:
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    text = message.get("content") or ""
    reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
    if not reasoning and "<think>" in text:
        reasoning, text = split_think_tags(text)
    usage = extract_openai_usage(data)
    calls = message.get("tool_calls") or []
    out: Dict[str, Any] = {"model": model, "created_at": now_iso()}
    if generate:
        out["response"] = text
        if reasoning:
            out["thinking"] = reasoning
    else:
        msg: Dict[str, Any] = {"role": "assistant", "content": text}
        if reasoning:
            msg["thinking"] = reasoning
        if calls:
            msg["tool_calls"] = _tool_calls_to_ollama(calls)
        out["message"] = msg
    out["done"] = True
    out["done_reason"] = _done_reason(choice.get("finish_reason"), bool(calls))
    if generate:
        out["context"] = []
    out.update(_stats(usage, started_ns, None))
    return out


def openai_completion_to_ollama_generate(
    data: Dict[str, Any], model: str, started_ns: int
) -> Dict[str, Any]:
    choice = (data.get("choices") or [{}])[0]
    usage = extract_openai_usage(data)
    out = {
        "model": model,
        "created_at": now_iso(),
        "response": choice.get("text") or "",
        "done": True,
        "done_reason": "length" if choice.get("finish_reason") == "length" else "stop",
        "context": [],
    }
    out.update(_stats(usage, started_ns, None))
    return out


def openai_embeddings_to_ollama(
    data: Dict[str, Any], model: str, started_ns: int, legacy: bool = False
) -> Dict[str, Any]:
    vectors = [item.get("embedding") for item in data.get("data") or []]
    if legacy:
        return {"embedding": vectors[0] if vectors else []}
    usage = extract_openai_usage(data, "embeddings")
    return {
        "model": model,
        "embeddings": vectors,
        "total_duration": max(0, time.monotonic_ns() - started_ns),
        "load_duration": 0,
        "prompt_eval_count": int(usage.input_tokens),
    }


class OllamaStreamBuilder:
    """OpenAI chat/completion chunks -> Ollama NDJSON objects."""

    def __init__(self, model: str, *, generate: bool, completions: bool = False, think_tags=True):
        self.model = model
        self.generate = generate
        self.completions = completions
        self.started_ns = time.monotonic_ns()
        self.first_token_ns: Optional[int] = None
        self.splitter = ThinkTagSplitter(enabled=think_tags)
        self.finish: Optional[str] = None
        self.usage = None
        self.tool_calls: Dict[int, Dict[str, Any]] = {}

    def _obj(
        self, content: str = "", thinking: str = "", tool_calls=None, done=False
    ) -> Dict[str, Any]:
        obj: Dict[str, Any] = {"model": self.model, "created_at": now_iso()}
        if self.generate:
            obj["response"] = content
            if thinking:
                obj["thinking"] = thinking
        else:
            msg: Dict[str, Any] = {"role": "assistant", "content": content}
            if thinking:
                msg["thinking"] = thinking
            if tool_calls:
                msg["tool_calls"] = tool_calls
            obj["message"] = msg
        obj["done"] = done
        return obj

    def feed(self, chunk: Dict[str, Any]) -> List[bytes]:
        out: List[bytes] = []
        if chunk.get("usage"):
            self.usage = extract_openai_usage(chunk)
        for choice in chunk.get("choices") or []:
            if self.completions:
                text = choice.get("text") or ""
                if text:
                    self._mark_first()
                    out.append(ndjson(self._obj(content=text)))
            else:
                delta = choice.get("delta") or {}
                reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
                content = delta.get("content") or ""
                if content:
                    r, v = self.splitter.feed(content)
                    reasoning += r
                    content = v
                if reasoning:
                    self._mark_first()
                    out.append(ndjson(self._obj(thinking=reasoning)))
                if content:
                    self._mark_first()
                    out.append(ndjson(self._obj(content=content)))
                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    fn = tc.get("function") or {}
                    slot = self.tool_calls.setdefault(idx, {"name": "", "args": ""})
                    if fn.get("name"):
                        slot["name"] = fn["name"]
                    if fn.get("arguments"):
                        slot["args"] += fn["arguments"]
            if choice.get("finish_reason"):
                self.finish = choice["finish_reason"]
        return out

    def _mark_first(self) -> None:
        if self.first_token_ns is None:
            self.first_token_ns = time.monotonic_ns()

    def finish_frames(self) -> List[bytes]:
        out: List[bytes] = []
        r, v = self.splitter.flush()
        if r:
            out.append(ndjson(self._obj(thinking=r)))
        if v:
            out.append(ndjson(self._obj(content=v)))
        calls = []
        for idx in sorted(self.tool_calls):
            slot = self.tool_calls[idx]
            try:
                args = json.loads(slot["args"] or "{}")
            except (json.JSONDecodeError, ValueError):
                args = {"_raw": slot["args"]}
            calls.append({"function": {"name": slot["name"], "arguments": args}})
        if calls and not self.generate:
            out.append(ndjson(self._obj(tool_calls=calls)))
        from app.services.serving.usage import Usage

        usage = self.usage or Usage()
        final = self._obj(done=True)
        final["done_reason"] = _done_reason(self.finish, bool(calls))
        if self.generate:
            final["context"] = []
        final.update(_stats(usage, self.started_ns, self.first_token_ns))
        out.append(ndjson(final))
        return out


async def openai_stream_to_ollama(
    frames: AsyncIterator[bytes], builder: OllamaStreamBuilder, collector=None
) -> AsyncIterator[bytes]:
    async for frame in frames:
        _, chunk, done = parse_sse_json(frame)
        if done:
            break
        if chunk is None:
            continue
        if collector is not None:
            collector.feed_openai_chunk(chunk)
        for line in builder.feed(chunk):
            yield line
    for line in builder.finish_frames():
        yield line


# ---------------------------------------------------------------------------
# Model listing
# ---------------------------------------------------------------------------


def _details(entry) -> Dict[str, Any]:
    v = entry.version or {}
    return {
        "parent_model": "",
        "format": v.get("format") or "gguf",
        "family": entry.family or "",
        "families": [entry.family] if entry.family else [],
        "parameter_size": v.get("parameter_size") or "",
        "quantization_level": v.get("quantization") or "",
    }


def ollama_tags(entries) -> Dict[str, Any]:
    models = []
    for e in entries:
        v = e.version or {}
        models.append(
            {
                "name": e.name,
                "model": e.name,
                "modified_at": v.get("modified_at") or e.created_at or now_iso(),
                "size": int(v.get("size_bytes") or 0),
                "digest": v.get("digest") or "",
                "details": _details(e),
            }
        )
    return {"models": models}


def ollama_ps(entries) -> Dict[str, Any]:
    models = []
    for e in entries:
        v = e.version or {}
        for inst in e.ready_instances:
            models.append(
                {
                    "name": e.name,
                    "model": e.name,
                    "size": int(v.get("size_bytes") or 0),
                    "digest": v.get("digest") or "",
                    "details": _details(e),
                    "expires_at": None,
                    "size_vram": int(v.get("size_bytes") or 0),
                    "context_length": e.context_size,
                    "instance_id": inst.instance_id,
                    "node_id": inst.node_id,
                }
            )
    return {"models": models}


def ollama_show(entry) -> Dict[str, Any]:
    v = entry.version or {}
    out: Dict[str, Any] = {
        "modelfile": f"# GPU Deck managed model {entry.name}",
        "parameters": v.get("parameters") or "",
        "template": v.get("template") or "",
        "details": _details(entry),
        "model_info": {
            "general.architecture": entry.family or "",
            "general.parameter_count": v.get("parameter_count"),
            "gpudeck.context_size": entry.context_size,
            "gpudeck.engine": entry.engine,
        },
        "capabilities": ["completion"]
        + (["tools"] if entry.type in ("chat", "thinking", "vision") else [])
        + (["vision"] if entry.type == "vision" else [])
        + (["embedding"] if entry.type == "embedding" else [])
        + (["thinking"] if entry.type == "thinking" else []),
        "modified_at": v.get("modified_at") or entry.created_at or now_iso(),
    }
    return out
