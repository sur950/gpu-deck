"""
Unit tests (no services): stable device ids, scheduler device pinning,
node certificate SANs + proxy TLS identity check (real TLS handshake), SSH key
parsing, GPU instance command args against command.json, cross-node PP ranks,
agent downloads + install script, node-scan candidate mapping, on-demand load
messages, llama.cpp seed facts and the /live/ws route shape.
"""

from __future__ import annotations

import asyncio
import base64
import json
import shutil
import ssl
import subprocess
import uuid
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------- device ids


def test_stable_device_id_matches_agent_uuid5_scheme():
    from app.db.postgres.repositories.inventory_repository import stable_device_id

    node = uuid.UUID("6f1c2d3e-1111-4222-8333-944455556666")
    # Rust: Uuid::new_v5(&node_id, b"apple:0") — namespace = node id, name = "<vendor>:<index>"
    assert stable_device_id(node, "apple", 0) == uuid.uuid5(node, "apple:0")
    assert stable_device_id(node, "nvidia", 1) != stable_device_id(node, "nvidia", 0)
    assert stable_device_id(str(node), "nvidia", "1") == uuid.uuid5(node, "nvidia:1")


# ----------------------------------------------------------------- scheduler


def _node(name, devices, labels=None):
    from app.services.scheduler.core import DeviceView, NodeView

    nid = uuid.uuid4()
    views = [
        DeviceView(
            id=uuid.uuid4(),
            node_id=nid,
            index=i,
            vendor="nvidia",
            vram_total_bytes=vram,
            name="GPU",
            health="healthy",
        )
        for i, vram in enumerate(devices)
    ]
    return NodeView(
        id=nid,
        name=name,
        status="online",
        cluster_id=None,
        labels=labels or {},
        devices=views,
    )


def test_scheduler_honours_node_selector_device_ids():
    from app.services.scheduler.core import PlacementRequest, place

    a = _node("a", [80 << 30, 80 << 30])
    b = _node("b", [80 << 30])
    target = a.devices[1]
    req = PlacementRequest(
        need_bytes_per_device=10 << 30, node_selector={"device_ids": [str(target.id)]}
    )
    plan = place([a, b], req)
    assert plan.feasible, plan.reason
    assert plan.assignments[0].device_ids == [target.id]
    # node_ids + device_ids together; device_ids is not a label
    req = PlacementRequest(
        need_bytes_per_device=10 << 30,
        node_selector={"node_ids": [str(b.id)], "device_ids": [str(b.devices[0].id)]},
    )
    assert place([a, b], req).assignments[0].node_id == b.id
    # a device id that is on no eligible node -> infeasible with a clear reason
    req = PlacementRequest(
        need_bytes_per_device=1, node_selector={"device_ids": [str(uuid.uuid4())]}
    )
    plan = place([a, b], req)
    assert not plan.feasible
    assert "node_selector.device_ids" in plan.reason


def test_full_gpu_request_needs_an_empty_device():
    from app.services.scheduler.core import PlacementRequest, place

    a = _node("a", [24 << 30, 24 << 30])
    a.devices[0].reserved_bytes = 1 << 30  # something already runs on GPU 0
    req = PlacementRequest(need_bytes_per_device=0, min_device_fraction=1.0, headroom_pct=0)
    plan = place([a], req)
    assert plan.feasible and plan.assignments[0].device_ids == [a.devices[1].id]
    a.devices[1].compute_reserved_pct = 0
    a.devices[1].reserved_bytes = 1
    assert not place([a], req).feasible


