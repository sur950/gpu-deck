"""GPU Deck service domain: traces — OpenTelemetry span index for the UI.

Span attribute conventions every process may set so the index can filter:
``gpudeck.request_id``, ``gpudeck.api_key_id``, ``gpudeck.model_id``,
``gpudeck.deployment_id``, ``gpudeck.instance_id``, ``gpudeck.node_id``,
``gpudeck.format`` (openai|anthropic|ollama), ``gpudeck.endpoint``.
"""
