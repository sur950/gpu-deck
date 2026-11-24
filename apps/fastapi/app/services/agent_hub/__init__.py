"""GPU Deck service domain: agent_hub.

Agent hub: the control-plane side of the node-agent WebSocket protocol (connections,
ingest, command delivery, routing-table pushes).

Business logic lives in service.py; start-up / shutdown work in hooks.py
(auto-discovered, see app/core/lifecycle.py).
"""