def test_ssh_placement_uses_real_vram():
    """FULL-TEST #13: whole GPUs and slices for SSH instances see memory used outside GPU Deck
    (an idle GPU's driver footprint is tolerated)."""
    from app.services.gpu_instances.service import WHOLE_GPU_IDLE_SLACK_BYTES
    from app.services.scheduler.core import PlacementRequest, place

    whole = PlacementRequest(
        need_bytes_per_device=0,
        min_device_fraction=1.0,
        headroom_pct=0,
        fraction_slack_bytes=WHOLE_GPU_IDLE_SLACK_BYTES,
    )
    a = _node("a", [80 << 30, 80 << 30])
    a.devices[0].observed_used_bytes = 60 << 30  # someone's vLLM, outside GPU Deck
    a.devices[0].external_used_bytes = 60 << 30
    a.devices[1].observed_used_bytes = 614 << 20  # idle driver reserve
    a.devices[1].external_used_bytes = 0
    plan = place([a], whole)
    assert plan.feasible and plan.assignments[0].device_ids == [a.devices[1].id]
    a.devices[1].observed_used_bytes = 4 << 30
    a.devices[1].external_used_bytes = 4 << 30
    assert not place([a], whole).feasible

    sliced = PlacementRequest(
        need_bytes_per_device=0,
        headroom_pct=0,
        slice_vram_bytes=24 << 30,
        slice_compute_pct=25,
    )
    # 24 GiB does not fit next to the 60 GiB server on GPU 0 (20 GiB left), fits on GPU 1.
    assert place([a], sliced).assignments[0].device_ids == [a.devices[1].id]
    a.devices[1].observed_used_bytes = 70 << 30
    a.devices[1].external_used_bytes = 70 << 30
    assert not place([a], sliced).feasible


async def test_ssh_placement_snapshot_includes_observed_usage(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from app.services.gpu_instances.service import GpuInstanceService

    svc = GpuInstanceService.__new__(GpuInstanceService)
    svc.scheduler = MagicMock()
    svc.scheduler.snapshot = AsyncMock(return_value=[])
    row = MagicMock(metadata_={}, cluster_id=None)
    out, reason = await svc._place(row)
    assert out is None and reason == "no nodes"
    kwargs = svc.scheduler.snapshot.call_args.kwargs
    assert kwargs.get("use_observed", True) is True


# --------------------------------------------------------------------- PKI


@pytest.fixture(scope="module")
def ca_pair():
    from app.services.pki import ca as cax

    return cax.generate_ca("ecdsa_p256", validity_days=30)


def _csr_with_sans(key, sans):
    builder = x509.CertificateSigningRequestBuilder().subject_name(
        x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "pending")])
    )
    builder = builder.add_extension(x509.SubjectAlternativeName(sans), critical=False)
    return builder.sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM).decode()


def test_node_cert_copies_csr_dns_ip_sans(ca_pair):
    import ipaddress

    from app.services.pki import ca as cax

    key, ca_cert = ca_pair
    node_id = uuid.uuid4()
    agent_key = cax.generate_private_key("ecdsa_p256")
    csr = _csr_with_sans(
        agent_key,
        [
            x509.DNSName("gpu-1.lab"),
            x509.DNSName("*.evil"),
            x509.IPAddress(ipaddress.ip_address("10.0.0.6")),
            x509.UniformResourceIdentifier("gpudeck://node/other"),
        ],
    )
    cert = cax.sign_node_csr(key, ca_cert, csr, node_id, 30)
    sans = cax.cert_sans(cert)
    assert f"gpudeck://node/{node_id}" in sans
    assert "gpu-1.lab" in sans and "10.0.0.6" in sans
    assert "*.evil" not in sans and "gpudeck://node/other" not in sans
    eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert ExtendedKeyUsageOID.SERVER_AUTH in eku and ExtendedKeyUsageOID.CLIENT_AUTH in eku


def _node_cert(ca_pair, node_id):
    from app.services.pki import ca as cax

    key, ca_cert = ca_pair
    agent_key = cax.generate_private_key("ecdsa_p256")
    cert = cax.sign_node_csr(key, ca_cert, cax.build_csr(agent_key, "pending"), node_id, 30)
    return agent_key, cert


def test_verify_node_peer_by_cn_and_routing_table(ca_pair):
    from app.services.pki import proxy_tls

    node_id = uuid.uuid4()
    _, cert = _node_cert(ca_pair, node_id)
    der = cert.public_bytes(serialization.Encoding.DER)
    proxy_tls.set_host_map_provider(lambda: {"10.0.0.6": {str(node_id)}})
    try:
        assert proxy_tls.verify_node_peer("10.0.0.6", der) == str(node_id)
        proxy_tls.set_host_map_provider(lambda: {"10.0.0.6": {str(uuid.uuid4())}})
        with pytest.raises(ssl.SSLCertVerificationError):
            proxy_tls.verify_node_peer("10.0.0.6", der)
        # unknown host: still must be a node cert whose URI SAN names its CN
        proxy_tls.set_host_map_provider(lambda: {})
        assert proxy_tls.verify_node_peer("10.9.9.9", der) == str(node_id)
    finally:
        proxy_tls.set_host_map_provider(None)
    from app.services.pki import ca as cax

    key, ca_cert = ca_pair
    svc = cax.sign_service_csr(
        key,
        ca_cert,
        cax.build_csr(cax.generate_private_key("ecdsa_p256"), "api"),
        "api_layer",
        None,
        [],
        30,
    )
    with pytest.raises(ssl.SSLCertVerificationError):
        proxy_tls.verify_node_peer("x", svc.public_bytes(serialization.Encoding.DER))


