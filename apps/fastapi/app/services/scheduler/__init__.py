"""GPU Deck service domain: scheduler.

Placement scheduler: picks nodes and devices for deployment replicas.

Business logic lives in service.py; start-up / shutdown work in hooks.py
(auto-discovered, see app/core/lifecycle.py).
"""
