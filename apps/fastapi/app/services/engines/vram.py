"""
VRAM estimator (pure functions).

    total = weights + kv_cache(context, concurrency) + activations + engine overhead

* weights: the size of the weight files (GGUF / safetensors are the bytes the
  engine maps), else ``parameter_count × bytes_per_param(quantization)``.
* KV cache per token = 2 (K and V) × n_layers × n_kv_heads × head_dim × bytes
  per element (``f16`` default; ``q8_0`` 1, ``q4_0`` 0.5 for llama.cpp
  ``--cache-type-k/v``); multiplied by the context size and the number of
  concurrent sequences (``SCHEDULER_KV_BYTES_PER_TOKEN_FALLBACK`` when the
  model shape is unknown). Paged-KV engines (vLLM, SGLang, TensorRT-LLM)
  need one full-context sequence; they claim a fraction of the device
  (``device_memory_fraction``) and fit concurrency inside it.
* activations / compute buffers: 5 % of weights, at least 256 MiB (llama.cpp: times
  ``ubatch / 512``).
* llama.cpp KV: the agent launches ``--parallel`` slots (4 by default) with
  ``--ctx-size`` = context x slots unless ``ctx-size`` is set (``llama_cpp_kv_cells``).
* placement: engines that pre-allocate claim ``device_need_bytes`` per device
  (vLLM ``gpu-memory-utilization`` / SGLang ``mem-fraction-static`` x device VRAM,
  TensorRT-LLM ``kv_cache_free_gpu_memory_fraction`` of what weights leave).
* engine overhead: CUDA graphs / allocator reserve per engine.

Tensor parallel splits weights and KV across ``tp_size`` GPUs; pipeline
parallel splits layers across ``pp_size``; ``per_gpu_bytes`` gives what one
device must hold.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

MIB = 1024 * 1024
GIB = 1024 * MIB

_BYTES_PER_PARAM = {
    "F32": 4.0,
    "BF16": 2.0,
    "F16": 2.0,
    "FP16": 2.0,
    "FP8": 1.0,
    "Q8_0": 1.06,
    "INT8": 1.0,
    "8BIT": 1.06,
    "Q6_K": 0.82,
    "6BIT": 0.80,
    "Q5_K_M": 0.71,
    "Q5_K_S": 0.69,
    "Q5_0": 0.69,
    "Q5_1": 0.75,
    "Q4_K_M": 0.60,
    "Q4_K_S": 0.57,
    "Q4_0": 0.56,
    "Q4_1": 0.62,
    "IQ4_XS": 0.54,
    "IQ4_NL": 0.56,
    "AWQ": 0.56,
    "GPTQ": 0.56,
    "NF4": 0.56,
    "INT4": 0.52,
    "4BIT": 0.56,
    "MXFP4": 0.53,
    "Q3_K_M": 0.49,
    "Q3_K_S": 0.44,
    "Q3_K_L": 0.53,
    "3BIT": 0.44,
    "IQ3_M": 0.46,
    "IQ3_S": 0.44,
    "Q2_K": 0.39,
    "IQ2_M": 0.35,
    "IQ2_S": 0.32,
    "IQ2_XS": 0.30,
    "IQ2_XXS": 0.27,
    "IQ1_M": 0.22,
    "IQ1_S": 0.20,
}

_KV_BYTES_PER_ELEM = {
    "f16": 2.0,
    "bf16": 2.0,
    "f32": 4.0,
    "q8_0": 1.06,
    "q4_0": 0.56,
    "fp8": 1.0,
}

_ENGINE_OVERHEAD = {
    "llama_cpp": 192 * MIB,
    "vllm": 1536 * MIB,
    "vllm_omni": 2048 * MIB,
    "sglang": 1536 * MIB,
    "sglang_diffusion": 1024 * MIB,
    "tensorrt_llm": 2048 * MIB,
    "mlx": 128 * MIB,
    "mlx_audio": 128 * MIB,
    "whisper_cpp": 64 * MIB,
    "stable_diffusion_cpp": 256 * MIB,
    "custom": 512 * MIB,
}


# Engines with a paged KV cache sized from a memory fraction: they need room
# for one full-context sequence; extra concurrency comes out of the fraction.
PAGED_KV_ENGINES = frozenset({"vllm", "vllm_omni", "sglang", "tensorrt_llm"})

# Flag (and default) that sets the share of the device such an engine claims.
_MEMORY_FRACTION_FLAGS = {
    "vllm": ("gpu-memory-utilization", 0.9),
    "vllm_omni": ("gpu-memory-utilization", 0.9),
    "sglang": ("mem-fraction-static", 0.88),
    "tensorrt_llm": ("kv_cache_free_gpu_memory_fraction", 0.9),
}


# TensorRT-LLM's fraction applies to the memory left after weights / runtime, not the device.
_FRACTION_OF_FREE_ENGINES = frozenset({"tensorrt_llm"})

# The node agent's llama.cpp launch (gpudeck-engines ``llama_cpp.rs``): ``--parallel``
# defaults to 4 slots and ``--ctx-size`` = context per slot x slots unless the flags set it.
LLAMA_CPP_DEFAULT_SLOTS = 4
LLAMA_CPP_DEFAULT_CONTEXT = 4096
LLAMA_CPP_DEFAULT_UBATCH = 512


def _int_flag(flags: Dict[str, Any], *names: str) -> Optional[int]:
    for name in names:
        value = flags.get(name)
        if isinstance(value, bool):
            continue
        try:
            number = int(value) if value is not None else None
        except (TypeError, ValueError):
            number = None
        if number and number > 0:
            return number
    return None


def llama_cpp_kv_cells(
    context_size: Optional[int], flags: Dict[str, Any], max_concurrency: Optional[int]
) -> "tuple[int, int]":
    """
    ``(KV cells, slots)`` llama-server allocates, mirroring the agent's launch: slots =
    ``max_concurrency`` (traffic / preset), else ``parallel``, else 4; an explicit
    ``ctx-size`` is the total, otherwise ``ctx-size`` = context per slot x slots (slots
    share one buffer either way, ``--kv-unified`` or not).
    """
    slots = (
        max_concurrency
        if isinstance(max_concurrency, int) and max_concurrency > 0
        else (_int_flag(flags, "parallel", "np") or LLAMA_CPP_DEFAULT_SLOTS)
    )
    total = _int_flag(flags, "ctx-size", "c")
    if total:
        return total, slots
    return int(context_size or LLAMA_CPP_DEFAULT_CONTEXT) * slots, slots


def llama_cpp_ubatch(flags: Dict[str, Any]) -> int:
    """Physical batch (``--ubatch-size``) that sizes llama.cpp's compute buffers."""
    ub = _int_flag(flags, "ubatch-size", "ub")
    if ub:
        return ub
    batch = _int_flag(flags, "batch-size", "b")
    return min(batch, LLAMA_CPP_DEFAULT_UBATCH) if batch else LLAMA_CPP_DEFAULT_UBATCH


