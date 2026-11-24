"""
Anthropic compaction and context management performed by the API layer.

Three request shapes are supported on ``/v1/messages`` (checked against the
Anthropic docs on 2026-11-16):

1. On-demand compaction — beta ``compact-2026-09-04``, top-level
   ``"compaction": {"type": "summarize", "instructions": "..."}``. The response
   is a single ``{"type": "compaction", "content": <summary>, "signature": ...}``
   block with ``stop_reason: "compaction"`` and ``usage.iterations``.
2. Threshold compaction — beta ``compact-2026-01-12``,
   ``context_management.edits: [{"type": "compact_20260112", "trigger":
   {"type": "input_tokens", "value": N}, "pause_after_compaction": bool,
   "instructions": "..."}]``. When the prompt reaches the trigger the API
   layer summarises first and (unless paused) answers from the summary; the
   compaction block is prepended to ``content``.
3. Context editing — beta ``context-management-2025-06-27``,
   ``clear_tool_uses_20250919`` / ``clear_thinking_20251015`` edits applied
   client-side; the response carries ``context_management.applied_edits``.

A ``compaction`` block sent back in ``messages`` is verified (HMAC over the
summary with ``SECRET_KEY``) and rendered as a system-level summary message
for OpenAI engines. Engines that speak Anthropic natively get everything
passed through untouched, including the beta header.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Dict, List, Optional, Tuple

from app.core.logger import get_logger
from app.core.settings import settings
from app.services.serving.errors import ServingError

logger = get_logger(__name__)

BETA_COMPACT_ON_DEMAND = "compact-2026-09-04"
BETA_COMPACT_THRESHOLD = "compact-2026-01-12"
BETA_CONTEXT_MANAGEMENT = "context-management-2025-06-27"
COMPACT_EDIT_TYPE = "compact_20260112"
CLEAR_TOOL_USES = "clear_tool_uses_20250919"
CLEAR_THINKING = "clear_thinking_20251015"
DEFAULT_TRIGGER_TOKENS = 150_000
MIN_TRIGGER_TOKENS = 50_000

DEFAULT_INSTRUCTIONS = (
    "You are compacting a conversation so it can continue with less context. Write a "
    "thorough summary of the conversation so far: the user's goals and open requests, every "
    "decision, fact, name, identifier, code snippet or value that later turns may need, and "
    "the current state of any task. Do not call tools. Respond with the summary text only."
)


def sign_payload(kind: str, text: str) -> str:
    key = settings.SECRET_KEY.encode()
    digest = hmac.new(key, f"{kind}\n{text}".encode(), hashlib.sha256).hexdigest()
    return f"gd1:{digest[:48]}"


def verify_signature(kind: str, text: str, signature: Optional[str]) -> bool:
    if not signature:
        return False
    return hmac.compare_digest(sign_payload(kind, text), str(signature))


def requested_betas(headers) -> List[str]:
    raw = headers.get("anthropic-beta") or ""
    return [b.strip() for b in raw.split(",") if b.strip()]


# ---------------------------------------------------------------------------
# Request inspection
# ---------------------------------------------------------------------------


def wants_on_demand_compaction(body: Dict[str, Any]) -> bool:
    comp = body.get("compaction")
    return isinstance(comp, dict) and comp.get("type", "summarize") == "summarize"


def compaction_edit(body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    cm = body.get("context_management") or {}
    for edit in cm.get("edits") or []:
        if isinstance(edit, dict) and edit.get("type") == COMPACT_EDIT_TYPE:
            return edit
    return None


def clearing_edits(body: Dict[str, Any]) -> List[Dict[str, Any]]:
    cm = body.get("context_management") or {}
    return [
        e
        for e in cm.get("edits") or []
        if isinstance(e, dict) and e.get("type") in (CLEAR_TOOL_USES, CLEAR_THINKING)
    ]


def validate_compaction_request(body: Dict[str, Any]) -> None:
    if wants_on_demand_compaction(body) and body.get("context_management"):
        raise ServingError.invalid_request(
            "compaction cannot be combined with context_management", "compaction"
        )
    if wants_on_demand_compaction(body):
        tc = body.get("tool_choice") or {}
        if isinstance(tc, dict) and tc.get("type") in ("any", "tool"):
            raise ServingError.invalid_request(
                "tool_choice of type any/tool is not allowed on a compaction request",
                "tool_choice",
            )
        if body.get("stop_sequences"):
            raise ServingError.invalid_request(
                "stop_sequences are not allowed on a compaction request",
                "stop_sequences",
            )
    edit = compaction_edit(body)
    if edit:
        trigger = edit.get("trigger") or {}
        if trigger:
            if trigger.get("type", "input_tokens") != "input_tokens":
                raise ServingError.invalid_request(
                    "only input_tokens triggers are supported", "context_management"
                )
            try:
                value = int(trigger.get("value", DEFAULT_TRIGGER_TOKENS))
            except (TypeError, ValueError):
                raise ServingError.invalid_request("trigger.value must be an integer")
            if value < MIN_TRIGGER_TOKENS:
                raise ServingError.invalid_request(
                    f"trigger.value must be at least {MIN_TRIGGER_TOKENS}",
                    "context_management",
                )


def trigger_tokens(edit: Dict[str, Any]) -> int:
    trigger = edit.get("trigger") or {}
    try:
        return int(trigger.get("value", DEFAULT_TRIGGER_TOKENS))
    except (TypeError, ValueError):
        return DEFAULT_TRIGGER_TOKENS


# ---------------------------------------------------------------------------
# Incoming compaction blocks
# ---------------------------------------------------------------------------


def find_compaction_blocks(
    messages: List[Dict[str, Any]],
) -> List[Tuple[int, Dict[str, Any]]]:
    found = []
    for idx, msg in enumerate(messages):
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "compaction":
                    found.append((idx, block))
    return found


def validate_incoming_compaction(messages: List[Dict[str, Any]]) -> Optional[str]:
    """Check compaction blocks in the history; returns the summary text (or None)."""
    blocks = find_compaction_blocks(messages)
    if not blocks:
        return None
    if len(blocks) > 1:
        raise ServingError.invalid_request(
            "a request may carry only one compaction block (send the newest)",
            "messages",
        )
    idx, block = blocks[0]
    if idx != 0:
        raise ServingError(
            400,
            "compaction_block_misplaced",
            "summarized messages remain in front of the compaction block",
            param="messages",
        )
    content = block.get("content")
    if content is None:
        return None
    if not verify_signature("compaction", str(content), block.get("signature")):
        raise ServingError(
            400,
            "compaction_signature_invalid",
            "the compaction block's signature or content was changed after it was returned",
            param="messages",
        )
    return str(content)


def make_compaction_block(summary: str) -> Dict[str, Any]:
    return {
        "type": "compaction",
        "content": summary,
        "signature": sign_payload("compaction", summary),
    }


# ---------------------------------------------------------------------------
# Context editing (clear_tool_uses / clear_thinking), applied client-side
# ---------------------------------------------------------------------------


def _approx_tokens(obj: Any) -> int:
    try:
        return max(1, len(json.dumps(obj, ensure_ascii=False)) // 4)
    except (TypeError, ValueError):
        return 1


def apply_clearing_edits(
    messages: List[Dict[str, Any]], edits: List[Dict[str, Any]], input_tokens: int
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return (new_messages, applied_edits) per the context-editing contract."""
    applied: List[Dict[str, Any]] = []
    msgs = json.loads(json.dumps(messages))  # deep copy
    for edit in edits:
        etype = edit.get("type")
        trigger = (edit.get("trigger") or {}).get("value")
        if trigger is not None and input_tokens < int(trigger):
            continue
        if etype == CLEAR_THINKING:
            keep = edit.get("keep", {"type": "thinking_turns", "value": 1})
            if keep == "all":
                continue
            keep_turns = int((keep or {}).get("value", 1))
            cleared_turns = 0
            cleared_tokens = 0
            assistant_idx = [
                i
                for i, m in enumerate(msgs)
                if m.get("role") == "assistant" and isinstance(m.get("content"), list)
            ]
            to_clear = assistant_idx[: max(0, len(assistant_idx) - keep_turns)]
            for i in to_clear:
                blocks = msgs[i]["content"]
                thinking = [b for b in blocks if b.get("type") in ("thinking", "redacted_thinking")]
                if thinking:
                    cleared_turns += 1
                    cleared_tokens += sum(_approx_tokens(b) for b in thinking)
                    msgs[i]["content"] = [
                        b for b in blocks if b.get("type") not in ("thinking", "redacted_thinking")
                    ]
            applied.append(
                {
                    "type": CLEAR_THINKING,
                    "cleared_thinking_turns": cleared_turns,
                    "cleared_input_tokens": cleared_tokens,
                }
            )
        elif etype == CLEAR_TOOL_USES:
            keep_n = int(((edit.get("keep") or {}).get("value", 3)))
            clear_at_least = int(((edit.get("clear_at_least") or {}).get("value", 0)))
            exclude = set(edit.get("exclude_tools") or [])
            clear_inputs = bool(edit.get("clear_tool_inputs", False))
            tool_use_names: Dict[str, str] = {}
            for m in msgs:
                if m.get("role") == "assistant" and isinstance(m.get("content"), list):
                    for b in m["content"]:
                        if b.get("type") == "tool_use":
                            tool_use_names[b.get("id")] = b.get("name", "")
            results = []  # (msg_idx, block_idx)
            for i, m in enumerate(msgs):
                if m.get("role") == "user" and isinstance(m.get("content"), list):
                    for j, b in enumerate(m["content"]):
                        if b.get("type") == "tool_result":
                            name = tool_use_names.get(b.get("tool_use_id"), "")
                            if name not in exclude:
                                results.append((i, j))
            candidates = results[: max(0, len(results) - keep_n)]
            cleared = 0
            cleared_tokens = 0
            for i, j in candidates:
                block = msgs[i]["content"][j]
                if block.get("content") in (None, "", []) and not clear_inputs:
                    continue
                cleared_tokens += _approx_tokens(block.get("content"))
                block["content"] = [{"type": "text", "text": "[cleared]"}]
                if clear_inputs:
                    for m in msgs:
                        if m.get("role") == "assistant" and isinstance(m.get("content"), list):
                            for ab in m["content"]:
                                if ab.get("type") == "tool_use" and ab.get("id") == block.get(
                                    "tool_use_id"
                                ):
                                    cleared_tokens += _approx_tokens(ab.get("input"))
                                    ab["input"] = {}
                cleared += 1
                if clear_at_least and cleared_tokens >= clear_at_least:
                    break
            applied.append(
                {
                    "type": CLEAR_TOOL_USES,
                    "cleared_tool_uses": cleared,
                    "cleared_input_tokens": cleared_tokens,
                }
            )
    return msgs, applied


