"""
Placement / dry run honour what engines really allocate (FULL-TEST #10, #13).

vLLM ``gpu-memory-utilization`` and SGLang ``mem-fraction-static`` claim a share of the
device, TensorRT-LLM ``kv_cache_free_gpu_memory_fraction`` a share of what weights leave,
and the agent launches llama.cpp with 4 slots x context unless told otherwise.
"""

from __future__ import annotations

import uuid

from app.services.engines.vram import (
    GIB,
    device_memory_fraction,
    device_need_bytes,
    estimate_vram,
    fraction_base_bytes,
    llama_cpp_kv_cells,
    llama_cpp_ubatch,
)
from app.services.scheduler.core import DeviceView, PlacementRequest

H200 = 141 * GIB


def _estimate(engine: str, **kw):
    return estimate_vram(
        engine_name=engine,
        weights_bytes=kw.pop("weights", 30 * GIB),
        parameter_count=None,
        quantization=None,
        config={"n_layers": 64, "n_kv_heads": 8, "head_dim": 128},
        context_size=kw.pop("context_size", 8192),
        **kw,
    )


def test_vllm_gpu_memory_utilization_claims_its_share_of_the_device():
    est = _estimate("vllm")
    fraction = device_memory_fraction("vllm", {"gpu-memory-utilization": 0.38})
    assert fraction == 0.38
    need = device_need_bytes("vllm", fraction, est, H200)
    assert need == int(H200 * 0.38) > est.per_gpu_bytes  # 53.6 GiB, not the ~35 GiB estimate
    # SGLang default mem-fraction-static
    assert device_need_bytes("sglang", device_memory_fraction("sglang", {}), est, H200) == int(
        H200 * 0.88
    )
    # No fraction (llama.cpp): the estimate
    assert device_need_bytes("llama_cpp", None, est, H200) == est.per_gpu_bytes


def test_tensorrt_llm_fraction_is_of_the_memory_left_after_weights():
    est = _estimate("tensorrt_llm", weights=2 * GIB)
    fraction = device_memory_fraction("tensorrt_llm", {"kv_cache_free_gpu_memory_fraction": 0.05})
    base = fraction_base_bytes("tensorrt_llm", est)
    assert base == est.per_gpu_non_kv_bytes and fraction_base_bytes("vllm", est) == 0
    need = device_need_bytes("tensorrt_llm", fraction, est, H200)
    assert need == max(est.per_gpu_bytes, base + int((H200 - base) * 0.05))


def test_scheduler_need_for_uses_the_fraction_base():
    dev = DeviceView(
        id=uuid.uuid4(),
        node_id=uuid.uuid4(),
        vendor="nvidia",
        index=0,
        name="H200",
        vram_total_bytes=H200,
        health="healthy",
    )
    req = PlacementRequest(need_bytes_per_device=10 * GIB, min_device_fraction=0.5)
    assert req.need_for(dev) == int(H200 * 0.5)
    req = PlacementRequest(
        need_bytes_per_device=10 * GIB, min_device_fraction=0.5, fraction_base_bytes=41 * GIB
    )
    assert req.need_for(dev) == 41 * GIB + 50 * GIB
    sliced = PlacementRequest(
        need_bytes_per_device=10 * GIB, min_device_fraction=0.5, slice_vram_bytes=8 * GIB
    )
    assert sliced.need_for(dev) == 8 * GIB  # a slice is the reservation


def test_llama_cpp_kv_cells_mirror_the_agent_launch():
    assert llama_cpp_kv_cells(None, {}, None) == (4096 * 4, 4)  # agent defaults
    assert llama_cpp_kv_cells(8192, {}, None) == (8192 * 4, 4)
    assert llama_cpp_kv_cells(8192, {"parallel": 2}, None) == (8192 * 2, 2)
    assert llama_cpp_kv_cells(8192, {"parallel": 2}, 6) == (8192 * 6, 6)  # max_concurrency wins
    assert llama_cpp_kv_cells(8192, {"ctx-size": 16384, "parallel": 4}, None) == (16384, 4)
    assert llama_cpp_kv_cells(8192, {"c": 32768}, 2) == (32768, 2)
    assert llama_cpp_ubatch({}) == 512
    assert llama_cpp_ubatch({"ubatch-size": 2048}) == 2048
    assert llama_cpp_ubatch({"batch-size": 256}) == 256
    est = _estimate("llama_cpp", weights=5 * GIB, kv_cells=8192 * 4, activation_scale=4.0)
    one = _estimate("llama_cpp", weights=5 * GIB, concurrency=1)
    assert est.kv_cache_bytes == 4 * one.kv_cache_bytes
    assert est.activation_bytes == 4 * one.activation_bytes
