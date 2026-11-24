"""
Unit checks for fixes found while running the multi-node Linux container fleet.

Covers the node proxy address choice when an agent reaches the hub through a
port forward (the hub sees 127.0.0.1, which no other process can use).
"""

from __future__ import annotations

from app.services.agent_hub.connection import _advertised_proxy_address


def test_declared_proxy_address_wins():
    assert _advertised_proxy_address("10.1.2.3", "127.0.0.1", ["172.30.50.13"]) == "10.1.2.3"


def test_loopback_source_is_replaced_by_a_reported_interface_address():
    assert _advertised_proxy_address(None, "127.0.0.1", ["172.30.50.13"]) == "172.30.50.13"
    assert _advertised_proxy_address(None, "::1", ["fd00::5"]) == "fd00::5"


def test_routable_source_and_missing_data_are_kept():
    assert _advertised_proxy_address(None, "192.168.1.20", ["10.0.0.9"]) == "192.168.1.20"
    assert _advertised_proxy_address(None, "127.0.0.1", []) == "127.0.0.1"
    assert _advertised_proxy_address(None, "node-1.lan", ["10.0.0.9"]) == "node-1.lan"
    assert _advertised_proxy_address(None, None, ["10.0.0.9"]) is None