# ---------------------------------------------------------------------------
# Summarisation prompt
# ---------------------------------------------------------------------------


def render_conversation(messages: List[Dict[str, Any]], system: Any = None) -> str:
    """Flatten an Anthropic conversation into text for the summariser."""
    lines: List[str] = []
    if system:
        if isinstance(system, list):
            system = "\n".join(b.get("text", "") for b in system if isinstance(b, dict))
        lines.append(f"[system]\n{system}\n")
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content")
        if isinstance(content, str):
            lines.append(f"[{role}]\n{content}\n")
            continue
        parts: List[str] = []
        for block in content or []:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                parts.append(block.get("text", ""))
            elif btype == "tool_use":
                parts.append(
                    f"<tool_call name={block.get('name')} id={block.get('id')}>"
                    f"{json.dumps(block.get('input') or {}, ensure_ascii=False)}</tool_call>"
                )
            elif btype == "tool_result":
                inner = block.get("content")
                if isinstance(inner, list):
                    inner = "".join(
                        b.get("text", "")
                        for b in inner
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                parts.append(f"<tool_result id={block.get('tool_use_id')}>{inner}</tool_result>")
            elif btype == "compaction":
                parts.append(f"<earlier_summary>{block.get('content', '')}</earlier_summary>")
            elif btype == "image":
                parts.append("[image]")
            elif btype == "document":
                parts.append(f"[document {block.get('title') or ''}]")
        lines.append(f"[{role}]\n" + "\n".join(parts) + "\n")
    return "\n".join(lines)


def build_summary_request(
    body: Dict[str, Any], served_model: str, instructions: Optional[str]
) -> Dict[str, Any]:
    """OpenAI chat request that produces the summary with the same model."""
    model = settings.SERVING_COMPACTION_MODEL_OVERRIDE or served_model
    text = render_conversation(body.get("messages") or [], body.get("system"))
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": instructions or DEFAULT_INSTRUCTIONS},
            {
                "role": "user",
                "content": "Conversation to compact:\n\n" + text + "\n\nWrite the summary now.",
            },
        ],
        "max_tokens": max(
            256,
            min(
                settings.SERVING_COMPACTION_MAX_TOKENS,
                int(body.get("max_tokens") or 4096),
            ),
        ),
        "temperature": 0.2,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }


def summary_from_openai_response(data: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """(summary_text, stop_reason) from the summariser's chat completion."""
    from app.services.serving.thinking import split_think_tags

    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    finish = choice.get("finish_reason")
    if message.get("tool_calls"):
        return None, "tool_use"
    text = message.get("content") or ""
    _, visible = split_think_tags(text)
    visible = visible.strip()
    if finish == "length":
        return visible or None, "max_tokens"
    if not visible:
        return None, "end_turn"
    return visible, "compaction"
