"""GPU Deck service domain: topology.

Topology: the cluster -> node -> device tree for the UI.

Business logic lives in service.py; start-up / shutdown work in hooks.py
(auto-discovered, see app/core/lifecycle.py).
"""
