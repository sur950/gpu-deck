"""
Reasoning / thinking handling across formats.

Engines expose model reasoning either as a separate ``reasoning_content``
delta (llama.cpp ``--reasoning-format``, vLLM reasoning parsers) or inline as
``<think>...</think>`` tags in ``content`` (Ollama's OpenAI endpoint, MLX,
llama.cpp without a reasoning format). ``ThinkTagSplitter`` turns the inline
form into (reasoning, text) pairs incrementally so translators can emit
Anthropic ``thinking`` blocks / Ollama ``thinking`` fields / Responses
``reasoning`` items regardless of the engine.

Request side: ``apply_thinking_request`` maps Anthropic ``thinking`` /
Responses ``reasoning.effort`` / Ollama ``think`` onto the OpenAI fields
engines understand (``reasoning_effort`` and ``chat_template_kwargs``).
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

THINK_OPEN = "<think>"
THINK_CLOSE = "</think>"

# Families whose chat templates emit <think> tags when reasoning is enabled.
THINK_TAG_FAMILIES = {
    "qwen3",
    "qwen",
    "deepseek",
    "deepseek_r1",
    "glm",
    "glm4",
    "smollm3",
    "exaone",
}


class ThinkTagSplitter:
    """Incremental splitter for ``<think>`` tags in a text delta stream.

    ``feed(text)`` returns ``(reasoning, text)`` deltas; ``flush()`` returns any
    buffered tail. Tag detection is tolerant of tags split across deltas.
    """

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.in_think = False
        self.started = False
        self.saw_tag = False
        self._pending = ""
        self._after_close = False  # drop the blank line(s) models emit after </think>

    def feed(self, text: str) -> Tuple[str, str]:
        if not self.enabled or not text:
            return ("", text or "")
        self._pending += text
        reasoning, visible = "", ""
        while self._pending:
            if self.in_think:
                idx = self._pending.find(THINK_CLOSE)
                if idx >= 0:
                    reasoning += self._pending[:idx]
                    self._pending = self._pending[idx + len(THINK_CLOSE) :]
                    self.in_think = False
                    self._after_close = True
                    continue
                # keep a possible partial close tag buffered
                keep = _partial_suffix(self._pending, THINK_CLOSE)
                reasoning += self._pending[: len(self._pending) - keep]
                self._pending = self._pending[len(self._pending) - keep :]
                break
            if self._after_close:
                # Newlines after </think> may arrive in a later delta; drop them all.
                stripped = self._pending.lstrip("\n")
                if not stripped:
                    self._pending = ""
                    break
                self._pending = stripped
                self._after_close = False
            idx = self._pending.find(THINK_OPEN)
            if idx >= 0:
                before = self._pending[:idx]
                if before.strip() or self.started:
                    visible += before
                self._pending = self._pending[idx + len(THINK_OPEN) :]
                self.in_think = True
                self.saw_tag = True
                if self._pending.startswith("\n"):
                    self._pending = self._pending[1:]
                continue
            if not self.started and not self._pending.strip():
                # leading whitespace before a possible <think>; hold it
                if len(self._pending) > 8:
                    visible += self._pending
                    self._pending = ""
                    self.started = True
                break
            keep = _partial_suffix(self._pending, THINK_OPEN) if not self.started else 0
            if not self.started and keep == len(self._pending):
                break
            out = self._pending[: len(self._pending) - keep]
            if out:
                visible += out
                self.started = True
            self._pending = self._pending[len(self._pending) - keep :]
            break
        return reasoning, visible

    def flush(self) -> Tuple[str, str]:
        tail, self._pending = self._pending, ""
        if self.in_think:
            return tail, ""
        return "", tail


def _partial_suffix(text: str, tag: str) -> int:
    """Length of the longest suffix of ``text`` that is a proper prefix of ``tag``."""
    max_len = min(len(text), len(tag) - 1)
    for size in range(max_len, 0, -1):
        if tag.startswith(text[-size:]):
            return size
    return 0


def split_think_tags(text: str) -> Tuple[str, str]:
    """Non-streaming helper: split a complete string into (reasoning, visible)."""
    splitter = ThinkTagSplitter()
    r, v = splitter.feed(text)
    r2, v2 = splitter.flush()
    return r + r2, v + v2


def family_uses_think_tags(family: Optional[str]) -> bool:
    return bool(family) and family.lower() in THINK_TAG_FAMILIES


def effort_from_budget(budget: Optional[int]) -> str:
    if budget is None:
        return "medium"
    if budget < 4096:
        return "low"
    if budget < 16384:
        return "medium"
    return "high"


def apply_thinking_request(
    openai_body: Dict[str, Any],
    *,
    enabled: Optional[bool],
    effort: Optional[str] = None,
) -> None:
    """Set OpenAI-side reasoning knobs on ``openai_body`` in place.

    ``enabled=None`` leaves the engine default untouched.
    """
    if enabled is None and effort is None:
        return
    kwargs = dict(openai_body.get("chat_template_kwargs") or {})
    if enabled is not None:
        kwargs["enable_thinking"] = bool(enabled)
    openai_body["chat_template_kwargs"] = kwargs
    if effort and enabled is not False:
        openai_body["reasoning_effort"] = effort
    elif enabled is False:
        openai_body.pop("reasoning_effort", None)


def thinking_signature(text: str) -> str:
    """Signature attached to thinking blocks (HMAC over the text; verified on replay)."""
    from app.services.serving.compaction import sign_payload

    return sign_payload("thinking", text)
