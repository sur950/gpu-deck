"""
Launch-flag presets and engine-flag rendering (pure functions).

``preset_flags(engine, preset)`` maps ``low_latency`` / ``high_throughput`` to
engine CLI flags. ``merge_launch_flags`` layers engine defaults < model
defaults < preset < deployment flags. ``render_engine_flags`` turns a
``LaunchSpec`` (tp/pp, context, tool parser, spec decoding, PD split, KV
sharing, control vectors) into the engine's concrete flags — the node agent
does the same from the structural fields; this rendering feeds previews,
``suggested_launch_flags`` and the custom-engine ``{{flags}}`` path.

Flag dict convention (LaunchSpec.flags): ``{"flag": value}``; ``True`` = bare
flag; a list repeats the flag; ``None``/``False`` drops it.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

_PRESETS: Dict[str, Dict[str, Dict[str, Any]]] = {
    "llama_cpp": {
        "low_latency": {
            "parallel": 2,
            "batch-size": 512,
            "ubatch-size": 256,
            "flash-attn": "on",
        },
        "high_throughput": {
            "parallel": 8,
            "batch-size": 2048,
            "ubatch-size": 512,
            "flash-attn": "on",
            "cache-type-k": "q8_0",
            "cache-type-v": "q8_0",
            "cont-batching": True,
        },
    },
    "vllm": {
        "low_latency": {
            "max-num-seqs": 8,
            "enable-chunked-prefill": True,
            "enable-prefix-caching": True,
            "gpu-memory-utilization": 0.85,
        },
        "high_throughput": {
            "max-num-seqs": 256,
            "max-num-batched-tokens": 8192,
            "enable-chunked-prefill": True,
            "enable-prefix-caching": True,
            "gpu-memory-utilization": 0.92,
        },
    },
    "vllm_omni": {
        "low_latency": {"max-num-seqs": 2},
        "high_throughput": {"max-num-seqs": 8},
    },
    "sglang": {
        "low_latency": {
            "max-running-requests": 8,
            "schedule-policy": "fcfs",
            "mem-fraction-static": 0.85,
        },
        "high_throughput": {
            "max-running-requests": 256,
            "chunked-prefill-size": 8192,
            "schedule-policy": "lpm",
            "mem-fraction-static": 0.9,
        },
    },
    "sglang_diffusion": {"low_latency": {}, "high_throughput": {}},
    "tensorrt_llm": {
        "low_latency": {"max_batch_size": 8},
        "high_throughput": {"max_batch_size": 256, "max_num_tokens": 8192},
    },
    "mlx": {"low_latency": {}, "high_throughput": {}},
    "mlx_audio": {"low_latency": {}, "high_throughput": {}},
    "whisper_cpp": {"low_latency": {"threads": 4}, "high_throughput": {"threads": 8}},
    "stable_diffusion_cpp": {"low_latency": {}, "high_throughput": {}},
    "custom": {"low_latency": {}, "high_throughput": {}},
}

# Default concurrency per preset (slots / max-num-seqs) used by the VRAM estimate.
PRESET_CONCURRENCY = {"low_latency": 2, "high_throughput": 16}


def preset_flags(engine_name: str, preset: Optional[str]) -> Dict[str, Any]:
    if not preset:
        return {}
    return dict(_PRESETS.get(engine_name, {}).get(preset, {}))


def merge_launch_flags(
    engine_defaults: Optional[Dict[str, Any]],
    model_defaults: Optional[Dict[str, Any]],
    preset: Dict[str, Any],
    deployment_flags: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Later layers win; ``None`` / ``False`` in a layer removes a flag."""
    merged: Dict[str, Any] = {}
    for layer in (
        engine_defaults or {},
        model_defaults or {},
        preset,
        deployment_flags or {},
    ):
        for key, value in layer.items():
            if value is None or value is False:
                merged.pop(key, None)
            else:
                merged[key] = value
    return merged


def preset_concurrency(
    preset: Optional[str], launch_flags: Dict[str, Any], engine_name: str
) -> int:
    """Concurrent sequences an instance serves (for KV sizing)."""
    for key in (
        "parallel",
        "max-num-seqs",
        "max-running-requests",
        "max_batch_size",
        "np",
    ):
        value = launch_flags.get(key)
        if isinstance(value, int) and value > 0:
            return value
    if preset in PRESET_CONCURRENCY:
        return PRESET_CONCURRENCY[preset]
    return 1


