"""
OpenAI Responses API <-> chat completions.

``responses_request_to_chat`` maps ``input`` items (messages, function calls
and outputs, reasoning items), ``instructions``, ``tools``, ``tool_choice``,
``text.format``, ``reasoning.effort`` and sampling to a chat request.
``chat_response_to_responses`` and ``ResponsesStreamBuilder`` build the
Response object / the typed ``response.*`` SSE events.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

from app.services.serving.errors import ServingError
from app.services.serving.sse import parse_sse_json, sse_event
from app.services.serving.thinking import (
    ThinkTagSplitter,
    apply_thinking_request,
    split_think_tags,
)
from app.services.serving.usage import extract_openai_usage


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


def _content_parts(content: Any) -> Any:
    if isinstance(content, str):
        return content
    parts: List[Dict[str, Any]] = []
    for part in content or []:
        if not isinstance(part, dict):
            continue
        ptype = part.get("type")
        if ptype in ("input_text", "output_text", "text"):
            parts.append({"type": "text", "text": part.get("text", "")})
        elif ptype == "input_image":
            url = part.get("image_url")
            if isinstance(url, dict):
                url = url.get("url")
            if url:
                img: Dict[str, Any] = {"url": url}
                if part.get("detail"):
                    img["detail"] = part["detail"]
                parts.append({"type": "image_url", "image_url": img})
        elif ptype == "input_file":
            file_part: Dict[str, Any] = {}
            if part.get("file_data"):
                file_part["file_data"] = part["file_data"]
            if part.get("filename"):
                file_part["filename"] = part["filename"]
            if part.get("file_id"):
                file_part["file_id"] = part["file_id"]
            parts.append({"type": "file", "file": file_part})
        elif ptype == "input_audio":
            parts.append({"type": "input_audio", "input_audio": part.get("input_audio") or {}})
    if len(parts) == 1 and parts[0]["type"] == "text":
        return parts[0]["text"]
    return parts


def responses_request_to_chat(body: Dict[str, Any]) -> Dict[str, Any]:
    if body.get("previous_response_id"):
        raise ServingError.invalid_request(
            "previous_response_id is not supported (stateless); resend the full input",
            "previous_response_id",
        )
    messages: List[Dict[str, Any]] = []
    instructions = body.get("instructions")
    if instructions:
        messages.append({"role": "system", "content": str(instructions)})
    inp = body.get("input")
    if inp is None:
        raise ServingError.invalid_request("input: field required", "input")
    if isinstance(inp, str):
        messages.append({"role": "user", "content": inp})
    else:
        pending_calls: List[Dict[str, Any]] = []

        def flush_calls() -> None:
            if pending_calls:
                messages.append(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": list(pending_calls),
                    }
                )
                pending_calls.clear()

        for item in inp:
            if not isinstance(item, dict):
                continue
            itype = item.get("type") or "message"
            if itype == "message":
                flush_calls()
                role = item.get("role", "user")
                if role == "developer":
                    role = "system"
                messages.append({"role": role, "content": _content_parts(item.get("content"))})
            elif itype == "function_call":
                pending_calls.append(
                    {
                        "id": item.get("call_id") or item.get("id") or new_id("call"),
                        "type": "function",
                        "function": {
                            "name": item.get("name"),
                            "arguments": item.get("arguments") or "{}",
                        },
                    }
                )
            elif itype == "function_call_output":
                flush_calls()
                output = item.get("output")
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": item.get("call_id"),
                        "content": (output if isinstance(output, str) else json.dumps(output)),
                    }
                )
            elif itype == "reasoning":
                continue
            elif itype == "item_reference":
                raise ServingError.invalid_request("item_reference requires stored state", "input")
        flush_calls()

    out: Dict[str, Any] = {
        "model": body.get("model"),
        "messages": messages,
        "stream": bool(body.get("stream", False)),
    }
    if body.get("max_output_tokens") is not None:
        out["max_tokens"] = body["max_output_tokens"]
    for key in (
        "temperature",
        "top_p",
        "user",
        "metadata",
        "seed",
        "top_logprobs",
        "logit_bias",
    ):
        if body.get(key) is not None:
            out[key] = body[key]
    if body.get("parallel_tool_calls") is not None:
        out["parallel_tool_calls"] = bool(body["parallel_tool_calls"])
    tools = []
    for t in body.get("tools") or []:
        if t.get("type") == "function":
            fn = {
                "name": t.get("name"),
                "description": t.get("description", ""),
                "parameters": t.get("parameters") or {"type": "object", "properties": {}},
            }
            if t.get("strict") is not None:
                fn["strict"] = bool(t["strict"])
            tools.append({"type": "function", "function": fn})
    if tools:
        out["tools"] = tools
    tc = body.get("tool_choice")
    if isinstance(tc, str):
        out["tool_choice"] = tc
    elif isinstance(tc, dict) and tc.get("type") == "function":
        out["tool_choice"] = {"type": "function", "function": {"name": tc.get("name")}}
    text_format = (body.get("text") or {}).get("format") or {}
    ftype = text_format.get("type")
    if ftype == "json_schema":
        out["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": text_format.get("name") or "response",
                "schema": text_format.get("schema") or {},
                "strict": bool(text_format.get("strict", True)),
            },
        }
    elif ftype == "json_object":
        out["response_format"] = {"type": "json_object"}
    reasoning = body.get("reasoning") or {}
    if reasoning.get("effort"):
        effort = {"minimal": "low", "none": "low", "xhigh": "high"}.get(
            reasoning["effort"], reasoning["effort"]
        )
        apply_thinking_request(out, enabled=reasoning["effort"] != "none", effort=effort)
    return out


# ---------------------------------------------------------------------------
# Response object
# ---------------------------------------------------------------------------


def _usage_block(usage) -> Dict[str, Any]:
    return {
        "input_tokens": int(usage.input_tokens),
        "input_tokens_details": {"cached_tokens": int(usage.cached_tokens)},
        "output_tokens": int(usage.output_tokens),
        "output_tokens_details": {"reasoning_tokens": int(usage.reasoning_tokens)},
        "total_tokens": int(usage.input_tokens) + int(usage.output_tokens),
    }


def response_skeleton(body: Dict[str, Any], response_id: str, status: str) -> Dict[str, Any]:
    reasoning = body.get("reasoning") or {}
    return {
        "id": response_id,
        "object": "response",
        "created_at": int(time.time()),
        "status": status,
        "background": bool(body.get("background", False)),
        "error": None,
        "incomplete_details": None,
        "instructions": body.get("instructions"),
        "max_output_tokens": body.get("max_output_tokens"),
        "model": body.get("model"),
        "output": [],
        "parallel_tool_calls": bool(body.get("parallel_tool_calls", True)),
        "previous_response_id": None,
        "reasoning": {
            "effort": reasoning.get("effort"),
            "summary": reasoning.get("summary"),
        },
        "store": bool(body.get("store", False)),
        "temperature": body.get("temperature", 1.0),
        "text": body.get("text") or {"format": {"type": "text"}},
        "tool_choice": body.get("tool_choice", "auto"),
        "tools": body.get("tools") or [],
        "top_p": body.get("top_p", 1.0),
        "truncation": body.get("truncation", "disabled"),
        "usage": None,
        "user": body.get("user"),
        "metadata": body.get("metadata") or {},
    }


def chat_response_to_responses(data: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    finish = choice.get("finish_reason")
    resp = response_skeleton(body, new_id("resp"), "completed")
    output: List[Dict[str, Any]] = []
    reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
    text = message.get("content") or ""
    if not reasoning and isinstance(text, str) and "<think>" in text:
        reasoning, text = split_think_tags(text)
    if reasoning:
        output.append(
            {
                "id": new_id("rs"),
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": reasoning}],
                "status": "completed",
            }
        )
    if text or not message.get("tool_calls"):
        output.append(
            {
                "id": new_id("msg"),
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text or "", "annotations": []}],
            }
        )
    for call in message.get("tool_calls") or []:
        fn = call.get("function") or {}
        output.append(
            {
                "id": new_id("fc"),
                "type": "function_call",
                "status": "completed",
                "call_id": call.get("id") or new_id("call"),
                "name": fn.get("name"),
                "arguments": fn.get("arguments") or "{}",
            }
        )
    resp["output"] = output
    if finish == "length":
        resp["status"] = "incomplete"
        resp["incomplete_details"] = {"reason": "max_output_tokens"}
    resp["usage"] = _usage_block(extract_openai_usage(data))
    return resp


class ResponsesStreamBuilder:
    """Chat chunks -> Responses API SSE events."""

    def __init__(self, body: Dict[str, Any], think_tags: bool = True) -> None:
        self.body = body
        self.response = response_skeleton(body, new_id("resp"), "in_progress")
        self.seq = 0
        self.output_index = -1
        self.current: Optional[Dict[str, Any]] = None  # open item
        self.current_kind: Optional[str] = None
        self.text_acc = ""
        self.reasoning_acc = ""
        self.tool_items: Dict[int, Dict[str, Any]] = {}
        self.finish: Optional[str] = None
        self.usage = None
        self.splitter = ThinkTagSplitter(enabled=think_tags)
        self.started = False

    def _ev(self, etype: str, **payload) -> bytes:
        self.seq += 1
        return sse_event(etype, {"type": etype, "sequence_number": self.seq, **payload})

    def start(self) -> List[bytes]:
        self.started = True
        return [
            self._ev("response.created", response=dict(self.response)),
            self._ev("response.in_progress", response=dict(self.response)),
        ]

    def _close_current(self) -> List[bytes]:
        if self.current is None:
            return []
        events = []
        item = self.current
        idx = self.output_index
        if self.current_kind == "message":
            item["content"][0]["text"] = self.text_acc
            events.append(
                self._ev(
                    "response.output_text.done",
                    item_id=item["id"],
                    output_index=idx,
                    content_index=0,
                    text=self.text_acc,
                )
            )
            events.append(
                self._ev(
                    "response.content_part.done",
                    item_id=item["id"],
                    output_index=idx,
                    content_index=0,
                    part=dict(item["content"][0]),
                )
            )
        elif self.current_kind == "reasoning":
            item["summary"][0]["text"] = self.reasoning_acc
            events.append(
                self._ev(
                    "response.reasoning_summary_text.done",
                    item_id=item["id"],
                    output_index=idx,
                    summary_index=0,
                    text=self.reasoning_acc,
                )
            )
            events.append(
                self._ev(
                    "response.reasoning_summary_part.done",
                    item_id=item["id"],
                    output_index=idx,
                    summary_index=0,
                    part=dict(item["summary"][0]),
                )
            )
        item["status"] = "completed"
        events.append(self._ev("response.output_item.done", output_index=idx, item=dict(item)))
        self.response["output"].append(item)
        self.current = None
        self.current_kind = None
        self.text_acc = ""
        self.reasoning_acc = ""
        return events

    def _open(self, kind: str) -> List[bytes]:
        events = self._close_current()
        self.output_index += 1
        if kind == "message":
            item = {
                "id": new_id("msg"),
                "type": "message",
                "status": "in_progress",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "", "annotations": []}],
            }
            events.append(
                self._ev(
                    "response.output_item.added",
                    output_index=self.output_index,
                    item=dict(item),
                )
            )
            events.append(
                self._ev(
                    "response.content_part.added",
                    item_id=item["id"],
                    output_index=self.output_index,
                    content_index=0,
                    part={"type": "output_text", "text": "", "annotations": []},
                )
            )
        else:
            item = {
                "id": new_id("rs"),
                "type": "reasoning",
                "status": "in_progress",
                "summary": [{"type": "summary_text", "text": ""}],
            }
            events.append(
                self._ev(
                    "response.output_item.added",
                    output_index=self.output_index,
                    item=dict(item),
                )
            )
            events.append(
                self._ev(
                    "response.reasoning_summary_part.added",
                    item_id=item["id"],
                    output_index=self.output_index,
                    summary_index=0,
                    part={"type": "summary_text", "text": ""},
                )
            )
        self.current = item
        self.current_kind = kind
        return events

    def _text(self, text: str) -> List[bytes]:
        if not text:
            return []
        events = []
        if self.current_kind != "message":
            events.extend(self._open("message"))
        self.text_acc += text
        events.append(
            self._ev(
                "response.output_text.delta",
                item_id=self.current["id"],
                output_index=self.output_index,
                content_index=0,
                delta=text,
                logprobs=[],
            )
        )
        return events

    def _reasoning(self, text: str) -> List[bytes]:
        if not text:
            return []
        events = []
        if self.current_kind != "reasoning":
            events.extend(self._open("reasoning"))
        self.reasoning_acc += text
        events.append(
            self._ev(
                "response.reasoning_summary_text.delta",
                item_id=self.current["id"],
                output_index=self.output_index,
                summary_index=0,
                delta=text,
            )
        )
        return events

    def _tool(self, tc: Dict[str, Any]) -> List[bytes]:
        events = self._close_current()
        idx = tc.get("index", 0)
        fn = tc.get("function") or {}
        if idx not in self.tool_items:
            self.output_index += 1
            item = {
                "id": new_id("fc"),
                "type": "function_call",
                "status": "in_progress",
                "call_id": tc.get("id") or new_id("call"),
                "name": fn.get("name") or "",
                "arguments": "",
            }
            self.tool_items[idx] = {"item": item, "output_index": self.output_index}
            events.append(
                self._ev(
                    "response.output_item.added",
                    output_index=self.output_index,
                    item=dict(item),
                )
            )
        slot = self.tool_items[idx]
        if fn.get("name") and not slot["item"]["name"]:
            slot["item"]["name"] = fn["name"]
        if fn.get("arguments"):
            slot["item"]["arguments"] += fn["arguments"]
            events.append(
                self._ev(
                    "response.function_call_arguments.delta",
                    item_id=slot["item"]["id"],
                    output_index=slot["output_index"],
                    delta=fn["arguments"],
                )
            )
        return events

    def feed(self, chunk: Dict[str, Any]) -> List[bytes]:
        events: List[bytes] = []
        if not self.started:
            events.extend(self.start())
        if chunk.get("usage"):
            self.usage = extract_openai_usage(chunk)
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
            content = delta.get("content") or ""
            if content:
                r, v = self.splitter.feed(content)
                reasoning += r
                content = v
            events.extend(self._reasoning(reasoning))
            events.extend(self._text(content))
            for tc in delta.get("tool_calls") or []:
                events.extend(self._tool(tc))
            if choice.get("finish_reason"):
                self.finish = choice["finish_reason"]
        return events

    def finish_events(self) -> List[bytes]:
        from app.services.serving.usage import Usage

        events: List[bytes] = []
        if not self.started:
            events.extend(self.start())
        r, v = self.splitter.flush()
        events.extend(self._reasoning(r))
        events.extend(self._text(v))
        if self.current is None and not self.tool_items and not self.response["output"]:
            events.extend(self._open("message"))
        events.extend(self._close_current())
        for idx in sorted(self.tool_items):
            slot = self.tool_items[idx]
            item = slot["item"]
            item["status"] = "completed"
            events.append(
                self._ev(
                    "response.function_call_arguments.done",
                    item_id=item["id"],
                    output_index=slot["output_index"],
                    arguments=item["arguments"],
                )
            )
            events.append(
                self._ev(
                    "response.output_item.done",
                    output_index=slot["output_index"],
                    item=dict(item),
                )
            )
            self.response["output"].append(item)
        self.response["output"].sort(key=lambda it: it.get("_oi", 0))
        self.response["status"] = "incomplete" if self.finish == "length" else "completed"
        if self.finish == "length":
            self.response["incomplete_details"] = {"reason": "max_output_tokens"}
        self.response["usage"] = _usage_block(self.usage or Usage())
        events.append(self._ev("response.completed", response=dict(self.response)))
        return events


async def chat_stream_to_responses(
    frames: AsyncIterator[bytes], builder: ResponsesStreamBuilder, collector=None
) -> AsyncIterator[bytes]:
    async for frame in frames:
        _, chunk, done = parse_sse_json(frame)
        if done:
            break
        if chunk is None:
            continue
        if collector is not None:
            collector.feed_openai_chunk(chunk)
        for ev in builder.feed(chunk):
            yield ev
    for ev in builder.finish_events():
        yield ev
