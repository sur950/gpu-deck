"""GPU Deck service domain: auth.

Authentication: admin login, sessions, CSRF tokens and lockout.

Business logic lives in service.py; start-up / shutdown work in hooks.py
(auto-discovered, see app/core/lifecycle.py).
"""
