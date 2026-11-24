"""Ollama-format endpoints (/api/chat, /api/generate, /api/embed, /api/tags, /api/show, /api/ps)."""

from app.services.serving.formats import explicit_prefix

PREFIX = explicit_prefix("ollama")
FORMAT = "ollama"
