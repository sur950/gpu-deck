"""GPU Deck service domain: swap.

Swap manager: planned model moves between nodes and devices (drain, load, verify, roll back).

Business logic lives in service.py; start-up / shutdown work in hooks.py
(auto-discovered, see app/core/lifecycle.py).
"""
