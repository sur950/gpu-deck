"""GPU Deck service domain: nodes.

Nodes: node lifecycle (join, drain, remove), devices and node-side file commands.

Business logic lives in service.py; start-up / shutdown work in hooks.py
(auto-discovered, see app/core/lifecycle.py).
"""
