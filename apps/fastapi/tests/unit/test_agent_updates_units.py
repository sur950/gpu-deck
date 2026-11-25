"""
Agent self-update (agent protocol §14), units only: release manifest signatures and shape,
local (air-gapped) releases and the target version, binary extraction from a release archive
checked against the signed manifest, maintenance windows, the cluster policy, the update status
of a node (helper / self / managed / manual / unsupported) and the protocol frames of both
agent generations (an agent that predates updates must keep validating).
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import tarfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.core.settings import settings
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

TARGET = "x86_64-unknown-linux-gnu"


def _keypair():
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return key, base64.b64encode(public).decode()


def _manifest(version: str, binary: bytes, archive: bytes = b"") -> bytes:
    doc = {
        "schema": 1,
        "product": "gpudeck-agent",
        "version": version,
        "protocol_version": 1,
        "created_at": "2026-11-24T10:00:00Z",
        "artifacts": [
            {
                "target": TARGET,
                "file": f"gpudeck-agent-{version}-{TARGET}.tar.gz",
                "size_bytes": len(archive) or None,
                "sha256": hashlib.sha256(archive).hexdigest() if archive else None,
                "binary_sha256": hashlib.sha256(binary).hexdigest(),
                "binary_size_bytes": len(binary),
            }
        ],
    }
    return (json.dumps(doc, indent=2) + "\n").encode()


def _tarball(binary: bytes) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("./gpudeck-agent")
        info.size = len(binary)
        info.mode = 0o755
        tar.addfile(info, io.BytesIO(binary))
        readme = b"packaging"
        info = tarfile.TarInfo("./packaging/README")
        info.size = len(readme)
        tar.addfile(info, io.BytesIO(readme))
    return buf.getvalue()


@pytest.fixture
def release_env(tmp_path, monkeypatch):
    """AGENT_DIST_DIR / AGENT_CACHE_DIR in tmp, a throwaway release key, no release URL."""
    from app.services.agent_updates import releases

    key, public = _keypair()
    dist = tmp_path / "dist"
    cache = tmp_path / "cache"
    dist.mkdir()
    cache.mkdir()
    monkeypatch.setattr(settings, "AGENT_DIST_DIR", str(dist))
    monkeypatch.setattr(settings, "AGENT_CACHE_DIR", str(cache))
    monkeypatch.setattr(settings, "AGENT_RELEASE_URL", "")
    monkeypatch.setattr(settings, "AGENT_RELEASE_PUBLIC_KEYS", public)
    monkeypatch.setattr(settings, "AGENT_UPDATE_TARGET_VERSION", "")
    releases.reset_caches()

    def publish(version: str, binary: bytes, flat: bool = True, signer=None) -> Path:
        archive = _tarball(binary)
        raw = _manifest(version, binary, archive)
        sig = base64.b64encode((signer or key).sign(raw)).decode()
        folder = dist if flat else dist / version
        folder.mkdir(exist_ok=True)
        (folder / f"gpudeck-agent-{version}-manifest.json").write_bytes(raw)
        (folder / f"gpudeck-agent-{version}-manifest.json.sig").write_text(sig + "\n")
        (dist / f"gpudeck-agent-{version}-{TARGET}.tar.gz").write_bytes(archive)
        return folder

    yield SimpleNamespace(key=key, public=public, dist=dist, cache=cache, publish=publish)
    releases.reset_caches()


def test_release_public_keys_setting_keeps_valid_keys_only(monkeypatch):
    _, a = _keypair()
    _, b = _keypair()
    monkeypatch.setattr(
        settings,
        "AGENT_RELEASE_PUBLIC_KEYS",
        f" {a} ,not-base64!,{base64.b64encode(b'x').decode()},{b},{a}",
    )
    assert settings.get_release_public_keys() == [a, b]


def test_manifest_signature_and_shape():
    from app.services.agent_updates import releases

    key, public = _keypair()
    _, other = _keypair()
    raw = _manifest("1.2.0", b"binary")
    sig = base64.b64encode(key.sign(raw)).decode()
    assert releases.verify_signature(raw, sig, [public])
    assert releases.verify_signature(raw, sig + "\n", [other, public])
    assert not releases.verify_signature(raw, sig, [other])
    assert not releases.verify_signature(raw + b" ", sig, [public])
    assert not releases.verify_signature(raw, "not base64", [public])
    doc = releases.parse_manifest(raw)
    assert doc["artifacts"][0]["binary_size_bytes"] == 6
    for bad in (
        {"schema": 2},
        {"product": "other"},
        {"version": "latest"},
        {"artifacts": []},
        {"artifacts": [{"target": "../x", "binary_sha256": "a" * 64, "binary_size_bytes": 1}]},
        {"artifacts": [{"target": TARGET, "binary_sha256": "zz", "binary_size_bytes": 1}]},
    ):
        broken = {**json.loads(raw), **bad}
        with pytest.raises(ValueError):
            releases.parse_manifest(json.dumps(broken).encode())
    assert releases.compare("1.10.0", "1.9.9") == 1
    assert releases.compare("1.0.0-rc.1", "1.0.0") == -1
    assert releases.compare(None, "0.0.1") == -1


async def test_local_releases_target_and_binary(release_env, monkeypatch):
    from app.services.agent_updates import releases

    release_env.publish("1.1.0", b"agent-1.1.0", flat=True)
    release_env.publish("1.2.0", b"agent-1.2.0", flat=False)
    rogue, _ = _keypair()
    release_env.publish("9.0.0", b"agent-9.0.0", flat=True, signer=rogue)  # not our key
    assert releases.local_versions() == ["9.0.0", "1.2.0", "1.1.0"]
    assert releases.load_local("9.0.0") is None, "a manifest signed by another key is ignored"
    assert releases.load_local("1.2.0").source == "local"
    target, reason = await releases.target_version(force=True)
    assert (target, reason) == ("1.2.0", None)
    # A pinned target needs a signed manifest.
    monkeypatch.setattr(settings, "AGENT_UPDATE_TARGET_VERSION", "1.1.0")
    assert await releases.target_version(force=True) == ("1.1.0", None)
    monkeypatch.setattr(settings, "AGENT_UPDATE_TARGET_VERSION", "9.0.0")
    target, reason = await releases.target_version(force=True)
    assert target is None and "9.0.0" in reason
    monkeypatch.setattr(settings, "AGENT_UPDATE_TARGET_VERSION", "")
    # The wire form the agent verifies.
    m = await releases.get_manifest("1.2.0")
    wire = m.wire()
    assert base64.b64decode(wire["manifest"]) == m.raw and wire["algorithm"] == "ed25519"
    # The binary is extracted from the archive once, checked against the manifest.
    path = await releases.binary_for("1.2.0", TARGET)
    assert path.read_bytes() == b"agent-1.2.0"
    assert path.is_relative_to(release_env.cache)
    assert (await releases.binary_for("1.2.0", TARGET)) == path
    with pytest.raises(Exception):
        await releases.binary_for("1.2.0", "aarch64-apple-darwin")
    # A tampered archive (binary does not match the signed sha256) is refused.
    archive = release_env.dist / f"gpudeck-agent-1.1.0-{TARGET}.tar.gz"
    archive.write_bytes(_tarball(b"evil"))
    from app.services.nodes import agent_downloads as dl

    dl._sha_cache.clear()
    with pytest.raises(Exception):
        await releases.binary_for("1.1.0", TARGET)
    # No release key: updates are off with a reason.
    monkeypatch.setattr(settings, "AGENT_RELEASE_PUBLIC_KEYS", "")
    target, reason = await releases.target_version(force=True)
    assert target is None and "AGENT_RELEASE_PUBLIC_KEYS" in reason


def test_maintenance_windows():
    from app.schemas.agent_update_schema import AgentUpdatePolicy, AgentUpdateWindow
    from app.services.agent_updates.policy import (
        auto_allowed,
        in_window,
        next_window_start,
    )

    w = AgentUpdateWindow(days=["sat"], start="22:00", end="02:00", timezone="UTC")
    sat_23 = datetime(2026, 11, 28, 23, 0, tzinfo=timezone.utc)  # a Saturday
    sun_01 = datetime(2026, 11, 29, 1, 0, tzinfo=timezone.utc)
    sun_03 = datetime(2026, 11, 29, 3, 0, tzinfo=timezone.utc)
    sat_21 = datetime(2026, 11, 28, 21, 0, tzinfo=timezone.utc)
    assert in_window(w, sat_23) and in_window(w, sun_01)
    assert not in_window(w, sun_03) and not in_window(w, sat_21)
    assert next_window_start(w, sat_21) == datetime(2026, 11, 28, 22, 0, tzinfo=timezone.utc)
    assert next_window_start(w, sun_01) == sun_01
    # Time zones: 02:00-05:00 in UTC+9 is 17:00-20:00 UTC the day before.
    tokyo = AgentUpdateWindow(days=["sun"], start="02:00", end="05:00", timezone="Asia/Tokyo")
    assert in_window(tokyo, datetime(2026, 11, 28, 18, 0, tzinfo=timezone.utc))
    assert not in_window(tokyo, datetime(2026, 11, 29, 3, 0, tzinfo=timezone.utc))
    policy = AgentUpdatePolicy(mode="auto", window=w)
    assert auto_allowed(policy, sat_23) and not auto_allowed(policy, sun_03)
    assert not auto_allowed(AgentUpdatePolicy(), sat_23)
    for bad in (
        {"mode": "auto"},
        {"mode": "auto", "window": {"days": [], "start": "01:00", "end": "02:00"}},
        {"mode": "auto", "window": {"days": ["mon"], "start": "25:00", "end": "02:00"}},
        {"mode": "auto", "window": {"days": ["mon"], "start": "01:00", "end": "01:00"}},
        {
            "mode": "auto",
            "window": {
                "days": ["mon"],
                "start": "01:00",
                "end": "02:00",
                "timezone": "Mars/Base",
            },
        },
        {"mode": "sometimes"},
    ):
        with pytest.raises(ValueError):
            AgentUpdatePolicy.model_validate(bad)


def test_cluster_config_validation_and_stored_policy():
    from app.schemas.agent_update_schema import policy_from_config
    from app.schemas.fleet_schema import ClusterUpdate

    cfg = ClusterUpdate(
        config={
            "agent_updates": {
                "mode": "auto",
                "window": {"days": ["sun", "sat"], "start": "02:00", "end": "05:00"},
            },
            "node_security": {"ssh_default_allowed_cidrs": ["10.0.0.0/8"]},
        }
    ).config
    assert cfg["agent_updates"]["window"]["days"] == ["sat", "sun"]
    assert cfg["agent_updates"]["window"]["timezone"] == "UTC"
    assert policy_from_config(cfg).mode == "auto"
    assert "agent_updates" not in ClusterUpdate(config={"agent_updates": None}).config
    assert policy_from_config({"agent_updates": {"mode": "bogus"}}).mode == "manual"
    with pytest.raises(ValueError):
        ClusterUpdate(config={"agent_updates": {"mode": "auto"}})


def _node(**kw):
    base = dict(
        id=uuid.uuid4(),
        name="gpu-1",
        status="online",
        agent_version="1.0.0",
        capabilities={"commands": ["load_model", "stage_update", "apply_update"]},
        metadata_={
            "agent_update": {
                "mode": "helper",
                "target": TARGET,
                "state": "idle",
                "release_keys": 1,
            }
        },
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_node_update_status():
    from app.services.agent_updates import status as st

    info = st.node_info(_node(), "1.1.0")
    assert info["status"] == "update_available" and info["can_update"] and info["mode"] == "helper"
    staged = _node(
        metadata_={
            "agent_update": {
                "mode": "self",
                "staged_version": "1.1.0",
                "target": TARGET,
            }
        }
    )
    assert st.node_info(staged, "1.1.0")["status"] == "staged"
    assert st.node_info(_node(agent_version="1.1.0"), "1.1.0")["status"] == "up_to_date"
    offline = st.node_info(_node(status="offline"), "1.1.0")
    assert offline["status"] == "update_available" and not offline["can_update"]
    assert st.count_key(offline, "offline") == "offline"
    # Kubernetes DaemonSet: managed by Helm, whatever the agent says.
    k8s = _node(
        capabilities={
            "commands": ["stage_update", "apply_update"],
            "kubernetes": {"pod": "p"},
        }
    )
    info = st.node_info(k8s, "1.1.0")
    assert (
        info["status"] == "managed"
        and info["reason"] == "Managed by Helm"
        and not info["can_update"]
    )
    # N-1: an agent that predates self-update (no update commands, no agent_update).
    old = _node(capabilities={"commands": ["load_model"]}, metadata_={})
    info = st.node_info(old, "1.1.0")
    assert info["status"] == "manual" and info["mode"] == "manual" and info["instructions"]
    assert not info["can_update"]
    assert st.node_info(_node(capabilities={}, metadata_={}), "1.1.0")["mode"] == "manual"
    # The agent can update but this machine cannot (no helper, binary root-owned).
    stuck = _node(metadata_={"agent_update": {"mode": "unsupported", "reason": "no helper"}})
    info = st.node_info(stuck, "1.1.0")
    assert info["status"] == "unsupported" and info["reason"] == "no helper"
    # Updating (marker) and disabled (no target).
    busy = _node(
        metadata_={
            "agent_update": {"mode": "helper"},
            "agent_update_active": {"run_id": "x"},
        }
    )
    assert st.node_info(busy, "1.1.0")["status"] == "updating"
    info = st.node_info(_node(), None, "agent updates are off")
    assert info["status"] == "disabled" and info["reason"] == "agent updates are off"


def test_rollout_skip_reasons():
    from app.services.agent_updates.orchestrator import skip_reason

    assert skip_reason(_node(), "1.1.0") is None
    assert skip_reason(None, "1.1.0") == "removed"
    assert skip_reason(_node(status="offline"), "1.1.0") == "node is offline"
    assert skip_reason(_node(agent_version="1.1.0"), "1.1.0") == "already on 1.1.0"
    assert "manually" in skip_reason(_node(capabilities={}, metadata_={}), "1.1.0")
    assert (
        skip_reason(_node(capabilities={"kubernetes": {"pod": "x"}}), "1.1.0") == "managed by Helm"
    )


def _hello(**extra):
    return {
        "agent_version": "1.0.0",
        "protocol_version": 1,
        "os": "linux",
        "arch": "x86_64",
        "hostname": "gpu-1",
        "started_at": "2026-11-24T10:00:00.000Z",
        "capabilities": {"engines": [], "runtimes": ["native"]},
        "cache": {"dir": "/var/lib/gpudeck/cache", "limit_bytes": 1, "used_bytes": 0},
        "proxy": {"port": 9443},
        **extra,
    }


def test_protocol_frames_of_both_agent_generations():
    from app.services.agent_hub.protocol import (
        HUB_FEATURES,
        ProtocolError,
        validate_payload,
    )
    from app.services.agent_hub.service import build_command_payload

    assert "agent_updates" in HUB_FEATURES
    # N-1 agent: no agent_update, no update commands.
    validate_payload("hello", _hello())
    # N agent.
    status = {
        "mode": "helper",
        "target": TARGET,
        "state": "staged",
        "staged_version": "1.1.0",
        "staged_sha256": "a" * 64,
        "staged_at": "2026-11-24T10:00:00Z",
        "release_keys": 1,
    }
    validate_payload("hello", _hello(agent_update=status))
    validate_payload(
        "inventory",
        {
            "devices": [],
            "disks": [],
            "cache": {"dir": "/x", "limit_bytes": 0, "used_bytes": 0, "items": []},
            "instances": [],
            "agent_update": status,
        },
    )
    with pytest.raises(ProtocolError):
        validate_payload("hello", _hello(agent_update={**status, "mode": "sometimes"}))
    validate_payload(
        "event",
        {
            "kind": "agent_update",
            "severity": "info",
            "message": "staged",
            "details": {"phase": "staged", "version": "1.1.0"},
        },
    )
    key, _ = _keypair()
    raw = _manifest("1.1.0", b"x")
    stage = build_command_payload(
        "stage_update",
        {
            "version": "1.1.0",
            "target": TARGET,
            "manifest": {
                "manifest": base64.b64encode(raw).decode(),
                "signature": base64.b64encode(key.sign(raw)).decode(),
                "algorithm": "ed25519",
            },
            "download_token": "gdu_" + "a" * 48,
        },
        1800,
    )
    assert stage["command"] == "stage_update"
    build_command_payload("apply_update", {"version": "1.1.0", "health_timeout_seconds": 120}, 900)
    from app.core.exceptions import ValidationError

    with pytest.raises(ValidationError):
        build_command_payload(
            "apply_update", {"version": "1.1.0", "health_timeout_seconds": 5}, 900
        )
    with pytest.raises(ValidationError):
        build_command_payload("stage_update", {"version": "1.1.0", "target": TARGET}, 900)


async def test_download_token_bound_to_version_and_target(monkeypatch):
    from app.services.agent_updates import tokens

    store = {}

    class FakeRedis:
        async def set(self, key, value, ex=None):
            store[key] = value

        async def get(self, key):
            return store.get(key)

    async def fake_get_redis():
        return FakeRedis()

    monkeypatch.setattr(tokens, "get_redis", fake_get_redis)
    node = uuid.uuid4()
    tok = await tokens.issue(node, "1.1.0", TARGET)
    assert tok.startswith("gdu_")
    assert all(not k.endswith(tok) for k in store), "only the hash is stored"
    assert all(k.startswith(settings.REDIS_KEY_PREFIX) for k in store)
    assert await tokens.check(tok, "1.1.0", TARGET) == node
    assert await tokens.check(tok, "1.2.0", TARGET) is None
    assert await tokens.check(tok, "1.1.0", "aarch64-apple-darwin") is None
    assert await tokens.check("gdu_wrong", "1.1.0", TARGET) is None
    assert await tokens.check(None, "1.1.0", TARGET) is None


def test_release_tooling_output_is_what_the_control_plane_verifies(tmp_path):
    """scripts/agent_release_manifest.py (release workflow) <-> releases.py (control plane)."""
    import os
    import subprocess
    import sys

    from app.services.agent_updates import releases

    script = Path(__file__).resolve().parents[4] / "scripts" / "agent_release_manifest.py"
    binary = b"\x7fELF fake agent"
    (tmp_path / f"gpudeck-agent-2.0.0-{TARGET}.tar.gz").write_bytes(_tarball(binary))
    (tmp_path / "gpudeck-agent-1.9.0-aarch64-apple-darwin.tar.gz").write_bytes(_tarball(b"old"))
    run = lambda *a, **kw: subprocess.run(  # noqa: E731
        [sys.executable, str(script), *a], capture_output=True, text=True, **kw
    )
    keyfile = tmp_path / "release.key"
    out = run("keygen", "--out", str(keyfile))
    assert out.returncode == 0, out.stderr
    public = out.stdout.strip().splitlines()[-1]
    assert oct(keyfile.stat().st_mode & 0o777) == "0o600"
    assert run("keygen", "--out", str(keyfile)).returncode != 0, "never overwrites a key"
    assert run("manifest", str(tmp_path), "2.0.0").returncode == 0
    manifest = tmp_path / "gpudeck-agent-2.0.0-manifest.json"
    env = {**os.environ, "AGENT_RELEASE_SIGNING_KEY": keyfile.read_text()}
    assert run("sign", str(manifest), "--expect-public-key", "AAAA", env=env).returncode != 0
    signed = run("sign", str(manifest), "--expect-public-key", public, env=env)
    assert signed.returncode == 0, signed.stderr
    assert run("verify", str(manifest), "--public-key", public).returncode == 0
    raw = manifest.read_bytes()
    sig = (tmp_path / f"{manifest.name}.sig").read_text()
    assert releases.verify_signature(raw, sig, [public])
    doc = releases.parse_manifest(raw)
    assert [a["target"] for a in doc["artifacts"]] == [TARGET], "only this version's archives"
    assert doc["artifacts"][0]["binary_sha256"] == hashlib.sha256(binary).hexdigest()
    assert doc["artifacts"][0]["binary_size_bytes"] == len(binary)


def test_without_a_signed_release_nodes_are_updated_manually():
    """No signing key / unsigned release: behind the control plane = update manually, never auto."""
    from app.services.agent_updates import status as st

    info = st.node_info(
        _node(), None, "no signed agent release is available", release_version="1.1.0"
    )
    assert info["status"] == "manual" and not info["can_update"]
    assert "re-running its install command" in info["instructions"]
    assert info["target_version"] == "1.1.0"
    same = st.node_info(_node(agent_version="1.1.0"), None, "off", release_version="1.1.0")
    assert same["status"] == "disabled"
