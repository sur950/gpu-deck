"""
Anthropic Messages API <-> OpenAI chat completions.

Request: ``anthropic_request_to_openai`` (system, messages with text / image
/ document / tool_use / tool_result / thinking / compaction blocks, tools,
tool_choice incl. ``disable_parallel_tool_use``, thinking, stop_sequences,
metadata, structured output). Response: ``openai_response_to_anthropic`` and
``openai_stream_to_anthropic`` (named SSE events with thinking, text and
tool_use blocks). Engine reasoning arrives either as ``reasoning_content`` or
as ``<think>`` tags; both become ``thinking`` blocks.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from app.services.serving.errors import ServingError
from app.services.serving.sse import parse_sse_json, sse_event
from app.services.serving.thinking import (
    ThinkTagSplitter,
    apply_thinking_request,
    effort_from_budget,
    split_think_tags,
    thinking_signature,
)
from app.services.serving.usage import extract_openai_usage

FINISH_TO_STOP = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "content_filter": "end_turn",
}


def new_message_id() -> str:
    return f"msg_{uuid.uuid4().hex[:24]}"


def new_tool_id() -> str:
    return f"toolu_{uuid.uuid4().hex[:24]}"


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


def _system_text(system: Any) -> str:
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        return "\n\n".join(
            b.get("text", "") for b in system if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _image_part(block: Dict[str, Any]) -> Dict[str, Any]:
    source = block.get("source") or {}
    if source.get("type") == "base64":
        url = f"data:{source.get('media_type', 'image/png')};base64,{source.get('data', '')}"
    else:
        url = source.get("url", "")
    return {"type": "image_url", "image_url": {"url": url}}


def _document_part(block: Dict[str, Any]) -> Dict[str, Any]:
    source = block.get("source") or {}
    title = block.get("title") or "document"
    stype = source.get("type")
    if stype == "text":
        return {
            "type": "text",
            "text": f"<document title=\"{title}\">\n{source.get('data', '')}\n</document>",
        }
    if stype == "content":
        inner = source.get("content")
        text = (
            "".join(b.get("text", "") for b in inner if isinstance(b, dict))
            if isinstance(inner, list)
            else str(inner or "")
        )
        return {
            "type": "text",
            "text": f'<document title="{title}">\n{text}\n</document>',
        }
    if stype == "base64":
        media = source.get("media_type", "application/pdf")
        return {
            "type": "file",
            "file": {
                "filename": title if "." in title else f"{title}.pdf",
                "file_data": f"data:{media};base64,{source.get('data', '')}",
            },
        }
    if stype == "url":
        return {
            "type": "text",
            "text": f"<document title=\"{title}\" url=\"{source.get('url')}\" />",
        }
    return {"type": "text", "text": f'<document title="{title}" />'}


def _tool_result_text(content: Any) -> Tuple[str, List[Dict[str, Any]]]:
    """(text, image_parts) of a tool_result content."""
    if content is None:
        return "", []
    if isinstance(content, str):
        return content, []
    if isinstance(content, list):
        texts, images = [], []
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text":
                texts.append(b.get("text", ""))
            elif b.get("type") == "image":
                images.append(_image_part(b))
            elif b.get("type") == "document":
                texts.append(_document_part(b).get("text", ""))
        return "".join(texts), images
    return json.dumps(content, ensure_ascii=False), []


def _messages_to_openai(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        blocks = content or []
        if role == "assistant":
            text_parts: List[str] = []
            tool_calls: List[Dict[str, Any]] = []
            reasoning: List[str] = []
            for block in blocks:
                btype = block.get("type")
                if btype == "text":
                    text_parts.append(block.get("text", ""))
                elif btype == "tool_use":
                    tool_calls.append(
                        {
                            "id": block.get("id") or new_tool_id(),
                            "type": "function",
                            "function": {
                                "name": block.get("name"),
                                "arguments": json.dumps(block.get("input") or {}),
                            },
                        }
                    )
                elif btype == "thinking":
                    reasoning.append(block.get("thinking", ""))
                elif btype == "compaction":
                    out.append(
                        {
                            "role": "system",
                            "content": "Summary of the earlier conversation:\n"
                            + str(block.get("content") or ""),
                        }
                    )
            if not text_parts and not tool_calls and not reasoning:
                continue
            entry: Dict[str, Any] = {
                "role": "assistant",
                "content": "".join(text_parts) or None,
            }
            if tool_calls:
                entry["tool_calls"] = tool_calls
            out.append(entry)
            continue
        # user: text/image/document aggregate; tool_result -> role tool, in order
        parts: List[Dict[str, Any]] = []

        def flush() -> None:
            if not parts:
                return
            if len(parts) == 1 and parts[0]["type"] == "text":
                out.append({"role": "user", "content": parts[0]["text"]})
            else:
                out.append({"role": "user", "content": list(parts)})
            parts.clear()

        for block in blocks:
            btype = block.get("type")
            if btype == "text":
                parts.append({"type": "text", "text": block.get("text", "")})
            elif btype == "image":
                parts.append(_image_part(block))
            elif btype == "document":
                parts.append(_document_part(block))
            elif btype == "tool_result":
                flush()
                text, images = _tool_result_text(block.get("content"))
                if block.get("is_error"):
                    text = text or "error"
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id"),
                        "content": text,
                    }
                )
                if images:
                    parts.extend(images)
        flush()
    return out


def _tools_to_openai(tools: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    converted = []
    for t in tools or []:
        ttype = t.get("type")
        if ttype and ttype != "custom" and "input_schema" not in t:
            # Anthropic server/built-in tools (bash_*, text_editor_*, web_search...) have no
            # schema an OpenAI engine could run; expose them as plain functions.
            converted.append(
                {
                    "type": "function",
                    "function": {
                        "name": t.get("name"),
                        "description": t.get("description", f"Anthropic tool {ttype}"),
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            )
            continue
        fn: Dict[str, Any] = {
            "name": t.get("name"),
            "description": t.get("description", ""),
            "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
        }
        if t.get("strict") is not None:
            fn["strict"] = bool(t.get("strict"))
        converted.append({"type": "function", "function": fn})
    return converted


def _tool_choice_to_openai(tool_choice: Any) -> Tuple[Any, Optional[bool]]:
    """(openai tool_choice, parallel_tool_calls)"""
    if not isinstance(tool_choice, dict):
        return None, None
    parallel = None
    if tool_choice.get("disable_parallel_tool_use") is not None:
        parallel = not bool(tool_choice.get("disable_parallel_tool_use"))
    kind = tool_choice.get("type")
    if kind == "auto":
        return "auto", parallel
    if kind == "any":
        return "required", parallel
    if kind == "none":
        return "none", parallel
    if kind == "tool":
        return {
            "type": "function",
            "function": {"name": tool_choice.get("name")},
        }, parallel
    return None, parallel


def anthropic_request_to_openai(
    body: Dict[str, Any], *, family: Optional[str] = None, summary: Optional[str] = None
) -> Dict[str, Any]:
    """Translate an Anthropic /v1/messages body to OpenAI chat/completions."""
    if not isinstance(body.get("messages"), list) or not body["messages"]:
        raise ServingError.invalid_request("messages: field required", "messages")
    if body.get("max_tokens") is None:
        raise ServingError.invalid_request("max_tokens: field required", "max_tokens")
    messages: List[Dict[str, Any]] = []
    system = _system_text(body.get("system"))
    if system:
        messages.append({"role": "system", "content": system})
    if summary:
        messages.append(
            {
                "role": "system",
                "content": "Summary of the earlier conversation:\n" + summary,
            }
        )
    src = [
        m
        for m in body["messages"]
        if not (
            isinstance(m.get("content"), list)
            and m["content"]
            and all(isinstance(b, dict) and b.get("type") == "compaction" for b in m["content"])
        )
    ]
    messages.extend(_messages_to_openai(src))

    out: Dict[str, Any] = {
        "model": body.get("model"),
        "messages": messages,
        "max_tokens": body.get("max_tokens"),
        "stream": bool(body.get("stream", False)),
    }
    for key in ("temperature", "top_p", "top_k", "seed"):
        if body.get(key) is not None:
            out[key] = body[key]
    if body.get("stop_sequences"):
        out["stop"] = list(body["stop_sequences"])
    tools = _tools_to_openai(body.get("tools"))
    if tools:
        out["tools"] = tools
    choice, parallel = _tool_choice_to_openai(body.get("tool_choice"))
    if choice is not None:
        out["tool_choice"] = choice
    if parallel is not None:
        out["parallel_tool_calls"] = parallel
    metadata = body.get("metadata") or {}
    if metadata.get("user_id"):
        out["user"] = str(metadata["user_id"])

    thinking = body.get("thinking")
    if isinstance(thinking, dict):
        ttype = thinking.get("type")
        if ttype == "enabled":
            apply_thinking_request(
                out,
                enabled=True,
                effort=effort_from_budget(thinking.get("budget_tokens")),
            )
        elif ttype == "adaptive":
            apply_thinking_request(out, enabled=True, effort="medium")
        elif ttype == "disabled":
            apply_thinking_request(out, enabled=False)
    effort = (body.get("output_config") or {}).get("effort")
    if effort:
        out["reasoning_effort"] = {"xhigh": "high", "max": "high"}.get(effort, effort)

    fmt = (body.get("output_config") or {}).get("format") or body.get("output_format")
    if isinstance(fmt, dict) and fmt.get("type") == "json_schema":
        out["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": fmt.get("name") or "response",
                "schema": fmt.get("schema") or {},
                "strict": True,
            },
        }
    return out


# ---------------------------------------------------------------------------
# Response (non-streaming)
# ---------------------------------------------------------------------------


def _thinking_block(text: str) -> Dict[str, Any]:
    return {"type": "thinking", "thinking": text, "signature": thinking_signature(text)}


def openai_response_to_anthropic(
    data: Dict[str, Any],
    model_name: str,
    *,
    extra_blocks: Optional[List[Dict[str, Any]]] = None,
    context_management: Optional[Dict[str, Any]] = None,
    iterations: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    finish = choice.get("finish_reason")
    blocks: List[Dict[str, Any]] = list(extra_blocks or [])

    reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
    text = message.get("content") or ""
    if not reasoning and isinstance(text, str) and "<think>" in text:
        reasoning, text = split_think_tags(text)
    if reasoning:
        blocks.append(_thinking_block(reasoning))
    if text:
        blocks.append({"type": "text", "text": text})
    for call in message.get("tool_calls") or []:
        fn = call.get("function") or {}
        try:
            tool_input = json.loads(fn.get("arguments") or "{}")
        except (json.JSONDecodeError, ValueError):
            tool_input = {"_raw": fn.get("arguments")}
        blocks.append(
            {
                "type": "tool_use",
                "id": call.get("id") or new_tool_id(),
                "name": fn.get("name"),
                "input": tool_input,
            }
        )
    if not blocks:
        blocks.append({"type": "text", "text": ""})
    usage = extract_openai_usage(data)
    stop_reason = FINISH_TO_STOP.get(finish, "end_turn")
    if message.get("tool_calls") and finish == "stop":
        stop_reason = "tool_use"
    out: Dict[str, Any] = {
        "id": new_message_id(),
        "type": "message",
        "role": "assistant",
        "model": model_name,
        "content": blocks,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": _usage(usage.input_tokens, usage.output_tokens, usage.cached_tokens, iterations),
    }
    if context_management is not None:
        out["context_management"] = context_management
    return out


def _usage(
    input_tokens: int,
    output_tokens: int,
    cached: int,
    iterations: Optional[List[Dict[str, Any]]],
) -> Dict[str, Any]:
    usage: Dict[str, Any] = {
        "input_tokens": max(0, int(input_tokens) - int(cached or 0)),
        "output_tokens": int(output_tokens),
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": int(cached or 0),
        "service_tier": "standard",
    }
    if iterations is not None:
        usage["iterations"] = iterations
    return usage


def compaction_only_response(
    model_name: str,
    summary: Optional[str],
    stop_reason: str,
    iteration_usage: Dict[str, int],
) -> Dict[str, Any]:
    from app.services.serving.compaction import make_compaction_block

    content = [make_compaction_block(summary)] if summary else []
    return {
        "id": new_message_id(),
        "type": "message",
        "role": "assistant",
        "model": model_name,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "iterations": [
                {
                    "type": "compaction",
                    "input_tokens": int(iteration_usage.get("input_tokens", 0)),
                    "output_tokens": int(iteration_usage.get("output_tokens", 0)),
                }
            ],
        },
    }


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


class AnthropicStreamBuilder:
    """Turns OpenAI chat chunks into Anthropic SSE events incrementally."""

    def __init__(
        self,
        model_name: str,
        *,
        think_tags: bool = True,
        prelude_blocks: Optional[List[Dict[str, Any]]] = None,
        context_management: Optional[Dict[str, Any]] = None,
        iterations: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self.model_name = model_name
        self.message_id = new_message_id()
        self.started = False
        self.next_index = 0
        self.open_kind: Optional[str] = None  # thinking | text
        self.open_index = -1
        self.thinking_text = ""
        self.tool_blocks: Dict[int, Dict[str, Any]] = {}
        self.input_tokens = 0
        self.output_tokens = 0
        self.cached_tokens = 0
        self.stop_reason = "end_turn"
        self.saw_tool_calls = False
        self.splitter = ThinkTagSplitter(enabled=think_tags)
        self.prelude_blocks = prelude_blocks or []
        self.context_management = context_management
        self.iterations = iterations

    # -- events ----------------------------------------------------------

    def _start(self) -> List[bytes]:
        self.started = True
        events = [
            sse_event(
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": self.message_id,
                        "type": "message",
                        "role": "assistant",
                        "model": self.model_name,
                        "content": [],
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "cache_creation_input_tokens": 0,
                            "cache_read_input_tokens": 0,
                        },
                    },
                },
            ),
            sse_event("ping", {"type": "ping"}),
        ]
        for block in self.prelude_blocks:
            idx = self.next_index
            self.next_index += 1
            events.append(
                sse_event(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": idx,
                        "content_block": block,
                    },
                )
            )
            events.append(
                sse_event("content_block_stop", {"type": "content_block_stop", "index": idx})
            )
        return events

    def _open(self, kind: str) -> List[bytes]:
        events = self._close()
        self.open_kind = kind
        self.open_index = self.next_index
        self.next_index += 1
        block = (
            {"type": "thinking", "thinking": "", "signature": ""}
            if kind == "thinking"
            else {"type": "text", "text": ""}
        )
        events.append(
            sse_event(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": self.open_index,
                    "content_block": block,
                },
            )
        )
        return events

    def _close(self) -> List[bytes]:
        if self.open_kind is None:
            return []
        events = []
        if self.open_kind == "thinking":
            events.append(
                sse_event(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": self.open_index,
                        "delta": {
                            "type": "signature_delta",
                            "signature": thinking_signature(self.thinking_text),
                        },
                    },
                )
            )
            self.thinking_text = ""
        events.append(
            sse_event(
                "content_block_stop",
                {"type": "content_block_stop", "index": self.open_index},
            )
        )
        self.open_kind = None
        return events

    def _delta(self, kind: str, text: str) -> List[bytes]:
        if not text:
            return []
        events = []
        if self.open_kind != kind:
            events.extend(self._open(kind))
        if kind == "thinking":
            self.thinking_text += text
            delta = {"type": "thinking_delta", "thinking": text}
        else:
            delta = {"type": "text_delta", "text": text}
        events.append(
            sse_event(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": self.open_index,
                    "delta": delta,
                },
            )
        )
        return events

    def feed(self, chunk: Dict[str, Any]) -> List[bytes]:
        events: List[bytes] = []
        if not self.started:
            events.extend(self._start())
        usage = chunk.get("usage")
        if usage:
            u = extract_openai_usage(chunk)
            self.input_tokens = u.input_tokens
            self.output_tokens = u.output_tokens
            self.cached_tokens = u.cached_tokens
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
            if reasoning:
                events.extend(self._delta("thinking", reasoning))
            content = delta.get("content")
            if content:
                r, v = self.splitter.feed(content)
                events.extend(self._delta("thinking", r))
                events.extend(self._delta("text", v))
            for tc in delta.get("tool_calls") or []:
                events.extend(self._tool_call(tc))
            finish = choice.get("finish_reason")
            if finish:
                self.stop_reason = FINISH_TO_STOP.get(finish, "end_turn")
        return events

    def _tool_call(self, tc: Dict[str, Any]) -> List[bytes]:
        events = self._close()
        self.saw_tool_calls = True
        idx = tc.get("index", 0)
        fn = tc.get("function") or {}
        if idx not in self.tool_blocks:
            block_index = self.next_index
            self.next_index += 1
            self.tool_blocks[idx] = {
                "block_index": block_index,
                "id": tc.get("id") or new_tool_id(),
                "name": fn.get("name") or "",
                "args": "",
            }
            events.append(
                sse_event(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": block_index,
                        "content_block": {
                            "type": "tool_use",
                            "id": self.tool_blocks[idx]["id"],
                            "name": self.tool_blocks[idx]["name"],
                            "input": {},
                        },
                    },
                )
            )
        elif fn.get("name") and not self.tool_blocks[idx]["name"]:
            self.tool_blocks[idx]["name"] = fn["name"]
        args = fn.get("arguments")
        if args:
            self.tool_blocks[idx]["args"] += args
            events.append(
                sse_event(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": self.tool_blocks[idx]["block_index"],
                        "delta": {"type": "input_json_delta", "partial_json": args},
                    },
                )
            )
        return events

    def finish(self) -> List[bytes]:
        events: List[bytes] = []
        if not self.started:
            events.extend(self._start())
        r, v = self.splitter.flush()
        events.extend(self._delta("thinking", r))
        events.extend(self._delta("text", v))
        if self.next_index == 0:
            events.extend(self._open("text"))
        events.extend(self._close())
        for tb in self.tool_blocks.values():
            events.append(
                sse_event(
                    "content_block_stop",
                    {"type": "content_block_stop", "index": tb["block_index"]},
                )
            )
        stop = self.stop_reason
        if self.saw_tool_calls and stop == "end_turn":
            stop = "tool_use"
        usage = _usage(self.input_tokens, self.output_tokens, self.cached_tokens, self.iterations)
        delta_event: Dict[str, Any] = {
            "type": "message_delta",
            "delta": {"stop_reason": stop, "stop_sequence": None},
            "usage": usage,
        }
        if self.context_management is not None:
            delta_event["context_management"] = self.context_management
        events.append(sse_event("message_delta", delta_event))
        events.append(sse_event("message_stop", {"type": "message_stop"}))
        return events


async def openai_stream_to_anthropic(
    frames: AsyncIterator[bytes], builder: AnthropicStreamBuilder, collector=None
) -> AsyncIterator[bytes]:
    """Consume OpenAI SSE frames and yield Anthropic SSE bytes."""
    async for frame in frames:
        _, chunk, done = parse_sse_json(frame)
        if done:
            break
        if chunk is None:
            continue
        if collector is not None:
            collector.feed_openai_chunk(chunk)
        for event in builder.feed(chunk):
            yield event
    for event in builder.finish():
        yield event


# ---------------------------------------------------------------------------
# Anthropic native stream passthrough helpers (engine speaks Anthropic)
# ---------------------------------------------------------------------------


def anthropic_models_list(entries, now_iso: str) -> Dict[str, Any]:
    data = [
        {
            "type": "model",
            "id": e.name,
            "display_name": e.display_name or e.name,
            "created_at": e.created_at or now_iso,
        }
        for e in entries
    ]
    return {
        "data": data,
        "has_more": False,
        "first_id": data[0]["id"] if data else None,
        "last_id": data[-1]["id"] if data else None,
    }
