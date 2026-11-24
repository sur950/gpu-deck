"""GPU Deck service domain: deployments.

Deployments: placement, launch, lifecycle reconciliation and the routing table.

Business logic lives in service.py; start-up / shutdown work in hooks.py
(auto-discovered, see app/core/lifecycle.py).
"""
