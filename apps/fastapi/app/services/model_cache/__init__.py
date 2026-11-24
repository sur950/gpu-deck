"""GPU Deck service domain: model_cache.

Model cache: per-node cached model files, downloads and prefetching.

Business logic lives in service.py; start-up / shutdown work in hooks.py
(auto-discovered, see app/core/lifecycle.py).
"""