def test_proxy_tls_handshake_checks_cn_not_hostname(ca_pair, tmp_path):
    """Real TLS: the server cert has no SAN for 127.0.0.1; chain + CN decide."""
    from app.services.pki import ca as cax
    from app.services.pki import proxy_tls

    node_id = uuid.uuid4()
    agent_key, cert = _node_cert(ca_pair, node_id)
    _, ca_cert = ca_pair
    (tmp_path / "n.crt").write_text(cax.cert_to_pem(cert))
    (tmp_path / "n.key").write_text(cax.key_to_pem(agent_key))
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(tmp_path / "n.crt", tmp_path / "n.key")

    async def run(expected_ids):
        async def handle(reader, writer):
            await reader.read(100)
            writer.write(b"HTTP/1.1 200 OK\r\ncontent-length: 2\r\n\r\nok")
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=server_ctx)
        port = server.sockets[0].getsockname()[1]
        proxy_tls.set_host_map_provider(lambda: {"127.0.0.1": expected_ids})
        try:
            import httpx

            ctx = proxy_tls.node_proxy_ssl_context()
            ctx.load_verify_locations(cadata=cax.cert_to_pem(ca_cert))
            async with httpx.AsyncClient(verify=ctx) as client:
                return (await client.get(f"https://127.0.0.1:{port}/")).status_code
        finally:
            proxy_tls.set_host_map_provider(None)
            server.close()
            await server.wait_closed()

    assert asyncio.run(run({str(node_id)})) == 200
    import httpx

    with pytest.raises(httpx.ConnectError):
        asyncio.run(run({str(uuid.uuid4())}))


def test_upstream_ssl_context_uses_node_proxy_verifier(monkeypatch):
    from app.core.settings import settings
    from app.services.pki.proxy_tls import NodeProxySSLContext
    from app.services.serving import upstream

    monkeypatch.setattr(settings, "SERVING_UPSTREAM_VERIFY_TLS", True)
    ctx = upstream._ssl_context(None)
    assert isinstance(ctx, NodeProxySSLContext)
    assert ctx.check_hostname is False and ctx.verify_mode == ssl.CERT_REQUIRED


# ------------------------------------------------------------- SSH keys


def _openssh_key(comment="dev@laptop"):
    key = ed25519.Ed25519PrivateKey.generate()
    pub = key.public_key().public_bytes(
        serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
    )
    return f"{pub.decode()} {comment}"


def test_parse_keys_fingerprint_matches_ssh_keygen(tmp_path):
    from app.core.exceptions import ValidationError
    from app.services.gpu_instances.keys import parse_keys

    line = _openssh_key()
    keys = parse_keys([line, line, "# comment", ""])
    assert len(keys) == 1 and keys[0]["type"] == "ssh-ed25519"
    assert keys[0]["comment"] == "dev@laptop"
    if shutil.which("ssh-keygen"):
        (tmp_path / "k.pub").write_text(line + "\n")
        out = subprocess.run(
            ["ssh-keygen", "-lf", str(tmp_path / "k.pub")],
            capture_output=True,
            text=True,
        ).stdout
        assert keys[0]["fingerprint"] in out
    for bad in (
        'command="rm -rf /" ' + line,
        "ssh-ed25519 not-base64!!",
        "ssh-rsa " + line.split()[1],  # type does not match the blob
    ):
        with pytest.raises(ValidationError):
            parse_keys([bad])
    with pytest.raises(ValidationError):
        parse_keys(["# only a comment"])


