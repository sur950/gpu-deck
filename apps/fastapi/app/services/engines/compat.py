"""
Compatibility checks and auto engine pick (pure functions).

``check_compat`` answers "can model version M run on engine version E (on
vendor V / OS O, with this GPU shape)?" with a result, a score for ranking and
a list of ``{code, message, severity}`` reasons, plus the VRAM estimate.

Checks, in order:

1. engine enabled and version available (deprecated = warning)
2. model type supported by the engine (the model-type table)
3. weight format supported (gguf / safetensors / mlx / pickle / other)
4. target OS / architecture / vendor supported, platform artifact or image present,
   vendor-specific driver minimum (see ``DEFAULT_MIN_DRIVER``)
5. pickle policy (blocked version), trust_remote_code requirement
6. parallelism: tp/pp only on engines with the capability, gpu_count = tp × pp
7. PD split / speculative decoding / KV sharing / control vectors / slices capability
8. VRAM: estimate vs the device(s) (unknown when no device info is given)

Score: base preference per (model type, format, vendor) + bonuses/penalties.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.services.engines.presets import (
    merge_launch_flags,
    preset_concurrency,
    preset_flags,
)
from app.services.engines.vram import VramEstimate, estimate_vram

# "Model type -> Engines" table (the engines rows seed the same sets).
MODEL_TYPE_ENGINES: Dict[str, List[str]] = {
    "chat": ["llama_cpp", "vllm", "sglang", "tensorrt_llm", "mlx"],
    "thinking": ["llama_cpp", "vllm", "sglang", "tensorrt_llm", "mlx"],
    "vision": ["llama_cpp", "vllm", "sglang", "mlx"],
    "embedding": ["llama_cpp", "vllm", "sglang"],
    "rerank": ["llama_cpp", "vllm"],
    "stt": ["whisper_cpp", "mlx_audio"],
    "tts": ["vllm_omni", "mlx_audio"],
    "image": ["stable_diffusion_cpp", "vllm_omni", "sglang_diffusion"],
    "video": ["vllm_omni", "sglang_diffusion"],
}

# Preference score per (format, engine); higher = better default pick.
_FORMAT_PREFERENCE: Dict[str, Dict[str, int]] = {
    "gguf": {
        "llama_cpp": 100,
        "whisper_cpp": 100,
        "stable_diffusion_cpp": 95,
        "custom": 40,
    },
    "safetensors": {
        "vllm": 95,
        "sglang": 90,
        "tensorrt_llm": 85,
        "mlx": 92,
        "mlx_audio": 90,
        "vllm_omni": 90,
        "sglang_diffusion": 85,
        "stable_diffusion_cpp": 70,
        "custom": 40,
    },
    "mlx": {"mlx": 100, "mlx_audio": 100, "custom": 40},
    "pickle": {"vllm": 60, "sglang": 55, "custom": 40},
    "other": {"whisper_cpp": 60, "custom": 40},
}

_VENDOR_OS = {"apple": "macos"}

# Driver minimums are vendor specific; a minimum is only ever compared with a version of the
# same kind. What is compared, per device vendor:
#   nvidia -> NVIDIA kernel driver (``devices.driver_version``, e.g. "550.54.15")
#   amd    -> ROCm version (``devices.runtime_version``, e.g. "6.2.0" or "ROCm 6.3.1"); the
#             amdgpu kernel driver version ("6.10.5") is not comparable across ROCm releases
#   others (apple, intel, hygon, metax, ...) -> not checked
# Where the minimum comes from, first hit wins per vendor:
#   1. ``engine_versions.requirements["min_driver"][vendor]`` (e.g. {"nvidia": "550", "amd": "6.2"})
#   2. nvidia: the ``engine_versions.min_driver_version`` column (always the NVIDIA driver);
#      amd: ``engine_versions.requirements["rocm"]`` (">=6.1")
#   3. ``DEFAULT_MIN_DRIVER[engine][vendor]`` below
# A missing or unparseable version on the target is a ``driver_unverified`` warning, never a
# rejection (and does not change the result, so engines with minimums are not ranked below
# engines without one).
DEFAULT_MIN_DRIVER: Dict[str, Dict[str, str]] = {
    # CUDA 12.1+ images: R535 (LTS, CUDA 12.2). ROCm images start at 6.1.
    "vllm": {"nvidia": "535", "amd": "6.1"},
    "vllm_omni": {"nvidia": "535"},
    # CUDA 12.x images: R535. ROCm images start at 6.2.
    "sglang": {"nvidia": "535", "amd": "6.2"},
    "sglang_diffusion": {"nvidia": "535", "amd": "6.2"},
    # NVIDIA only; CUDA 12.4+ needs R550.
    "tensorrt_llm": {"nvidia": "550"},
    # CUDA 12 builds run on R525+ (CUDA minor-version compatibility); HIP builds need ROCm 6.0.
    "llama_cpp": {"nvidia": "525", "amd": "6.0"},
    "whisper_cpp": {"nvidia": "525", "amd": "6.0"},
    "stable_diffusion_cpp": {"nvidia": "525", "amd": "6.0"},
}

# Which target field carries the comparable version, and how to name it in messages.
_DRIVER_KIND: Dict[str, Tuple[str, str]] = {
    "nvidia": ("driver_version", "NVIDIA driver"),
    "amd": ("runtime_version", "ROCm"),
}

_VERSION_RE = re.compile(r"\d+(?:\.\d+){0,2}")


@dataclass
class EngineInfo:
    """Engine + engine version rows flattened for the check."""

    engine_id: str
    engine_version_id: str
    name: str
    version: str
    runtime: str
    is_enabled: bool
    version_status: str
    supported_model_types: Sequence[str]
    supported_vendors: Sequence[str]
    supported_os: Sequence[str]
    supported_formats: Sequence[str]
    capabilities: Dict[str, Any] = field(default_factory=dict)
    image: Optional[str] = None
    artifacts: Dict[str, Any] = field(default_factory=dict)
    default_launch_flags: Dict[str, Any] = field(default_factory=dict)
    launch_template: Dict[str, Any] = field(default_factory=dict)
    min_driver_version: Optional[str] = None
    requirements: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelVersionInfo:
    model_version_id: str
    model_type: str
    format: str
    status: str
    total_size_bytes: int
    parameter_count: Optional[int]
    quantization: Optional[str]
    config: Dict[str, Any] = field(default_factory=dict)
    allow_pickle: bool = False
    trust_remote_code: bool = False
    family: Optional[str] = None
    default_launch_flags: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Target:
    """Where it would run. Any field may be None (= unknown, not a failure)."""

    os: Optional[str] = None
    arch: Optional[str] = None
    vendor: Optional[str] = None
    device_vram_bytes: Optional[Sequence[int]] = None  # candidate devices (free or total)
    driver_version: Optional[str] = None  # NVIDIA: kernel driver (lowest of the vendor's devices)
    runtime_version: Optional[str] = None  # AMD: ROCm version (lowest of the vendor's devices)
    node_engines: Optional[Sequence[str]] = None  # engines the node agent reports as installed


@dataclass
class Shape:
    gpu_count: int = 1
    tp_size: int = 1
    pp_size: int = 1
    context_size: Optional[int] = None
    preset: Optional[str] = None
    slice_vram_bytes: Optional[int] = None
    pd_split: bool = False
    spec_decoding: bool = False
    kv_sharing: bool = False
    control_vectors: bool = False
    launch_flags: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CompatOutcome:
    result: str  # compatible | incompatible | unknown
    score: int
    reasons: List[Dict[str, str]]
    estimate: Optional[VramEstimate]
    engine: EngineInfo

    @property
    def estimated_vram_bytes(self) -> Optional[int]:
        return self.estimate.per_gpu_bytes if self.estimate else None

    def reason_codes(self) -> List[str]:
        return [r["code"] for r in self.reasons]


def _reason(code: str, message: str, severity: str = "error") -> Dict[str, str]:
    return {"code": code, "message": message, "severity": severity}


def _platform_key(target: Target) -> Optional[str]:
    if target.os and target.arch:
        return f"{target.os}/{target.arch}"
    return None


def version_tuple(value: Optional[str]) -> Optional[Tuple[int, ...]]:
    """First dotted number in ``value`` ("ROCm 6.3.1" -> (6, 3, 1), ">=12.1" -> (12, 1))."""
    if value is None:
        return None
    m = _VERSION_RE.search(str(value))
    if not m:
        return None
    return tuple(int(p) for p in m.group(0).split("."))


def min_driver_for(engine: EngineInfo, vendor: Optional[str]) -> Optional[str]:
    """The engine version's minimum driver / runtime for ``vendor`` (None = not checked)."""
    if not vendor or vendor not in _DRIVER_KIND:
        return None
    req = engine.requirements or {}
    explicit = req.get("min_driver")
    if isinstance(explicit, dict) and explicit.get(vendor):
        return str(explicit[vendor])
    if vendor == "nvidia" and engine.min_driver_version:
        return str(engine.min_driver_version)
    if vendor == "amd" and req.get("rocm"):
        rocm = version_tuple(str(req["rocm"]))
        if rocm:
            return ".".join(str(p) for p in rocm)
    return DEFAULT_MIN_DRIVER.get(engine.name, {}).get(vendor)