def device_memory_fraction(engine_name: str, flags: Dict[str, Any]) -> Optional[float]:
    """Share of a device the engine pre-allocates (None = sized by the estimate)."""
    spec = _MEMORY_FRACTION_FLAGS.get(engine_name)
    if spec is None:
        return None
    flag, default = spec
    try:
        value = float(flags.get(flag, default))
    except (TypeError, ValueError):
        value = default
    return min(max(value, 0.05), 1.0)


@dataclass(frozen=True)
class VramEstimate:
    weights_bytes: int
    kv_cache_bytes: int
    activation_bytes: int
    overhead_bytes: int
    context_size: int
    concurrency: int
    kv_bytes_per_token: int
    tp_size: int = 1
    pp_size: int = 1

    @property
    def total_bytes(self) -> int:
        return (
            self.weights_bytes + self.kv_cache_bytes + self.activation_bytes + self.overhead_bytes
        )

    @property
    def per_gpu_non_kv_bytes(self) -> int:
        """Weights + activations per device plus the engine overhead (no KV cache)."""
        shards = max(self.tp_size, 1) * max(self.pp_size, 1)
        return (self.weights_bytes + self.activation_bytes + shards - 1) // shards + (
            self.overhead_bytes
        )

    @property
    def per_gpu_bytes(self) -> int:
        """Bytes one device holds under TP × PP (overhead is per process/device)."""
        shards = max(self.tp_size, 1) * max(self.pp_size, 1)
        return (
            self.weights_bytes + self.kv_cache_bytes + self.activation_bytes + shards - 1
        ) // shards + self.overhead_bytes

    def as_dict(self) -> Dict[str, int]:
        return {
            "weights_bytes": self.weights_bytes,
            "kv_cache_bytes": self.kv_cache_bytes,
            "activation_bytes": self.activation_bytes,
            "overhead_bytes": self.overhead_bytes,
            "total_bytes": self.total_bytes,
            "per_gpu_bytes": self.per_gpu_bytes,
            "context_size": self.context_size,
            "concurrency": self.concurrency,
            "kv_bytes_per_token": self.kv_bytes_per_token,
        }


def fraction_base_bytes(engine_name: str, estimate: "VramEstimate") -> int:
    """What the memory fraction is applied on top of: 0 (share of the whole device) for
    vLLM / SGLang; weights + runtime for TensorRT-LLM (share of what is left)."""
    return estimate.per_gpu_non_kv_bytes if engine_name in _FRACTION_OF_FREE_ENGINES else 0