def test_gpu_instance_create_validation():
    from app.schemas.gpu_instances_schema import GpuInstanceCreate
    from pydantic import ValidationError

    key = _openssh_key()
    ok = GpuInstanceCreate(name="dev-box", authorized_keys=[key], slice_vram_bytes=8 << 30)
    assert ok.gpu_count == 1
    for bad in (
        {"name": "Dev_Box"},
        {"name": "x", "ssh_user": "root"},
        {"name": "x", "slice_vram_bytes": 1, "gpu_count": 2},
        {"name": "x", "slice_compute_pct": 50},
        {"name": "x", "allowed_source_cidrs": ["not-a-cidr"]},
        {"name": "x", "env": {"1BAD": "v"}},
    ):
        with pytest.raises(ValidationError):
            GpuInstanceCreate(authorized_keys=[key], **bad)


def test_gpu_instance_start_args_validate_against_command_json():
    from app.services.agent_hub.service import build_command_payload
    from app.services.gpu_instances.keys import parse_keys
    from app.services.gpu_instances.service import GpuInstanceService

    row = SimpleNamespace(
        id=uuid.uuid4(),
        name="dev-box",
        image="lscr.io/linuxserver/openssh-server:latest",
        limits={"cpu_cores": 4, "memory_bytes": 8 << 30},
        authorized_keys=parse_keys([_openssh_key()]),
        ssh_user="gpudeck",
        requested_ssh_port=None,
        container_port=2222,
        allowed_source_cidrs=["10.0.0.0/8"],
        env={"FOO": "bar"},
        volumes=[{"type": "volume", "source": "work", "target": "/work", "keep": True}],
        labels={"team": "ml"},
    )
    dev = SimpleNamespace(id=uuid.uuid4(), index=0, vendor="nvidia")
    from datetime import datetime, timedelta, timezone

    svc = GpuInstanceService.__new__(GpuInstanceService)
    args = svc.start_args(
        row,
        [dev],
        {"vram_bytes": 8 << 30, "compute_pct": 50},
        datetime.now(timezone.utc) + timedelta(hours=1),
    )
    payload = build_command_payload("start_ssh_instance", args, 900)
    assert payload["args"]["devices"][0]["slice"]["compute_pct"] == 50
    assert 3500 <= payload["args"]["ttl_seconds"] <= 3600
    build_command_payload("stop_ssh_instance", {"ssh_instance_id": str(row.id), "force": True}, 120)
    build_command_payload("scan_path", {"path": "~/.ollama/models", "max_depth": 4}, 600)
    build_command_payload("hash_files", {"root": "~/m", "paths": ["a.gguf"]}, 600)


# --------------------------------------------------------- cross-node PP


def test_node_ranks_merge_stages_per_node():
    from app.services.deployments.service import node_ranks, pp_master_port

    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    shards = [
        {"node_id": a, "device_ids": ["d0"], "pp_rank": 0},
        {"node_id": b, "device_ids": ["d1"], "pp_rank": 1},
        {"node_id": a, "device_ids": ["d2"], "pp_rank": 2},
    ]
    ranks = node_ranks(shards)
    assert [r["node_id"] for r in ranks] == [a, b]
    assert ranks[0]["device_ids"] == ["d0", "d2"] and ranks[1]["pp_rank"] == 1
    port = pp_master_port(uuid.uuid4())
    assert 29500 <= port < 29900


def test_load_model_with_distributed_validates():
    from tests.unit import test_deployment_launch_states as launch_states

    L = launch_states.L
    source = {
        "kind": "local_path",
        "repo_id": "/m",
        "revision": None,
        "endpoint": None,
        "token": None,
    }
    model = L.model_spec(
        launch_states.MODEL,
        launch_states.VERSION,
        launch_states.FILES,
        "qwen",
        "/var/cache/gd",
        source,
    )
    launch = L.launch_spec(
        launch_states.DEP, launch_states.MODEL, launch_states.ENGINE, launch_states.EV
    )
    head, worker = str(uuid.uuid4()), str(uuid.uuid4())
    launch["distributed"] = {
        "group_id": str(uuid.uuid4()),
        "rank": 1,
        "world_size": 2,
        "master_addr": "10.0.0.5",
        "master_port": 29600,
        "bind_address": "10.0.0.6",
        "rpc_port": 50052,
        "peers": [
            {
                "rank": 0,
                "node_id": head,
                "address": "10.0.0.5",
                "rpc_port": None,
                "device_ids": [],
            },
            {
                "rank": 1,
                "node_id": worker,
                "address": "10.0.0.6",
                "rpc_port": 50052,
                "device_ids": [],
            },
        ],
    }
    args = L.load_model_args(
        instance_id=uuid.uuid4(),
        deployment=launch_states.DEP,
        engine=L.engine_spec(launch_states.ENGINE, launch_states.EV, "linux", "x86_64"),
        model=model,
        devices=L.device_assignments(launch_states.DEVICES, None),
        launch=launch,
    )
    launch_states.assert_command("load_model", args)


