"""
Agent-protocol unit tests: inbound frames validate against the contract
schemas, outbound payloads the hub builds (command, config_push routing,
hello_ack) are schema-valid, and invalid command args are refused before
anything is persisted.
"""

import uuid
from datetime import datetime, timezone

import pytest
from app.core.exceptions import ValidationError
from app.services.agent_hub.config_builder import agent_settings, routing_entry
from app.services.agent_hub.protocol import (
    ProtocolError,
    build_envelope,
    dumps,
    loads,
    validate_envelope,
    validate_payload,
)
from app.services.agent_hub.service import build_command_payload

pytestmark = pytest.mark.unit

NODE = str(uuid.uuid4())


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def hello_payload():
    return {
        "agent_version": "1.0.0",
        "protocol_version": 1,
        "os": "macos",
        "arch": "aarch64",
        "hostname": "mac-1",
        "started_at": _now(),
        "capabilities": {
            "engines": [{"name": "llama_cpp", "versions": ["b8798"], "runtime": "native"}],
            "runtimes": ["native"],
            "metal": True,
        },
        "cache": {"dir": "/tmp/cache", "limit_bytes": 10, "used_bytes": 1},
        "proxy": {"port": 9443},
        "config_version": 0,
    }


def test_valid_hello_frame_passes():
    frame = loads(dumps(build_envelope("hello", hello_payload(), NODE)))
    validate_envelope(frame)


def test_unknown_top_level_field_is_rejected():
    frame = loads(dumps(build_envelope("hello", hello_payload(), NODE)))
    frame["extra"] = 1
    with pytest.raises(ProtocolError):
        validate_envelope(frame)


def test_payload_violation_is_rejected():
    payload = hello_payload()
    payload["os"] = "windows"
    with pytest.raises(ProtocolError):
        validate_envelope(loads(dumps(build_envelope("hello", payload, NODE))))


def test_metrics_batch_and_heartbeat_frames():
    inst, dev, dep = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    batch = {
        "interval_seconds": 2,
        "gpu": [{"ts": _now(), "device_id": dev, "utilization_pct": 40.0}],
        "engine": [
            {
                "ts": _now(),
                "instance_id": inst,
                "deployment_id": dep,
                "active_sessions": 1,
            }
        ],
        "sessions": [
            {
                "ts": _now(),
                "instance_id": inst,
                "session_key": "slot-0",
                "state": "generating",
            }
        ],
    }
    validate_envelope(loads(dumps(build_envelope("metrics_batch", batch, NODE))))
    beat = {"uptime_seconds": 5, "config_version": 1, "instances": []}
    validate_envelope(loads(dumps(build_envelope("heartbeat", beat, NODE))))


def test_command_result_requires_error_on_failure():
    cmd = str(uuid.uuid4())
    bad = {"command": "drain_instance", "status": "failed", "finished_at": _now()}
    with pytest.raises(ProtocolError):
        validate_envelope(loads(dumps(build_envelope("command_result", bad, NODE, cmd))))
    good = dict(bad, error={"code": "drain_timeout", "message": "sessions remained"})
    validate_envelope(loads(dumps(build_envelope("command_result", good, NODE, cmd))))


def test_build_command_payload_validates_args():
    payload = build_command_payload(
        "drain_instance", {"instance_id": str(uuid.uuid4()), "timeout_seconds": 30}, 60
    )
    assert payload["command"] == "drain_instance"
    validate_envelope(loads(dumps(build_envelope("command", payload, NODE))))
    with pytest.raises(ValidationError):
        build_command_payload("drain_instance", {"instance": "nope"}, 60)
    with pytest.raises(ValidationError):
        build_command_payload("format_disk", {}, 60)


def test_install_cert_payload_serialises_datetimes():
    payload = build_command_payload(
        "install_cert",
        {
            "certificate_pem": "x",
            "ca_bundle_pem": "y",
            "serial": "ab",
            "not_after": datetime.now(timezone.utc),
        },
        60,
    )
    assert payload["args"]["not_after"].endswith("Z")


def test_config_push_routing_entry_is_schema_valid():
    row = {
        "instance_id": uuid.uuid4(),
        "deployment_id": uuid.uuid4(),
        "port": 10240,
        "served_model_name": "qwen3-0.6b",
        "state": "ready",
        "traffic_weight": 100,
        "model_name": "qwen3",
        "model_type": "chat",
        "engine": "llama_cpp",
        "launch_flags": {"parallel": 4},
    }
    entry = routing_entry(row)
    assert entry["accepting"] is True and entry["max_concurrent"] == 4
    draining = routing_entry(row, node_accepting=False)
    assert draining["accepting"] is False
    no_meta = routing_entry({**row, "model_name": None, "engine": None})
    assert "engine" not in no_meta and "model_name" not in no_meta
    push = {
        "config_version": 3,
        "full": True,
        "routing": [entry, no_meta],
        "egress_allowlist": ["huggingface.co:443"],
        "crl": {"generated_at": _now(), "serials": [], "fingerprints_sha256": []},
        "settings": agent_settings(),
    }
    validate_payload("config_push", loads(dumps(push)))


def test_hello_ack_shape_is_valid():
    ack = {
        "accepted": True,
        "reason": None,
        "server_time": datetime.now(timezone.utc),
        "node_id": NODE,
        "node_name": "mac-1",
        "cluster_id": None,
        "intervals": {
            "heartbeat_seconds": 10,
            "metrics_seconds": 2,
            "inventory_seconds": 60,
        },
        "config_version": 1,
        "drain": False,
    }
    validate_envelope(loads(dumps(build_envelope("hello_ack", ack, NODE, str(uuid.uuid4())))))
