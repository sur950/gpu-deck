"""GPU Deck service domain: metering.

Metering: usage events, hourly rollups and billable GPU-minute allocation.

Business logic lives in service.py; start-up / shutdown work in hooks.py
(auto-discovered, see app/core/lifecycle.py).
"""