# ----------------------------------------------------------- downloads


def _dist(tmp_path, monkeypatch):
    from app.services.nodes import agent_downloads as dl

    for version in ("1.0.0", "1.1.0"):
        d = tmp_path / version / "aarch64-apple-darwin"
        d.mkdir(parents=True)
        (d / "gpudeck-agent").write_bytes(f"binary {version}".encode())
    (tmp_path / "1.1.0" / "x86_64-unknown-linux-gnu").mkdir()
    lin = tmp_path / "1.1.0" / "x86_64-unknown-linux-gnu" / "gpudeck-agent"
    lin.write_bytes(b"linux")
    (lin.parent / "SHA256SUMS").write_text(f"{'a' * 64}  gpudeck-agent\n")
    monkeypatch.setattr(dl.download_settings, "AGENT_DIST_DIR", str(tmp_path))
    return dl


def test_agent_downloads_listing_and_resolution(tmp_path, monkeypatch):
    import hashlib

    from app.core.exceptions import ResourceNotFoundError, ValidationError

    dl = _dist(tmp_path, monkeypatch)
    arts = dl.list_artifacts()
    assert [a.version for a in arts][:2] == ["1.1.0", "1.1.0"]
    assert dl.latest_version() == "1.1.0"
    mac = dl.resolve("latest", "aarch64-apple-darwin")
    assert mac.version == "1.1.0" and (mac.os, mac.arch) == ("macos", "aarch64")
    assert mac.sha256 == hashlib.sha256(b"binary 1.1.0").hexdigest()
    assert dl.resolve("1.1.0", "x86_64-unknown-linux-gnu").sha256 == "a" * 64
    assert dl.resolve("1.0.0", "aarch64-apple-darwin").version == "1.0.0"
    with pytest.raises(ResourceNotFoundError):
        dl.resolve("9.9.9", "aarch64-apple-darwin")
    for bad in ("..", "../etc"):
        with pytest.raises((ValidationError, ResourceNotFoundError)):
            dl.resolve(bad, "aarch64-apple-darwin")


def test_install_script_is_valid_sh_and_embeds_join_values():
    from app.services.nodes import agent_downloads as dl

    script = dl.install_script(
        "gdj_" + "ab" * 20,
        hub_url="https://hub.example:8443",
        ca_fingerprint="cd" * 32,
        name="gpu-1",
        base_url="https://api.example/api/v1",
    )
    assert "TOKEN=gdj_" in script and "--ca-fingerprint" in script
    assert "https://api.example/api/v1" in script and "gpudeck-agent.service" in script
    assert "io.github.sur950.gpudeck-agent" in script
    checked = subprocess.run(["sh", "-n"], input=script, text=True, capture_output=True)
    assert checked.returncode == 0, checked.stderr
    assert "install.sh" in dl.install_command("gdj_x")


def test_install_script_linux_branch_works_as_root():
    """Run as root (cloud-init, containers) SUDO is empty: `$SUDO -u gpudeck` became
    `-u gpudeck ...` (command not found), and the service user could not write
    /etc/gpudeck/agent.toml. Verified live in a systemd container."""
    from app.services.nodes import agent_downloads as dl

    script = dl.install_script(
        "gdj_" + "ab" * 20, hub_url="https://hub.example:8443", ca_fingerprint=None
    )
    assert "$SUDO -u" not in script
    assert '$SUDO "$BIN" --config "$CONF" join $JOIN --state-dir "$STATE"' in script
    assert '$SUDO chown -R gpudeck:gpudeck "$STATE"' in script
    assert "ExecStart=$BIN --config $CONF run" in script
    assert "SupplementaryGroups=$GPU_GROUPS" in script  # only groups that exist


