"""
Deployment-readiness units: agent builds served out of the box (local dist dir,
flat release layout, release fetch + cache), the hub-only installer and its
pinned one-liner, engine artifact selection per platform and GPU, the pinned
engine seed, and enrolment-token validation.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import httpx
import pytest
from app.core.logger import get_logger

logger = get_logger(__name__)

VERSION = "1.0.0"
TARGET = "x86_64-unknown-linux-gnu"


@pytest.fixture
def dl(tmp_path, monkeypatch):
    from app.services.nodes import agent_downloads as mod

    monkeypatch.setattr(mod.download_settings, "AGENT_DIST_DIR", str(tmp_path / "dist"))
    monkeypatch.setattr(mod.download_settings, "AGENT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(mod.download_settings, "AGENT_RELEASE_URL", "https://rel.example/dl")
    monkeypatch.setattr(mod.download_settings, "AGENT_DOWNLOAD_BASE_URL", "")
    monkeypatch.setattr(mod.settings, "APP_VERSION", VERSION)
    monkeypatch.setattr(mod.settings, "AGENT_HUB_PUBLIC_URL", "https://hub.example:8443")
    monkeypatch.setattr(mod.settings, "AGENT_HUB_TLS_CERT_FILE", None)
    monkeypatch.setattr(mod.settings, "AGENT_HUB_TLS_KEY_FILE", None)
    mod._sums_cache.clear()
    mod._fetch_locks.clear()
    yield mod
    mod._transport = None
    mod._sums_cache.clear()


def _release(payload: bytes, calls: list, *, sums_sha: str | None = None):
    name = f"gpudeck-agent-{VERSION}-{TARGET}.tar.gz"
    sha = sums_sha or hashlib.sha256(payload).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path.endswith("/SHA256SUMS.txt"):
            return httpx.Response(200, text=f"{sha}  {name}\n{'b' * 64}  other.zip\n")
        if request.url.path.endswith(name):
            return httpx.Response(200, content=payload)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_flat_release_layout_in_dist_dir(dl, tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    name = f"gpudeck-agent-1.2.0-{TARGET}.tar.gz"
    (dist / name).write_bytes(b"agent tarball")
    (dist / "SHA256SUMS").write_text(f"{'c' * 64}  {name}\n")
    art = dl.resolve("latest", TARGET)
    assert (art.version, art.sha256, art.source) == ("1.2.0", "c" * 64, "local")
    assert [a.target for a in dl.list_artifacts()] == [TARGET]


async def test_release_fetch_verifies_and_caches(dl, tmp_path):
    calls: list = []
    dl._transport = _release(b"real agent", calls)
    art = await dl.resolve_or_fetch("latest", TARGET)
    assert art.version == VERSION and art.source == "cache"
    assert art.path.read_bytes() == b"real agent"
    assert art.sha256 == hashlib.sha256(b"real agent").hexdigest()
    assert any(c.endswith(f"/v{VERSION}/SHA256SUMS.txt") for c in calls)
    fetched = len(calls)
    again = await dl.resolve_or_fetch(VERSION, TARGET)  # served from the cache now
    assert again.path == art.path and len(calls) == fetched
    assert (tmp_path / "cache" / f"{art.filename}.sha256").read_text().startswith(art.sha256)


async def test_release_fetch_rejects_checksum_mismatch(dl, tmp_path):
    from app.core.exceptions import ExternalServiceError

    dl._transport = _release(b"tampered", [], sums_sha="d" * 64)
    with pytest.raises(ExternalServiceError):
        await dl.resolve_or_fetch(VERSION, TARGET)
    assert not list((tmp_path / "cache").glob("gpudeck-agent-*"))


async def test_release_fetch_disabled_and_unknown_targets(dl, monkeypatch):
    from app.core.exceptions import ResourceNotFoundError

    dl._transport = _release(b"x", [])
    with pytest.raises(ResourceNotFoundError):
        await dl.resolve_or_fetch(VERSION, "riscv64gc-unknown-linux-gnu")
    monkeypatch.setattr(dl.download_settings, "AGENT_RELEASE_URL", "")
    with pytest.raises(ResourceNotFoundError):
        await dl.resolve_or_fetch(VERSION, TARGET)


async def test_listing_includes_release_builds(dl):
    dl._transport = _release(b"x", [])
    items = await dl.list_with_release()
    assert [(a.target, a.source, a.size_bytes) for a in items] == [(TARGET, "release", 0)]
    assert items[0].as_dict()["download_path"] == f"/agent/downloads/{VERSION}/{TARGET}"
    assert await dl.latest_available_version() == VERSION


def test_install_command_talks_only_to_the_hub(dl, monkeypatch):
    pem = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"
    cmd = dl.install_command("gdj_" + "a" * 40, pem)
    pin = hashlib.sha256(pem.encode()).hexdigest()
    assert "https://hub.example:8443/agent/v1/ca.pem" in cmd and pin in cmd
    assert "--cacert" in cmd and "https://hub.example:8443/agent/v1/install.sh" in cmd
    assert "/api/v1" not in cmd
    assert subprocess.run(["sh", "-n", "-c", cmd]).returncode == 0
    # A hub with its own public certificate: plain curl with the system trust store.
    monkeypatch.setattr(dl.settings, "AGENT_HUB_TLS_CERT_FILE", "/certs/hub.crt")
    monkeypatch.setattr(dl.settings, "AGENT_HUB_TLS_KEY_FILE", "/certs/hub.key")
    plain = dl.install_command("gdj_x", pem)
    assert plain.startswith("curl -fsSL") and "--cacert" not in plain
    assert dl.install_script_url() == "https://hub.example:8443/agent/v1/install.sh"
    # Older configs pointed AGENT_DOWNLOAD_BASE_URL at the admin API: still served there.
    monkeypatch.setattr(dl.download_settings, "AGENT_DOWNLOAD_BASE_URL", "https://adm/api/v1")
    assert dl.install_script_url() == "https://adm/api/v1/agent/install.sh"
    assert "--cacert" not in dl.install_command("gdj_x", pem)


def test_install_script_downloads_from_hub_with_embedded_ca(dl):
    pem = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----"
    script = dl.install_script(
        "gdj_" + "ab" * 20,
        hub_url="https://hub.example:8443",
        ca_fingerprint="cd" * 32,
        ca_pem=pem,
    )
    assert "BASE_URL=https://hub.example:8443/agent/v1" in script
    assert '"$BASE_URL/downloads/$VERSION/$TARGET"' in script
    assert pem in script and '--cacert "$TMP/ca.pem"' in script
    # Engines (vLLM, torch inductor, triton) need a writable /tmp under ProtectSystem=strict,
    # one that outlives agent restarts; engines survive agent restarts (KillMode=process).
    assert "ProtectSystem=strict" in script and "BindPaths=$STATE/tmp:/tmp" in script
    assert "StateDirectory=gpudeck gpudeck/tmp" in script and "PrivateTmp" not in script
    assert "KillMode=process" in script and "<key>AbandonProcessGroup</key><true/>" in script
    assert "--retry 3" in script
    checked = subprocess.run(["sh", "-n"], input=script, text=True, capture_output=True)
    assert checked.returncode == 0, checked.stderr


# ------------------------------------------------------------- engine artifacts


def _arts():
    from app.services.engines.seed import LLAMACPP_ARTIFACTS

    return LLAMACPP_ARTIFACTS


@pytest.mark.parametrize(
    "os_name,arch,vendor,driver,runtime,want",
    [
        ("linux", "x86_64", "nvidia", "550.54.15", "12.4", "cuda-12.8-x64"),
        ("linux", "x86_64", "nvidia", "470.10", None, "vulkan-x64"),
        ("linux", "x86_64", "nvidia", None, None, "vulkan-x64"),
        ("linux", "x86_64", "amd", "6.10", "10.0.1", "rocm-10.0-x64"),
        ("linux", "x86_64", "amd", None, "6.4.0", "vulkan-x64"),
        ("linux", "x86_64", None, None, None, "ubuntu-x64"),
        ("linux", "aarch64", "nvidia", "570.10", None, "vulkan-arm64"),
        ("linux", "aarch64", "nvidia", "580.65", None, "cuda-13.5-arm64"),
        ("macos", "aarch64", "apple", None, None, "macos-arm64"),
    ],
)
def test_llama_cpp_build_selection(os_name, arch, vendor, driver, runtime, want):
    from app.services.engines import artifacts as arts

    picked = arts.select(_arts(), os_name, arch, vendor, driver, runtime)
    assert picked is not None and want in picked["url"], picked
    assert "min_driver_version" not in picked and "min_runtime_version" not in picked
    if "cuda" in want:
        assert picked["parts"][0]["url"].split("/")[-1].startswith("cudart-")
        assert picked["parts"][0]["dest"] == "llama-b12500"


def test_unpinned_entries_are_never_selected():
    from app.services.engines import artifacts as arts

    table = {
        "linux/x86_64": {
            "url": "https://x/y.tgz",
            "sha256": "0" * 64,
            "kind": "tarball",
        }
    }
    assert arts.select(table, "linux", "x86_64") is None
    assert arts.platforms(table) == []


def test_seed_pins_every_native_build_and_image():
    from app.services.engines import artifacts as arts
    from app.services.engines.seed import SEED_ENGINE_VERSIONS

    schema_dir = Path(__file__).resolve().parents[4] / "libs" / "contracts" / "agent-protocol"
    common = json.loads((schema_dir / "common.json").read_text())
    artifact_schema = {**common["$defs"]["Artifact"], "$defs": common["$defs"]}
    import jsonschema

    for spec in SEED_ENGINE_VERSIONS:
        if spec["engine"] == "custom":
            continue
        image = spec.get("image")
        if image:
            assert "@sha256:" in image and ":" in image.split("@")[0], image
        for key, entry in spec["artifacts"].items():
            assert arts.is_pinned(entry), (spec["engine"], key)
            assert entry["url"].startswith("https://"), entry["url"]
            jsonschema.validate(arts.for_agent(entry), artifact_schema)
        assert spec["artifacts"] or image, spec["engine"]


def test_select_artifact_uses_the_instance_devices():
    from types import SimpleNamespace

    from app.services.deployments import launch as L

    ev = SimpleNamespace(artifacts=_arts())
    nv = [SimpleNamespace(vendor="nvidia", driver_version="560.35", runtime_version="12.6")]
    assert "cuda" in L.select_artifact(ev, "linux", "x86_64", devices=nv)["url"]
    assert "ubuntu-x64" in L.select_artifact(ev, "linux", "x86_64")["url"]
    assert L.select_artifact(ev, "linux", "x86_64", installed=True) is None


# ------------------------------------------------------------- enrolment tokens


def test_join_token_create_rules():
    import uuid

    from app.schemas.fleet_schema import JoinTokenCreate
    from pydantic import ValidationError

    cid = uuid.uuid4()
    assert JoinTokenCreate(cluster_id=cid).max_uses == 1
    multi = JoinTokenCreate(cluster_id=cid, max_uses=50, ttl_minutes=60 * 24 * 30)
    assert multi.max_uses == 50
    with pytest.raises(ValidationError):
        JoinTokenCreate(cluster_id=cid, ttl_minutes=1441)  # one-time tokens live <= 1 day
    with pytest.raises(ValidationError):
        JoinTokenCreate(cluster_id=cid, max_uses=3, node_name_hint="gpu-1")
    with pytest.raises(ValidationError):
        JoinTokenCreate(cluster_id=cid, max_uses=0)
