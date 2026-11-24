"""
Deployments pure logic: agent command builders validated against the
agent-protocol JSON Schemas, instance state machine, deployment status,
PD-split role targets, spec hash, agent hub port adapter, routing helpers.
"""

import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.core.exceptions import ConflictError
from app.services.deployments import launch as L
from app.services.deployments.agent_hub_port import B2AgentHubAdapter, FakeAgentHub
from app.services.deployments.routing_publisher import proxy_url
from app.services.deployments.service import role_targets, spec_hash
from app.services.deployments.states import (
    StatusInput,
    can_transition,
    derive_status,
    ensure_transition,
)
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

CONTRACTS = Path(__file__).resolve().parents[4] / "libs/contracts/agent-protocol"


def _validator(name):
    registry = Registry()
    schemas = {}
    for path in CONTRACTS.glob("*.json"):
        schema = json.loads(path.read_text())
        schemas[path.stem] = schema
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    return Draft202012Validator(schemas[name], registry=registry)


COMMAND = _validator("command")


def assert_command(command, args):
    errors = sorted(COMMAND.iter_errors({"command": command, "args": args}), key=str)
    assert not errors, [f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in errors[:5]]


def ns(**kw):
    return SimpleNamespace(**kw)


ENGINE = ns(
    name="llama_cpp",
    runtime="native",
    capabilities={"router_mode": True, "control_vectors": True},
)
EV = ns(
    id=uuid.uuid4(),
    version="b8798",
    image=None,
    artifacts={
        "macos/aarch64": {
            "url": "https://x/llama.tgz",
            "sha256": "a" * 64,
            "kind": "tarball",
            "entrypoint": "bin/llama-server",
        },
        "linux/x86_64": {
            "url": "https://x/l.tgz",
            "sha256": "0" * 64,
            "kind": "tarball",
        },
    },
    launch_template={
        "command": ["llama-server", "--port", "{{port}}", "{{flags}}"],
        "env": {"A": 1},
        "router_mode": True,
        "extra": "dropped",
    },
    default_launch_flags={"jinja": True, "flash-attn": "auto"},
    health_config={"path": "/health", "interval_seconds": 5, "unknown": 1},
    metrics_config={
        "path": "/metrics",
        "format": "prometheus",
        "slots_path": "/slots",
        "derive_from_proxy": False,
    },
    port_range=[10240, 10340],
)
MODEL = ns(
    id=uuid.uuid4(),
    name="qwen3:0.6b",
    type="chat",
    trust_remote_code=False,
    allow_pickle=False,
    default_context_size=8192,
    default_launch_flags={"temp": 0.6},
)
VERSION = ns(id=uuid.uuid4(), format="gguf", config={"n_layers": 28, "_private": 1})
FILES = [
    ns(
        id=uuid.uuid4(),
        path="model.gguf",
        format="gguf",
        size_bytes=522640096,
        sha256="b" * 64,
        is_required=True,
    ),
    ns(
        id=uuid.uuid4(),
        path="README.md",
        format="other",
        size_bytes=10,
        sha256=None,
        is_required=False,
    ),
]
DEP = ns(
    id=uuid.uuid4(),
    preset="high_throughput",
    launch_flags={"parallel": 4, "flash-attn": None},
    env_vars={"X": "1"},
    context_size=None,
    tool_parser="jinja",
    control_vectors=[{"path": "/cv.gguf", "scale": 0.5}],
    tp_size=1,
    pp_size=1,
    pd_split=False,
    pd_config={},
    spec_decoding={"enabled": True, "method": "draft_model"},
    kv_sharing=False,
    kv_sharing_config={},
    traffic_config={},
)
DEVICES = [ns(id=uuid.uuid4(), index=0, vendor="apple")]


def test_engine_spec_shapes_and_artifact_selection():
    spec = L.engine_spec(ENGINE, EV, "macos", "aarch64")
    assert spec["artifact"]["url"] == "https://x/llama.tgz"
    assert "extra" not in spec["launch_template"] and spec["launch_template"]["env"] == {"A": "1"}
    assert "unknown" not in spec["health"]
    assert L.engine_spec(ENGINE, EV, "linux", "x86_64")["artifact"] is None  # unpinned seed sha
    assert L.engine_spec(ENGINE, EV, "macos", "aarch64", installed=True)["artifact"] is None
    assert L.is_router_mode(EV)


