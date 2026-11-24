"""GPU Deck service domain: clusters.

Clusters: CRUD for every cluster type and its egress / price-plan settings.

Business logic lives in service.py; start-up / shutdown work in hooks.py
(auto-discovered, see app/core/lifecycle.py).
"""
