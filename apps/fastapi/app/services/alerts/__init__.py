"""GPU Deck service domain: alerts.

Public entry point for every domain::

    from app.services.alerts import alerts
    await alerts.emit("node.offline", "warning", "Node offline", "...",
                      resource_type="node", resource_id=node_id, dedupe_key="...")

See ``service.py`` for the full signature and delivery semantics.
"""

from app.services.alerts.service import alerts, emit, emit_sync  # noqa: F401