def render_engine_flags(engine_name: str, launch: Dict[str, Any]) -> Dict[str, Any]:
    """Concrete engine flags for a LaunchSpec (structural fields + flags)."""
    flags: Dict[str, Any] = dict(launch.get("flags") or {})
    ctx = launch.get("context_size")
    tp = int(launch.get("tp_size") or 1)
    pp = int(launch.get("pp_size") or 1)
    parser = launch.get("tool_parser")
    spec = launch.get("spec_decoding") or {}
    pd_split = bool(launch.get("pd_split"))
    pd_role = launch.get("pd_role")
    pd_cfg = launch.get("pd_config") or {}
    kv_sharing = bool(launch.get("kv_sharing"))
    kv_cfg = launch.get("kv_sharing_config") or {}
    cvs: List[Dict[str, Any]] = launch.get("control_vectors") or []
    max_conc = launch.get("max_concurrency")

    if engine_name == "llama_cpp":
        if ctx:
            flags["ctx-size"] = ctx
        if parser == "jinja" or parser:
            flags["jinja"] = True
        if max_conc:
            flags["parallel"] = max_conc
        if spec.get("enabled"):
            if spec.get("method") == "draft_model" and spec.get("draft_model_path"):
                flags["model-draft"] = spec["draft_model_path"]
            elif spec.get("method") in ("ngram", None):
                flags["spec-type"] = "ngram-simple"
            if spec.get("num_draft_tokens"):
                flags["draft-max"] = spec["num_draft_tokens"]
        if cvs:
            plain = [cv["path"] for cv in cvs if float(cv.get("scale", 1)) == 1.0]
            scaled = [
                f"{cv['path']}:{cv.get('scale')}" for cv in cvs if float(cv.get("scale", 1)) != 1.0
            ]
            if plain:
                flags["control-vector"] = plain
            if scaled:
                flags["control-vector-scaled"] = scaled
            ranges = [cv.get("layer_range") for cv in cvs if cv.get("layer_range")]
            if ranges:
                flags["control-vector-layer-range"] = f"{ranges[0][0]} {ranges[0][1]}"
    elif engine_name in ("vllm", "vllm_omni"):
        if ctx:
            flags["max-model-len"] = ctx
        if tp > 1:
            flags["tensor-parallel-size"] = tp
        if pp > 1:
            flags["pipeline-parallel-size"] = pp
        if parser:
            flags["enable-auto-tool-choice"] = True
            flags["tool-call-parser"] = parser
        if max_conc:
            flags["max-num-seqs"] = max_conc
        if spec.get("enabled"):
            method = spec.get("method") or "ngram"
            cfg: Dict[str, Any] = {
                "method": method,
                "num_speculative_tokens": int(spec.get("num_draft_tokens") or 5),
            }
            if method == "draft_model" and spec.get("draft_model_path"):
                cfg = {
                    "model": spec["draft_model_path"],
                    "num_speculative_tokens": cfg["num_speculative_tokens"],
                }
            elif method == "ngram":
                cfg["prompt_lookup_max"] = int(spec.get("prompt_lookup_max") or 4)
            flags["speculative-config"] = _json(cfg)
        if pd_split and pd_role:
            transfer = pd_cfg.get("transfer", "nixl")
            connector = "NixlConnector" if transfer == "nixl" else "MooncakeConnector"
            role = "kv_producer" if pd_role == "prefill" else "kv_consumer"
            flags["kv-transfer-config"] = _json({"kv_connector": connector, "kv_role": role})
        elif kv_sharing:
            backend = kv_cfg.get("backend", "lmcache")
            connector = {
                "lmcache": "LMCacheConnectorV1",
                "mooncake": "MooncakeStoreConnector",
            }.get(backend, "SharedStorageConnector")
            flags["kv-transfer-config"] = _json({"kv_connector": connector, "kv_role": "kv_both"})
    elif engine_name == "sglang":
        if ctx:
            flags["context-length"] = ctx
        if tp > 1:
            flags["tp-size"] = tp
        if pp > 1:
            flags["pp-size"] = pp
        if parser:
            flags["tool-call-parser"] = parser
        if max_conc:
            flags["max-running-requests"] = max_conc
        if spec.get("enabled"):
            flags["speculative-algorithm"] = (spec.get("method") or "eagle").upper()
            if spec.get("draft_model_path"):
                flags["speculative-draft-model-path"] = spec["draft_model_path"]
            if spec.get("num_draft_tokens"):
                flags["speculative-num-draft-tokens"] = spec["num_draft_tokens"]
        if pd_split and pd_role:
            flags["disaggregation-mode"] = pd_role
            flags["disaggregation-transfer-backend"] = pd_cfg.get("transfer", "nixl")
        elif kv_sharing:
            flags["enable-hierarchical-cache"] = True
    elif engine_name == "tensorrt_llm":
        if ctx:
            flags["max_seq_len"] = ctx
        if tp > 1:
            flags["tp_size"] = tp
        if pp > 1:
            flags["pp_size"] = pp
        if parser:
            flags["tool_parser"] = parser
        if max_conc:
            flags["max_batch_size"] = max_conc
    elif engine_name == "mlx":
        if ctx:
            flags["max-tokens"] = ctx
    return flags


def flags_to_argv(flags: Dict[str, Any]) -> List[str]:
    """``{"ctx-size": 4096, "jinja": True, "control-vector": ["a","b"]}`` -> argv."""
    argv: List[str] = []
    for key, value in flags.items():
        name = f"--{key}" if not key.startswith("-") else key
        if value is True:
            argv.append(name)
        elif value is False or value is None:
            continue
        elif isinstance(value, list):
            for item in value:
                argv.extend([name, str(item)])
        else:
            argv.extend([name, str(value)])
    return argv


def _json(obj: Dict[str, Any]) -> str:
    import json

    return json.dumps(obj, separators=(",", ":"))
