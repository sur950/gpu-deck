"""OpenAI-format endpoints (chat/completions, responses, embeddings, audio, images, ...)."""

from app.services.serving.formats import explicit_prefix

PREFIX = explicit_prefix("openai")
FORMAT = "openai"