def _driver_ok(min_version: Optional[str], have: Optional[str]) -> Optional[bool]:
    """True/False when both versions parse, None when either is missing or unparseable."""
    want, got = version_tuple(min_version), version_tuple(have)
    if want is None or got is None:
        return None
    return got >= want


def _driver_reason(engine: EngineInfo, target: Target) -> Optional[Dict[str, str]]:
    vendor = target.vendor
    if not vendor or vendor not in engine.supported_vendors:
        return None
    need = min_driver_for(engine, vendor)
    if not need:
        return None
    attr, label = _DRIVER_KIND[vendor]
    have = getattr(target, attr)
    ok = _driver_ok(need, have)
    if ok is False:
        return _reason("driver_too_old", f"{label} {have} < required {need} for {engine.name}")
    if ok is None:
        return _reason(
            "driver_unverified",
            f"{label} version unknown on the target; {engine.name} needs >= {need}",
            "warning",
        )
    return None


def check_compat(
    model: ModelVersionInfo,
    engine: EngineInfo,
    target: Optional[Target] = None,
    shape: Optional[Shape] = None,
    *,
    headroom_pct: int = 10,
    kv_fallback: int = 131072,
) -> CompatOutcome:
    """Pure compatibility check; never raises."""
    target = target or Target()
    shape = shape or Shape()
    reasons: List[Dict[str, str]] = []
    hard_fail = False
    unknown = False
    caps = engine.capabilities or {}

    # 1. availability
    if not engine.is_enabled:
        reasons.append(_reason("engine_disabled", f"{engine.name} is disabled"))
        hard_fail = True
    if engine.version_status == "disabled":
        reasons.append(
            _reason("engine_version_disabled", f"{engine.name} {engine.version} is disabled")
        )
        hard_fail = True
    elif engine.version_status == "deprecated":
        reasons.append(
            _reason(
                "engine_version_deprecated",
                f"{engine.name} {engine.version} is deprecated",
                "warning",
            )
        )

    # 2. model type
    allowed = MODEL_TYPE_ENGINES.get(model.model_type, [])
    if model.model_type not in engine.supported_model_types or (
        engine.name != "custom" and engine.name not in allowed
    ):
        reasons.append(
            _reason(
                "model_type_unsupported",
                f"{engine.name} does not serve {model.model_type} models",
            )
        )
        hard_fail = True

    # 3. format
    fmt = model.format
    if fmt not in engine.supported_formats:
        reasons.append(_reason("format_unsupported", f"{engine.name} cannot load {fmt} weights"))
        hard_fail = True

    # 4. platform
    if target.vendor and target.vendor not in engine.supported_vendors:
        reasons.append(
            _reason(
                "vendor_unsupported",
                f"{engine.name} does not run on {target.vendor} devices",
            )
        )
        hard_fail = True
    os_ = target.os or _VENDOR_OS.get(target.vendor or "")
    if os_ and os_ not in engine.supported_os:
        reasons.append(_reason("os_unsupported", f"{engine.name} does not run on {os_}"))
        hard_fail = True
    key = _platform_key(target)
    if engine.runtime == "container":
        if not engine.image:
            reasons.append(
                _reason(
                    "image_missing",
                    f"{engine.name} {engine.version} has no container image",
                )
            )
            hard_fail = True
        if os_ == "macos":
            reasons.append(
                _reason(
                    "container_on_macos",
                    "container engines are not supported on macOS nodes",
                )
            )
            hard_fail = True
    else:
        if key and engine.artifacts and key not in engine.artifacts:
            installed = engine.name in (target.node_engines or [])
            if not installed:
                reasons.append(_reason("artifact_missing", f"no {engine.name} build for {key}"))
                hard_fail = True
    built_for = (engine.requirements or {}).get("vendors")
    if isinstance(built_for, list) and built_for and target.vendor:
        if target.vendor not in built_for:
            reasons.append(
                _reason(
                    "vendor_unsupported",
                    f"{engine.name} {engine.version} is built for {', '.join(built_for)} only",
                )
            )
            hard_fail = True
    if target.vendor and target.vendor != "nvidia" and engine.name == "tensorrt_llm":
        reasons.append(_reason("vendor_unsupported", "TensorRT-LLM is NVIDIA only"))
        hard_fail = True
    drv = _driver_reason(engine, target)
    if drv is not None:
        reasons.append(drv)
        hard_fail = hard_fail or drv["severity"] == "error"

    # 5. security policy
    if model.status == "blocked":
        reasons.append(
            _reason(
                "model_version_blocked",
                "model version is blocked (pickle policy or hash mismatch)",
            )
        )
        hard_fail = True
    if fmt == "pickle" and not model.allow_pickle:
        reasons.append(
            _reason(
                "pickle_blocked",
                "pickle weights are blocked unless allow_pickle is set",
            )
        )
        hard_fail = True
    if model.trust_remote_code and engine.name in (
        "llama_cpp",
        "mlx",
        "whisper_cpp",
        "stable_diffusion_cpp",
    ):
        reasons.append(
            _reason(
                "trust_remote_code_ignored",
                f"{engine.name} never runs remote code",
                "info",
            )
        )

    # 6. parallelism
    tp, pp, gpus = max(shape.tp_size, 1), max(shape.pp_size, 1), max(shape.gpu_count, 1)
    if tp > 1 and not caps.get("tensor_parallel"):
        reasons.append(_reason("tp_unsupported", f"{engine.name} has no tensor parallel"))
        hard_fail = True
    if pp > 1 and not caps.get("pipeline_parallel"):
        reasons.append(_reason("pp_unsupported", f"{engine.name} has no pipeline parallel"))
        hard_fail = True
    if gpus != tp * pp:
        reasons.append(
            _reason(
                "gpu_count_mismatch",
                f"gpu_count {gpus} must equal tp_size × pp_size ({tp}×{pp})",
            )
        )
        hard_fail = True
    if gpus > 1 and not (caps.get("tensor_parallel") or caps.get("pipeline_parallel")):
        reasons.append(
            _reason("multi_gpu_unsupported", f"{engine.name} uses one device per replica")
        )
        hard_fail = True

    # 7. capability-gated features
    for flag, cap, label in (
        (shape.pd_split, "pd_split", "prefill/decode split"),
        (shape.spec_decoding, "speculative_decoding", "speculative decoding"),
        (shape.kv_sharing, "kv_sharing", "shared KV cache"),
        (shape.control_vectors, "control_vectors", "control vectors"),
        (shape.slice_vram_bytes is not None, "slices", "GPU slices"),
    ):
        if flag and not caps.get(cap):
            reasons.append(_reason(f"{cap}_unsupported", f"{engine.name} does not support {label}"))
            hard_fail = True

    # 8. VRAM
    flags = merge_launch_flags(
        engine.default_launch_flags,
        model.default_launch_flags,
        preset_flags(engine.name, shape.preset),
        shape.launch_flags,
    )
    conc = preset_concurrency(shape.preset, flags, engine.name)
    kv_dtype = str(flags.get("cache-type-k") or flags.get("kv-cache-dtype") or "f16")
    cfg = dict(model.config or {})
    cfg["model_type_hint"] = model.model_type
    estimate = estimate_vram(
        engine_name=engine.name,
        weights_bytes=model.total_size_bytes,
        parameter_count=model.parameter_count,
        quantization=model.quantization,
        config=cfg,
        context_size=shape.context_size,
        concurrency=conc,
        kv_dtype=kv_dtype,
        tp_size=tp,
        pp_size=pp,
        kv_fallback_bytes_per_token=kv_fallback,
    )
    need = estimate.per_gpu_bytes
    if shape.slice_vram_bytes is not None:
        budget = shape.slice_vram_bytes
        if need > budget:
            reasons.append(
                _reason(
                    "slice_too_small",
                    f"needs ~{need // (1 << 20)} MiB per GPU, slice is {budget // (1 << 20)} MiB",
                )
            )
            hard_fail = True
    if target.device_vram_bytes:
        vram = sorted(target.device_vram_bytes, reverse=True)
        usable = [int(v * (100 - headroom_pct) / 100) for v in vram]
        fits = [v for v in usable if v >= need]
        if len(fits) < gpus:
            reasons.append(
                _reason(
                    "insufficient_vram",
                    f"needs ~{need // (1 << 20)} MiB on {gpus} device(s); "
                    f"{len(fits)} device(s) have that much free after {headroom_pct}% headroom",
                )
            )
            hard_fail = True
        elif need > usable[0] * 0.8:
            reasons.append(
                _reason("vram_tight", "less than 20% VRAM left after loading", "warning")
            )
    else:
        unknown = True
        reasons.append(
            _reason("vram_unverified", "no device information: VRAM fit not checked", "info")
        )

    # Score
    score = _FORMAT_PREFERENCE.get(fmt, {}).get(engine.name, 50)
    if target.vendor == "apple" and engine.name in ("mlx", "mlx_audio"):
        score += 5
    if target.vendor == "nvidia" and engine.name in ("vllm", "tensorrt_llm"):
        score += 3
    if shape.preset == "high_throughput" and engine.name in (
        "vllm",
        "sglang",
        "tensorrt_llm",
    ):
        score += 5
    if shape.preset == "low_latency" and engine.name in (
        "llama_cpp",
        "mlx",
        "tensorrt_llm",
    ):
        score += 3
    if engine.version_status == "deprecated":
        score -= 10
    if engine.name == "custom":
        score = min(score, 40)
    if hard_fail:
        return CompatOutcome("incompatible", 0, reasons, estimate, engine)
    if unknown:
        return CompatOutcome("unknown", max(score - 5, 1), reasons, estimate, engine)
    return CompatOutcome("compatible", score, reasons, estimate, engine)


def rank(outcomes: Iterable[CompatOutcome]) -> List[CompatOutcome]:
    """Best first: compatible > unknown > incompatible, then score, then name for determinism."""
    order = {"compatible": 0, "unknown": 1, "incompatible": 2}
    return sorted(
        outcomes,
        key=lambda o: (
            order.get(o.result, 3),
            -o.score,
            o.engine.name,
            o.engine.version,
        ),
    )


def auto_pick(outcomes: Iterable[CompatOutcome]) -> Optional[CompatOutcome]:
    """The best non-incompatible outcome, or None."""
    ranked = rank(outcomes)
    for o in ranked:
        if o.result != "incompatible":
            return o
    return None
