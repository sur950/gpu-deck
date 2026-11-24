"""GPU Deck service domain: security.

Security: IP allow / block rules and related lifecycle hooks.

Business logic lives in service.py; start-up / shutdown work in hooks.py
(auto-discovered, see app/core/lifecycle.py).
"""