def device_need_bytes(
    engine_name: str,
    fraction: Optional[float],
    estimate: "VramEstimate",
    device_total_bytes: int,
) -> int:
    """Bytes one instance holds on a device of ``device_total_bytes`` (no slice): the
    estimate, or what a pre-allocating engine claims (``fraction`` of the device, or of the
    memory left after weights for TensorRT-LLM) when that is larger."""
    need = estimate.per_gpu_bytes
    if not fraction or device_total_bytes <= 0:
        return need
    base = fraction_base_bytes(engine_name, estimate)
    return max(need, base + int(max(device_total_bytes - base, 0) * float(fraction)))


def bytes_per_param(quantization: Optional[str], default: float = 2.0) -> float:
    if not quantization:
        return default
    return _BYTES_PER_PARAM.get(str(quantization).upper(), default)


def kv_bytes_per_token(
    config: Dict[str, Any], kv_dtype: str = "f16", fallback: int = 131072
) -> int:
    """2 × layers × kv_heads × head_dim × elem bytes; ``fallback`` when the shape is unknown."""
    n_layers = config.get("n_layers")
    n_kv = config.get("n_kv_heads") or config.get("n_heads")
    head_dim = config.get("head_dim")
    if not head_dim and config.get("hidden_size") and config.get("n_heads"):
        head_dim = int(config["hidden_size"]) // int(config["n_heads"])
    if not (n_layers and n_kv and head_dim):
        return int(fallback)
    elem = _KV_BYTES_PER_ELEM.get(str(kv_dtype).lower(), 2.0)
    return int(round(2 * int(n_layers) * int(n_kv) * int(head_dim) * elem))


def estimate_vram(
    *,
    engine_name: str,
    weights_bytes: Optional[int],
    parameter_count: Optional[int],
    quantization: Optional[str],
    config: Dict[str, Any],
    context_size: Optional[int],
    concurrency: int = 1,
    kv_dtype: str = "f16",
    tp_size: int = 1,
    pp_size: int = 1,
    kv_fallback_bytes_per_token: int = 131072,
    default_context: int = 4096,
    kv_cells: Optional[int] = None,
    activation_scale: float = 1.0,
) -> VramEstimate:
    """
    Total VRAM an instance needs. Inputs come from model_versions + deployment.

    ``kv_cells`` overrides context x concurrency as the number of KV-cache tokens the
    engine allocates (llama.cpp: ``llama_cpp_kv_cells``); ``activation_scale`` scales the
    compute buffers (llama.cpp: ``ubatch / 512``).
    """
    if weights_bytes and weights_bytes > 0:
        weights = int(weights_bytes)
    elif parameter_count:
        weights = int(parameter_count * bytes_per_param(quantization))
    else:
        weights = 0
    ctx = int(context_size or config.get("max_position_embeddings") or default_context)
    ctx = max(ctx, 1)
    conc = max(int(concurrency or 1), 1)
    per_token = kv_bytes_per_token(config, kv_dtype, kv_fallback_bytes_per_token)
    model_type_hint = config.get("model_type_hint")
    if model_type_hint in ("embedding", "rerank", "stt", "tts", "image", "video"):
        # No autoregressive KV growth: a single context worth is plenty.
        kv = per_token * min(ctx, 8192)
    elif kv_cells is not None:
        kv = per_token * max(int(kv_cells), 1)
    elif engine_name in PAGED_KV_ENGINES:
        kv = per_token * ctx
    else:
        kv = per_token * ctx * conc
    activations = int(max(int(weights * 0.05), 256 * MIB) * max(float(activation_scale), 1.0))
    overhead = _ENGINE_OVERHEAD.get(engine_name, 512 * MIB)
    return VramEstimate(
        weights_bytes=weights,
        kv_cache_bytes=int(kv),
        activation_bytes=activations,
        overhead_bytes=overhead,
        context_size=ctx,
        concurrency=conc,
        kv_bytes_per_token=per_token,
        tp_size=max(tp_size, 1),
        pp_size=max(pp_size, 1),
    )


def max_context_for_budget(
    budget_bytes: int,
    weights_bytes: int,
    per_token: int,
    concurrency: int = 1,
    overhead: int = 512 * MIB,
) -> int:
    """Largest context that fits in ``budget_bytes`` (used by compat warnings)."""
    free = budget_bytes - weights_bytes - overhead - max(int(weights_bytes * 0.05), 256 * MIB)
    if free <= 0 or per_token <= 0:
        return 0
    return int(free // (per_token * max(concurrency, 1)))