def test_installed_engine_runs_natively_even_if_catalog_default_is_container():
    from app.services.deployments.service import _installed_engines, _native_engines

    vllm = ns(**{**vars(ENGINE), "name": "vllm", "runtime": "container"})
    assert L.engine_spec(vllm, EV, "linux", "x86_64")["runtime"] == "container"
    assert L.engine_spec(vllm, EV, "linux", "x86_64", installed=True)["runtime"] == "native"
    node = ns(
        capabilities={
            "engines": [
                {"name": "vllm", "runtime": "native", "path": "/opt/venv/bin/vllm"},
                {"name": "sglang", "runtime": "container"},
                {"name": "llama_cpp"},
            ]
        }
    )
    assert _native_engines(node) == ["vllm", "llama_cpp"]
    assert _installed_engines(node) == ["vllm", "sglang", "llama_cpp"]


def test_load_model_args_validate_against_contract():
    cache_root = L.default_cache_root(ns(cache_dir="/var/cache/gd/", os="linux"))
    assert cache_root == "/var/cache/gd"
    served = L.served_model_name(MODEL.name, DEP.id)
    assert served.startswith("qwen3:0.6b--")
    source = {
        "kind": "local_path",
        "repo_id": "/models/qwen",
        "revision": None,
        "endpoint": None,
        "token": None,
    }
    model = L.model_spec(MODEL, VERSION, FILES, served, cache_root, source)
    assert [f["path"] for f in model["files"]] == ["model.gguf"]
    assert model["cache_dir"] == f"/var/cache/gd/{VERSION.id}"
    assert "_private" not in model["config"]
    launch = L.launch_spec(DEP, MODEL, ENGINE, EV, None, "/cache/draft.gguf")
    assert launch["flags"]["parallel"] == 4 and "flash-attn" not in launch["flags"]
    assert launch["flags"]["temp"] == 0.6 and launch["flags"]["jinja"] is True
    assert launch["context_size"] == 8192 and launch["max_concurrency"] == 4
    assert launch["spec_decoding"]["draft_model_path"] == "/cache/draft.gguf"
    engine = L.engine_spec(ENGINE, EV, "macos", "aarch64")
    devices = L.device_assignments(DEVICES, {"vram_bytes": 4 << 30, "compute_pct": 50})
    proc = uuid.uuid4()
    args = L.load_model_args(
        instance_id=uuid.uuid4(),
        deployment=DEP,
        engine=engine,
        model=model,
        devices=devices,
        launch=launch,
        engine_process_id=proc,
        draft_model=None,
    )
    assert_command("load_model", args)
    assert_command("start_engine", L.start_engine_args(proc, engine, devices, launch))
    assert_command(
        "unload_model",
        L.unload_model_args(
            uuid.uuid4(), force=True, drain_timeout_seconds=0, stop_engine_if_empty=True
        ),
    )


def test_flag_layers_respect_none_removal():
    dep = ns(**{**DEP.__dict__, "preset": None, "launch_flags": {"jinja": False}})
    flags = L.effective_flags(dep, MODEL, ENGINE, EV)
    assert "jinja" not in flags and flags["flash-attn"] == "auto"


def test_state_machine():
    assert can_transition("pending", "loading")
    assert can_transition("loading", "ready")
    assert can_transition("ready", "draining") and can_transition("draining", "unloading")
    assert can_transition("unloading", "stopped")
    assert not can_transition("stopped", "loading")
    assert not can_transition("unloading", "ready")
    assert can_transition("error", "pending")
    with pytest.raises(ConflictError):
        ensure_transition("stopped", "ready")


