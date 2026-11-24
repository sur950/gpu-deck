"""Anthropic-format endpoints: /v1/messages (+ count_tokens, batches) and /v1/models."""

from app.services.serving.formats import explicit_prefix

PREFIX = explicit_prefix("anthropic")
FORMAT = "anthropic"
