"""GPU Deck service domain: api_keys.

API keys: creation, rotation and the serving-side verification of ``gd-`` keys.

Business logic lives in service.py; start-up / shutdown work in hooks.py
(auto-discovered, see app/core/lifecycle.py).
"""