@pytest.mark.parametrize(
    "inp,expected",
    [
        (StatusInput(False, True, 2, ["ready", "ready"]), "running"),
        (StatusInput(False, True, 2, ["ready", "loading"]), "scaling"),
        (StatusInput(False, True, 2, ["ready"]), "degraded"),
        (StatusInput(False, True, 2, ["loading", "pending"]), "deploying"),
        (StatusInput(False, True, 1, []), "scheduling"),
        (StatusInput(False, True, 1, [], unplaceable="no room"), "pending"),
        (StatusInput(False, True, 2, ["ready"], unplaceable="no room"), "degraded"),
        (StatusInput(False, False, 1, ["unloading"]), "scaling"),
        (StatusInput(False, False, 1, []), "stopped"),
        (StatusInput(False, True, 0, []), "stopped"),
        (StatusInput(True, False, 0, ["ready"]), "deleting"),
        (StatusInput(False, True, 1, ["ready"], crash_looping=True), "failed"),
        (StatusInput(False, True, 1, ["ready"], active_swaps=1), "scaling"),
        (StatusInput(False, True, 1, ["ready", "draining"]), "scaling"),
    ],
)
def test_derive_status(inp, expected):
    assert derive_status(inp) == expected


def test_role_targets_and_spec_hash():
    base = dict(replicas=3, desired_running=True, deleted_at=None, pd_split=False, pd_config={})
    assert role_targets(ns(**base)) == {None: 3}
    assert role_targets(ns(**{**base, "desired_running": False})) == {None: 0}
    pd = ns(**{**base, "pd_split": True, "pd_config": {"prefill_replicas": 2}})
    assert role_targets(pd) == {"prefill": 2, "decode": 3}
    h1 = spec_hash(DEP)
    other = ns(**{**DEP.__dict__, "launch_flags": {"parallel": 8}})
    assert spec_hash(other) != h1
    same = ns(**{**DEP.__dict__, "traffic_config": {"strategy": "weighted"}})
    assert spec_hash(same) == h1


def test_proxy_url():
    assert (
        proxy_url(
            ns(
                proxy_address="10.0.0.5",
                ip_address=None,
                hostname=None,
                proxy_port=9443,
            )
        )
        == "https://10.0.0.5:9443"
    )
    assert (
        proxy_url(
            ns(
                proxy_address=None,
                ip_address="10.0.0.6/32",
                hostname=None,
                proxy_port=None,
            )
        )
        == "https://10.0.0.6:9443"
    )
    assert (
        proxy_url(ns(proxy_address="fe80::1", ip_address=None, hostname=None, proxy_port=1))
        == "https://[fe80::1]:1"
    )
    assert proxy_url(ns(proxy_address=None, ip_address=None, hostname=None, proxy_port=1)) is None


async def test_fake_hub_and_fleet_adapter():
    fake = FakeAgentHub()
    nid = uuid.uuid4()
    cid = await fake.send_command(nid, "load_model", {"x": 1})
    assert fake.of("load_model")[0].id == cid and await fake.is_connected(nid)
    with pytest.raises(ValueError):
        await fake.send_command(nid, "bogus", {})

    calls = []

    class StaticHub:
        @staticmethod
        async def send_command(
            node_id,
            command,
            args,
            timeout=None,
            swap_operation_id=None,
            instance_id=None,
            wait=True,
        ):
            calls.append((node_id, command, timeout, wait, instance_id))
            return SimpleNamespace(command_id=uuid.UUID(int=7), state="pending")

        @staticmethod
        async def is_connected(node_id):
            return True

    adapter = B2AgentHubAdapter(StaticHub)
    iid = uuid.uuid4()
    out = await adapter.send_command(nid, "unload_model", {}, instance_id=iid, timeout_seconds=30)
    assert out == uuid.UUID(int=7)
    assert calls == [(nid, "unload_model", 30, False, iid)]
    assert await adapter.is_connected(nid)


async def test_placement_failure_asks_rules_to_make_room(monkeypatch):
    from app.services.deployments import reconcile

    calls = []

    async def fake_make_room(dep_id):
        calls.append(dep_id)
        return [uuid.uuid4()]

    async def no_redis():
        raise ConnectionError("no redis in unit tests")

    monkeypatch.setattr("app.services.rules.service.make_room_for", fake_make_room, raising=False)
    monkeypatch.setattr("app.db.redis.client.get_redis", no_redis)
    dep_id = uuid.uuid4()
    await reconcile._request_room(
        dep_id, "replica 1 of 1 does not fit: needs 9000 MiB, 100 MiB free (1)"
    )
    await reconcile._request_room(dep_id, "replica 1 of 1 does not fit: label zone='a' != 'b' (1)")
    assert calls == [dep_id]
