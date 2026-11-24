"""GPU Deck service domain: metrics.

Metrics: ingest, queries, live event stream and Prometheus exposition.

Business logic lives in service.py; start-up / shutdown work in hooks.py
(auto-discovered, see app/core/lifecycle.py).
"""
