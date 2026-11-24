"""
Engine registry pure logic: VRAM estimator, compatibility matrix (model-type table x the 11 seeded
engines), auto engine pick and presets.

Engines are parsed from the real ``001_init/up.sql`` seed so the matrix tests
the shipped data, not a copy.
"""

import json
import re
from pathlib import Path

import pytest
from app.services.engines import compat
from app.services.engines.presets import (
    flags_to_argv,
    merge_launch_flags,
    preset_concurrency,
    preset_flags,
    render_engine_flags,
)
from app.services.engines.seed import ENGINE_IDS, SEED_ENGINE_VERSIONS
from app.services.engines.vram import (
    GIB,
    MIB,
    bytes_per_param,
    estimate_vram,
    kv_bytes_per_token,
    max_context_for_budget,
)

UP_SQL = Path(__file__).resolve().parents[2] / "app/db/postgres/alembic/migrations/001_init/up.sql"

QWEN3_06B = {
    "n_layers": 28,
    "n_kv_heads": 8,
    "head_dim": 128,
    "max_position_embeddings": 40960,
}

PRODUCT_PLAN_TABLE = {
    "chat": {"llama_cpp", "vllm", "sglang", "tensorrt_llm", "mlx"},
    "thinking": {"llama_cpp", "vllm", "sglang", "tensorrt_llm", "mlx"},
    "vision": {"llama_cpp", "vllm", "sglang", "mlx"},
    "embedding": {"llama_cpp", "vllm", "sglang"},
    "rerank": {"llama_cpp", "vllm"},
    "stt": {"whisper_cpp", "mlx_audio"},
    "tts": {"vllm_omni", "mlx_audio"},
    "image": {"stable_diffusion_cpp", "vllm_omni", "sglang_diffusion"},
    "video": {"vllm_omni", "sglang_diffusion"},
}


def _parse_seed_engines():
    sql = UP_SQL.read_text()
    block = sql[sql.index("INSERT INTO engines") :]
    block = block[: block.index(";\n")]
    pattern = re.compile(
        r"\('(?P<id>[0-9a-f-]{36})', '(?P<name>\w+)', '[^']*', '(?P<runtime>\w+)',\s*"
        r"'\{(?P<types>[^}]*)\}', '\{(?P<vendors>[^}]*)\}', "
        r"'\{(?P<os>[^}]*)\}', '\{(?P<formats>[^}]*)\}',\s*"
        r"'(?P<caps>\{.*?\})', TRUE\)",
        re.S,
    )
    engines = {}
    for m in pattern.finditer(block):
        engines[m["name"]] = m.groupdict()
    return engines


SEED = _parse_seed_engines()


def engine_info(name, *, status="available", enabled=True, artifacts=None, image="img:1"):
    row = SEED[name]
    return compat.EngineInfo(
        engine_id=row["id"],
        engine_version_id=f"ev-{name}",
        name=name,
        version="1",
        runtime=row["runtime"],
        is_enabled=enabled,
        version_status=status,
        supported_model_types=row["types"].split(","),
        supported_vendors=row["vendors"].split(","),
        supported_os=row["os"].split(","),
        supported_formats=row["formats"].split(","),
        capabilities=json.loads(row["caps"]),
        image=image if row["runtime"] == "container" else None,
        artifacts=artifacts or {},
    )


def model(mtype="chat", fmt="gguf", size=522_640_096, **kw):
    kw.setdefault("config", dict(QWEN3_06B))
    return compat.ModelVersionInfo(
        model_version_id="mv",
        model_type=mtype,
        format=fmt,
        status=kw.pop("status", "ready"),
        total_size_bytes=size,
        parameter_count=kw.pop("parameter_count", None),
        quantization=kw.pop("quantization", None),
        **kw,
    )


def fmt_for(engine_name, mtype):
    formats = SEED[engine_name]["formats"].split(",")
    for f in ("gguf", "mlx", "safetensors", "other"):
        if f in formats:
            return f
    return formats[0]


