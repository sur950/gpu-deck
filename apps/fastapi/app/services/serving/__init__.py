"""GPU Deck service domain: serving.

Serving API data plane: authentication, routing, proxying and request translation.

Business logic lives in service.py; start-up / shutdown work in hooks.py
(auto-discovered, see app/core/lifecycle.py).
"""