# -------------------------------------------------------------- catalog


def test_agent_scan_candidate_mapping():
    from app.services.catalog.service import _candidate_from_agent

    blob = _candidate_from_agent(
        {
            "path": "/Users/x/.ollama/models/blobs/sha256-abc",
            "kind": "ollama_blob",
            "format": "gguf",
            "files": [
                {
                    "path": "sha256-abc",
                    "absolute_path": "/Users/x/.ollama/models/blobs/sha256-abc",
                    "size_bytes": 10,
                    "format": "gguf",
                    "sha256": "a" * 64,
                }
            ],
            "total_size_bytes": 10,
            "suggested": {
                "name": "qwen3:0.6b",
                "quantization": "Q4_K_M",
                "family": None,
            },
        }
    )
    assert blob.config["local_root"] == "/Users/x/.ollama/models/blobs"
    assert blob.files[0].sha256 == "a" * 64 and blob.suggested["quantization"] == "Q4_K_M"
    # A null family from the agent is completed by the control plane's detector.
    assert blob.suggested["family"] == "qwen3"
    folder = _candidate_from_agent(
        {
            "path": "/m/gemma",
            "kind": "mlx_dir",
            "format": "mlx",
            "files": [
                {
                    "path": "model.safetensors",
                    "absolute_path": "/m/gemma/model.safetensors",
                    "size_bytes": 5,
                    "format": "safetensors",
                },
                {
                    "path": "config.json",
                    "absolute_path": "/m/gemma/config.json",
                    "size_bytes": 1,
                    "format": "other",
                },
            ],
            "total_size_bytes": 6,
        }
    )
    assert folder.config["local_root"] == "/m/gemma"


def test_control_plane_scan_expands_home(tmp_path, monkeypatch):
    from app.schemas.catalog_schema import LocalScanRequest
    from app.services.catalog.service import CatalogService

    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "tiny.gguf").write_bytes(b"GGUF" + b"\0" * 64)
    monkeypatch.setenv("HOME", str(tmp_path))
    svc = CatalogService.__new__(CatalogService)
    cands = asyncio.run(svc.scan_local(LocalScanRequest(path="~/models")))
    assert cands and cands[0].path.startswith(str(tmp_path))


# ------------------------------------------------------------- misc


def test_on_demand_message_parsing():
    from app.services.deployments.on_demand import channel, parse_message

    dep = uuid.uuid4()
    assert parse_message(json.dumps({"deployment_id": str(dep)})) == dep
    assert parse_message(b'{"deployment_id": "nope"}') is None
    assert parse_message("garbage") is None
    assert channel().endswith("deployments:start-requested")


def test_llamacpp_seed_metrics_and_router_facts():
    from app.services.engines.seed import SEED_ENGINE_VERSIONS

    llama = next(s for s in SEED_ENGINE_VERSIONS if s["engine"] == "llama_cpp")
    assert llama["metrics_config"]["path"] == "/metrics"
    assert llama["metrics_config"]["slots_path"] == "/slots"
    assert llama["metrics_config"]["metric_prefix"] == "llamacpp:"
    assert llama["requirements"]["router"]["models_read_at_start"] is True


def test_live_ws_is_a_regular_fastapi_websocket_route():
    from app.api.v1.routes import metrics
    from fastapi.routing import APIWebSocketRoute

    routes = [r for r in metrics.router.routes if getattr(r, "path", "") == "/live/ws"]
    assert routes and isinstance(routes[0], APIWebSocketRoute)


def test_agent_ca_route_has_no_auth_dependency():
    from app.services.agent_hub import routes

    route = next(r for r in routes.router.routes if getattr(r, "path", "") == "/agent/v1/ca")
    names = {d.call.__name__ for d in route.dependant.dependencies}
    assert not any("admin" in n for n in names)


def test_base64_helper_roundtrip():
    # guard for the OpenSSH fingerprint format (unpadded base64, 43 chars for sha256)
    from app.services.gpu_instances.keys import fingerprint

    fp = fingerprint(b"x")
    assert fp.startswith("SHA256:") and len(fp) == 7 + 43
    assert base64.b64decode(fp[7:] + "=")