# ------------------------------------------------------------------ seed


def test_seed_has_eleven_engines_matching_ids():
    assert set(SEED) == set(ENGINE_IDS)
    for name, row in SEED.items():
        assert row["id"] == ENGINE_IDS[name]
    assert {s["engine"] for s in SEED_ENGINE_VERSIONS} == set(ENGINE_IDS)


def test_seed_versions_have_launch_health_metrics():
    for spec in SEED_ENGINE_VERSIONS:
        assert spec["launch_template"]["command"], spec["engine"]
        assert "{{port}}" in spec["launch_template"]["command"], spec["engine"]
        assert spec["health_config"].get("path"), spec["engine"]
        assert "derive_from_proxy" in spec["metrics_config"], spec["engine"]
        if SEED[spec["engine"]]["runtime"] == "container":
            assert spec.get("image"), spec["engine"]
    llama = next(s for s in SEED_ENGINE_VERSIONS if s["engine"] == "llama_cpp")
    assert llama["launch_template"]["router_mode"] is True
    assert llama["metrics_config"]["slots_path"] == "/slots"


# ------------------------------------------------------------- VRAM


def test_kv_bytes_per_token_qwen3():
    assert kv_bytes_per_token(QWEN3_06B) == 2 * 28 * 8 * 128 * 2
    assert kv_bytes_per_token(QWEN3_06B, "q8_0") == round(2 * 28 * 8 * 128 * 1.06)
    assert kv_bytes_per_token({}, fallback=1234) == 1234
    # head_dim derived from hidden_size / n_heads
    assert (
        kv_bytes_per_token({"n_layers": 2, "n_heads": 4, "hidden_size": 256}) == 2 * 2 * 4 * 64 * 2
    )


def test_estimate_vram_exact_numbers():
    est = estimate_vram(
        engine_name="llama_cpp",
        weights_bytes=522_640_096,
        parameter_count=None,
        quantization=None,
        config=dict(QWEN3_06B),
        context_size=4096,
        concurrency=1,
    )
    assert est.kv_bytes_per_token == 114_688
    assert est.kv_cache_bytes == 114_688 * 4096
    assert est.activation_bytes == 256 * MIB
    assert est.overhead_bytes == 192 * MIB
    assert est.total_bytes == 522_640_096 + 114_688 * 4096 + 256 * MIB + 192 * MIB
    assert est.per_gpu_bytes == est.total_bytes


