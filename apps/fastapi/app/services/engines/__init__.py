"""GPU Deck service domain: engines.

Inference engines: registry, versions, VRAM estimation and compatibility rules.

Business logic lives in service.py; start-up / shutdown work in hooks.py
(auto-discovered, see app/core/lifecycle.py).
"""
