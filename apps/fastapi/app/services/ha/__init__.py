"""GPU Deck service domain: ha.

High availability: lease-based leader election for the jobs process.

Business logic lives in service.py; start-up / shutdown work in hooks.py
(auto-discovered, see app/core/lifecycle.py).
"""