def test_estimate_scales_with_context_concurrency_and_tp():
    base = dict(
        engine_name="llama_cpp",
        weights_bytes=16 * GIB,
        parameter_count=None,
        quantization=None,
        config=dict(QWEN3_06B),
    )
    one = estimate_vram(context_size=8192, concurrency=1, **base)
    four = estimate_vram(context_size=8192, concurrency=4, **base)
    assert four.kv_cache_bytes == 4 * one.kv_cache_bytes
    tp2 = estimate_vram(context_size=8192, concurrency=1, tp_size=2, **base)
    assert tp2.per_gpu_bytes < one.per_gpu_bytes
    assert tp2.per_gpu_bytes == -(-(one.total_bytes - one.overhead_bytes) // 2) + one.overhead_bytes


def test_estimate_from_params_when_no_size():
    est = estimate_vram(
        engine_name="vllm",
        weights_bytes=None,
        parameter_count=7_000_000_000,
        quantization="Q4_K_M",
        config={},
        context_size=2048,
    )
    assert est.weights_bytes == int(7e9 * bytes_per_param("Q4_K_M"))
    assert bytes_per_param("bf16") == 2.0 and bytes_per_param(None) == 2.0


def test_default_context_and_embedding_kv():
    est = estimate_vram(
        engine_name="llama_cpp",
        weights_bytes=GIB,
        parameter_count=None,
        quantization=None,
        config=dict(QWEN3_06B),
        context_size=None,
    )
    assert est.context_size == 40960
    emb = estimate_vram(
        engine_name="llama_cpp",
        weights_bytes=GIB,
        parameter_count=None,
        quantization=None,
        config={**QWEN3_06B, "model_type_hint": "embedding"},
        context_size=32768,
        concurrency=8,
    )
    assert emb.kv_cache_bytes == 114_688 * 8192


def test_paged_engines_need_one_sequence_and_claim_a_fraction():
    from app.services.engines.vram import device_memory_fraction

    est = estimate_vram(
        engine_name="vllm",
        weights_bytes=GIB,
        parameter_count=None,
        quantization=None,
        config=dict(QWEN3_06B),
        context_size=8192,
        concurrency=256,
    )
    assert est.kv_cache_bytes == 114_688 * 8192
    assert device_memory_fraction("vllm", {}) == 0.9
    assert device_memory_fraction("vllm", {"gpu-memory-utilization": 0.5}) == 0.5
    assert device_memory_fraction("sglang", {}) == 0.88
    assert device_memory_fraction("llama_cpp", {}) is None


def test_max_context_for_budget():
    per_token = 114_688
    ctx = max_context_for_budget(8 * GIB, GIB, per_token)
    assert ctx > 0
    assert max_context_for_budget(GIB, 2 * GIB, per_token) == 0


# ------------------------------------------------------------- compat


@pytest.mark.parametrize("mtype", sorted(PRODUCT_PLAN_TABLE))
def test_compat_matrix_matches_product_plan(mtype):
    for name in SEED:
        outcome = compat.check_compat(model(mtype, fmt_for(name, mtype)), engine_info(name))
        allowed = name == "custom" or name in PRODUCT_PLAN_TABLE[mtype]
        if allowed:
            assert outcome.result != "incompatible", (mtype, name, outcome.reasons)
        else:
            assert outcome.result == "incompatible", (mtype, name)
            assert "model_type_unsupported" in outcome.reason_codes()


def test_platform_checks():
    apple = compat.Target(os="macos", arch="aarch64", vendor="apple", device_vram_bytes=[48 * GIB])
    vllm = compat.check_compat(model(fmt="safetensors"), engine_info("vllm"), apple)
    assert vllm.result == "incompatible"
    assert {"vendor_unsupported", "os_unsupported", "container_on_macos"} <= set(
        vllm.reason_codes()
    )
    nvidia = compat.Target(os="linux", arch="x86_64", vendor="nvidia", device_vram_bytes=[80 * GIB])
    assert (
        compat.check_compat(model(fmt="mlx"), engine_info("mlx"), nvidia).result == "incompatible"
    )
    amd = compat.Target(os="linux", arch="x86_64", vendor="amd", device_vram_bytes=[80 * GIB])
    trt = compat.check_compat(model(fmt="safetensors"), engine_info("tensorrt_llm"), amd)
    assert "vendor_unsupported" in trt.reason_codes()
    no_image = compat.check_compat(
        model(fmt="safetensors"), engine_info("vllm", image=None), nvidia
    )
    assert "image_missing" in no_image.reason_codes()


def test_native_artifact_missing_unless_installed():
    arts = {"linux/x86_64": {"url": "u", "sha256": "0" * 64, "kind": "tarball"}}
    mac = compat.Target(os="macos", arch="aarch64", vendor="apple", device_vram_bytes=[48 * GIB])
    out = compat.check_compat(model(), engine_info("llama_cpp", artifacts=arts), mac)
    assert "artifact_missing" in out.reason_codes()
    mac.node_engines = ["llama_cpp"]
    assert (
        compat.check_compat(model(), engine_info("llama_cpp", artifacts=arts), mac).result
        == "compatible"
    )


def test_format_and_security_policy():
    assert (
        "format_unsupported"
        in compat.check_compat(model(fmt="gguf"), engine_info("vllm")).reason_codes()
    )
    blocked = compat.check_compat(model(fmt="pickle"), engine_info("custom"))
    assert "pickle_blocked" in blocked.reason_codes()
    allowed = compat.check_compat(model(fmt="pickle", allow_pickle=True), engine_info("custom"))
    assert allowed.result != "incompatible"
    assert (
        "model_version_blocked"
        in compat.check_compat(model(status="blocked"), engine_info("llama_cpp")).reason_codes()
    )
    info = compat.check_compat(model(trust_remote_code=True), engine_info("llama_cpp"))
    assert "trust_remote_code_ignored" in info.reason_codes() and info.result != "incompatible"


def test_parallelism_and_feature_capabilities():
    llama = engine_info("llama_cpp")
    out = compat.check_compat(model(), llama, shape=compat.Shape(gpu_count=2, tp_size=2))
    assert {"tp_unsupported", "multi_gpu_unsupported"} <= set(out.reason_codes())
    mismatch = compat.check_compat(
        model(fmt="safetensors"),
        engine_info("vllm"),
        shape=compat.Shape(gpu_count=3, tp_size=2),
    )
    assert "gpu_count_mismatch" in mismatch.reason_codes()
    ok = compat.check_compat(
        model(fmt="safetensors"),
        engine_info("vllm"),
        shape=compat.Shape(
            gpu_count=4,
            tp_size=2,
            pp_size=2,
            pd_split=True,
            kv_sharing=True,
            spec_decoding=True,
        ),
    )
    assert ok.result != "incompatible", ok.reasons
    cv = compat.check_compat(
        model(fmt="safetensors"),
        engine_info("vllm"),
        shape=compat.Shape(control_vectors=True),
    )
    assert "control_vectors_unsupported" in cv.reason_codes()
    sl = compat.check_compat(
        model(fmt="mlx"), engine_info("mlx"), shape=compat.Shape(slice_vram_bytes=GIB)
    )
    assert "slices_unsupported" in sl.reason_codes()
    assert (
        compat.check_compat(
            model(),
            llama,
            shape=compat.Shape(control_vectors=True, slice_vram_bytes=4 * GIB, context_size=4096),
        ).result
        != "incompatible"
    )


def test_vram_fit_and_slice_size():
    small = compat.Target(os="linux", arch="x86_64", vendor="nvidia", device_vram_bytes=[GIB])
    out = compat.check_compat(
        model(), engine_info("llama_cpp"), small, compat.Shape(context_size=4096)
    )
    assert "insufficient_vram" in out.reason_codes()
    big = compat.Target(os="linux", arch="x86_64", vendor="nvidia", device_vram_bytes=[24 * GIB])
    assert (
        compat.check_compat(
            model(), engine_info("llama_cpp"), big, compat.Shape(context_size=4096)
        ).result
        == "compatible"
    )
    tiny_slice = compat.check_compat(
        model(),
        engine_info("llama_cpp"),
        big,
        compat.Shape(context_size=4096, slice_vram_bytes=256 * MIB),
    )
    assert "slice_too_small" in tiny_slice.reason_codes()
    unknown = compat.check_compat(model(), engine_info("llama_cpp"))
    assert unknown.result == "unknown" and "vram_unverified" in unknown.reason_codes()
    assert unknown.estimated_vram_bytes and unknown.estimated_vram_bytes > 522_640_096


def test_disabled_and_deprecated():
    assert (
        compat.check_compat(model(), engine_info("llama_cpp", enabled=False)).result
        == "incompatible"
    )
    assert (
        compat.check_compat(model(), engine_info("llama_cpp", status="disabled")).result
        == "incompatible"
    )
    dep = compat.check_compat(model(), engine_info("llama_cpp", status="deprecated"))
    assert dep.result != "incompatible" and "engine_version_deprecated" in dep.reason_codes()


def _pick(m, target, shape=None):
    outcomes = [compat.check_compat(m, engine_info(n), target, shape) for n in SEED]
    best = compat.auto_pick(outcomes)
    return best.engine.name if best else None


def test_auto_pick():
    mac = compat.Target(
        os="macos",
        arch="aarch64",
        vendor="apple",
        device_vram_bytes=[48 * GIB],
        node_engines=["llama_cpp", "mlx"],
    )
    linux = compat.Target(os="linux", arch="x86_64", vendor="nvidia", device_vram_bytes=[80 * GIB])
    assert _pick(model("chat", "gguf"), mac) == "llama_cpp"
    assert _pick(model("chat", "mlx"), mac) == "mlx"
    assert _pick(model("chat", "safetensors"), linux) == "vllm"
    assert (
        _pick(model("chat", "safetensors"), linux, compat.Shape(preset="high_throughput")) == "vllm"
    )
    assert _pick(model("embedding", "gguf"), mac) == "llama_cpp"
    assert _pick(model("stt", "other", config={}), mac) == "whisper_cpp"
    assert _pick(model("tts", "mlx", config={}), mac) == "mlx_audio"
    assert _pick(model("image", "gguf", config={}), linux) == "stable_diffusion_cpp"
    assert (
        _pick(model("chat", "pickle"), linux) is None
        or _pick(model("chat", "pickle"), linux) == "custom"
    )


def test_rank_is_deterministic():
    outs = [compat.check_compat(model(), engine_info(n)) for n in SEED]
    assert [o.engine.name for o in compat.rank(outs)] == [
        o.engine.name for o in compat.rank(list(reversed(outs)))
    ]


# ------------------------------------------------------------- presets


def test_presets_and_merge():
    assert preset_flags("vllm", "high_throughput")["max-num-seqs"] == 256
    assert preset_flags("llama_cpp", None) == {}
    merged = merge_launch_flags(
        {"a": 1, "jinja": True}, {"b": 2}, {"a": 3}, {"jinja": None, "c": False}
    )
    assert merged == {"a": 3, "b": 2}
    assert preset_concurrency("high_throughput", {}, "vllm") == 16
    assert preset_concurrency(None, {"parallel": 6}, "llama_cpp") == 6
    assert preset_concurrency(None, {}, "llama_cpp") == 1


def test_render_engine_flags():
    v = render_engine_flags(
        "vllm",
        {
            "context_size": 8192,
            "tp_size": 2,
            "pp_size": 2,
            "tool_parser": "hermes",
            "pd_split": True,
            "pd_role": "prefill",
            "pd_config": {"transfer": "nixl"},
            "spec_decoding": {
                "enabled": True,
                "method": "ngram",
                "num_draft_tokens": 4,
            },
        },
    )
    assert (
        v["max-model-len"] == 8192
        and v["tensor-parallel-size"] == 2
        and v["pipeline-parallel-size"] == 2
    )
    assert v["tool-call-parser"] == "hermes" and v["enable-auto-tool-choice"] is True
    assert json.loads(v["kv-transfer-config"]) == {
        "kv_connector": "NixlConnector",
        "kv_role": "kv_producer",
    }
    assert json.loads(v["speculative-config"])["num_speculative_tokens"] == 4
    ll = render_engine_flags(
        "llama_cpp",
        {
            "context_size": 4096,
            "tool_parser": "jinja",
            "control_vectors": [
                {"path": "/cv/a.gguf"},
                {"path": "/cv/b.gguf", "scale": 0.5, "layer_range": [10, 20]},
            ],
        },
    )
    assert ll["ctx-size"] == 4096 and ll["jinja"] is True
    assert ll["control-vector"] == ["/cv/a.gguf"]
    assert ll["control-vector-scaled"] == ["/cv/b.gguf:0.5"]
    sg = render_engine_flags("sglang", {"tp_size": 4, "kv_sharing": True})
    assert sg["tp-size"] == 4 and sg["enable-hierarchical-cache"] is True
    argv = flags_to_argv({"ctx-size": 4096, "jinja": True, "control-vector": ["a", "b"], "x": None})
    assert argv == [
        "--ctx-size",
        "4096",
        "--jinja",
        "--control-vector",
        "a",
        "--control-vector",
        "b",
    ]
