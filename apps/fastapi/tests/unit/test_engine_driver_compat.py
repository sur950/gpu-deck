"""
Vendor-specific driver minimums in the engine compatibility check.

NVIDIA nodes compare the NVIDIA kernel driver, AMD nodes the ROCm version,
other vendors are not checked; a missing version is a warning, never a
rejection. Also covers the per-vendor node targets the engine service builds
(a mixed NVIDIA/AMD node never compares an NVIDIA minimum with an AMD driver).
"""

from types import SimpleNamespace

from app.services.engines import compat
from app.services.engines.seed import SEED_ENGINE_VERSIONS
from app.services.engines.service import _target_from_node, _targets_from_node
from app.services.engines.vram import GIB
from tests.unit.test_engines_vram import engine_info, model


def _seeded(name):
    """engine_info() with the seeded engine version's driver fields."""
    spec = next(s for s in SEED_ENGINE_VERSIONS if s["engine"] == name)
    info = engine_info(name)
    info.min_driver_version = spec.get("min_driver_version")
    info.requirements = dict(spec.get("requirements") or {})
    return info


def _rocm_build(name, rocm):
    """The seeded version as a ROCm image build (what an operator adds for AMD nodes)."""
    info = _seeded(name)
    info.requirements.pop("vendors", None)
    info.requirements["rocm"] = rocm
    return info


def _linux(vendor, driver=None, runtime=None):
    return compat.Target(
        os="linux",
        arch="x86_64",
        vendor=vendor,
        device_vram_bytes=[192 * GIB],
        driver_version=driver,
        runtime_version=runtime,
    )


def _check(name, target, fmt="safetensors"):
    return compat.check_compat(model(fmt=fmt), _seeded(name), target)


# ------------------------------------------------------------------ nvidia


def test_nvidia_driver_minimum_is_enforced():
    # The pinned vLLM / SGLang / TensorRT-LLM images are CUDA 13 builds: R580 or newer.
    ok = _check("vllm", _linux("nvidia", "580.65.06", "13.0"))
    assert ok.result == "compatible"
    assert not any(c.startswith("driver_") for c in ok.reason_codes())
    old = _check("vllm", _linux("nvidia", "570.124.06"))
    assert old.result == "incompatible"
    assert "driver_too_old" in old.reason_codes()
    assert any("NVIDIA driver 570.124.06 < required 580" in r["message"] for r in old.reasons)
    trt = _check("tensorrt_llm", _linux("nvidia", "575.57.08"))
    assert "driver_too_old" in trt.reason_codes()
    assert _check("tensorrt_llm", _linux("nvidia", "580.82.07")).result == "compatible"
    assert _check("sglang", _linux("nvidia", "580.65.06")).result == "compatible"


def test_cuda_only_images_are_not_offered_to_amd():
    amd = _check("vllm", _linux("amd", "6.10.5", "6.3.1"))
    assert amd.result == "incompatible" and "vendor_unsupported" in amd.reason_codes()


def test_nvidia_llama_cpp_default_minimum():
    assert compat.min_driver_for(_seeded("llama_cpp"), "nvidia") == "525"
    assert _check("llama_cpp", _linux("nvidia", "525.60.13"), "gguf").result == "compatible"
    assert (
        "driver_too_old" in _check("llama_cpp", _linux("nvidia", "470.82"), "gguf").reason_codes()
    )


# ------------------------------------------------------------------ amd


def test_amd_compares_rocm_not_the_nvidia_minimum():
    # amdgpu driver 6.10.5 used to be compared with NVIDIA's "535" and rejected.
    mi300x = _linux("amd", driver="6.10.5", runtime="ROCm 6.3.1")
    for name, rocm in (("vllm", ">=6.1"), ("sglang", ">=6.2")):
        out = compat.check_compat(model(fmt="safetensors"), _rocm_build(name, rocm), mi300x)
        assert out.result == "compatible", out.reasons
        assert "driver_too_old" not in out.reason_codes()
    assert _check("llama_cpp", mi300x, "gguf").result == "compatible"


def test_amd_rocm_minimum_is_enforced():
    vllm, sglang = _rocm_build("vllm", ">=6.1"), _rocm_build("sglang", ">=6.2")
    assert compat.min_driver_for(vllm, "amd") == "6.1"  # from requirements.rocm
    assert compat.min_driver_for(sglang, "amd") == "6.2"
    st = model(fmt="safetensors")
    old = compat.check_compat(st, sglang, _linux("amd", driver="6.7.0", runtime="6.1.2"))
    assert old.result == "incompatible"
    assert any("ROCm 6.1.2 < required 6.2" in r["message"] for r in old.reasons)
    assert compat.check_compat(st, vllm, _linux("amd", runtime="6.1.2")).result == "compatible"


def test_amd_tensorrt_is_vendor_unsupported_not_driver():
    out = _check("tensorrt_llm", _linux("amd", runtime="6.3.1"))
    assert "vendor_unsupported" in out.reason_codes()
    assert "driver_too_old" not in out.reason_codes()


def test_explicit_per_vendor_minimum_overrides_defaults():
    info = _seeded("vllm")
    info.requirements["min_driver"] = {"nvidia": "560", "amd": "6.4"}
    assert compat.min_driver_for(info, "nvidia") == "560"
    assert compat.min_driver_for(info, "amd") == "6.4"
    out = compat.check_compat(model(fmt="safetensors"), info, _linux("amd", runtime="6.3.1"))
    assert "driver_too_old" in out.reason_codes()


# --------------------------------------------------------- apple / others


def test_apple_and_other_vendors_are_not_checked():
    mac = compat.Target(
        os="macos",
        arch="aarch64",
        vendor="apple",
        device_vram_bytes=[48 * GIB],
        driver_version="macOS 27.2 (26B123)",
        runtime_version="Metal 4",
    )
    for name, fmt in (("mlx", "mlx"), ("llama_cpp", "gguf")):
        info = _seeded(name)
        info.min_driver_version = "535"  # NVIDIA-only number never applies to Apple
        assert compat.min_driver_for(info, "apple") is None
        out = compat.check_compat(model(fmt=fmt), info, mac)
        assert not any(c.startswith("driver_") for c in out.reason_codes())
    metax = _linux("metax", driver="2.20", runtime="MACA 2.29")
    out = _check("llama_cpp", metax, "gguf")
    assert out.result == "compatible"
    assert not any(c.startswith("driver_") for c in out.reason_codes())


# ---------------------------------------------------------------- unknown


def test_unknown_driver_is_a_warning_not_a_rejection():
    vllm_rocm = _rocm_build("vllm", ">=6.1")
    for target, info in (
        (_linux("nvidia"), _seeded("vllm")),
        (_linux("nvidia", "n/a"), _seeded("vllm")),
        (_linux("amd", driver="6.10.5"), vllm_rocm),
    ):
        out = compat.check_compat(model(fmt="safetensors"), info, target)
        assert out.result == "compatible", out.reasons
        reason = next(r for r in out.reasons if r["code"] == "driver_unverified")
        assert reason["severity"] == "warning"
    # Unknown driver must not rank vLLM below an engine without a minimum.
    assert _check("llama_cpp", _linux("nvidia"), "gguf").result == "compatible"


def test_version_tuple_parsing():
    assert compat.version_tuple("ROCm 6.3.1") == (6, 3, 1)
    assert compat.version_tuple(">=12.1") == (12, 1)
    assert compat.version_tuple("570.124.06") == (570, 124, 6)
    assert compat.version_tuple("") is None
    assert compat.version_tuple(None) is None


# ---------------------------------------------------------- node targets


def _dev(vendor, driver, runtime, vram=80 * GIB):
    return SimpleNamespace(
        vendor=vendor,
        driver_version=driver,
        runtime_version=runtime,
        vram_total_bytes=vram,
    )


_NODE = SimpleNamespace(os="linux", arch="x86_64", capabilities={})


def test_node_target_uses_the_vendors_own_versions_and_the_oldest_device():
    devices = [
        _dev("amd", "6.10.5", "6.3.1", 192 * GIB),
        _dev("nvidia", "590.44.01", "13.1"),
        _dev("nvidia", "580.65.06", "13.0"),
    ]
    targets = {t.vendor: t for t in _targets_from_node(_NODE, devices)}
    assert set(targets) == {"amd", "nvidia"}
    assert targets["nvidia"].driver_version == "580.65.06"
    assert targets["nvidia"].device_vram_bytes == [80 * GIB, 80 * GIB]
    assert targets["amd"].runtime_version == "6.3.1"
    assert targets["amd"].device_vram_bytes == [192 * GIB]
    for t in targets.values():
        assert _check("llama_cpp", t, "gguf").result == "compatible", t
    assert _check("vllm", targets["nvidia"]).result == "compatible"
    # A mixed node with no vendor picked compares nothing.
    mixed = _target_from_node(_NODE, devices, None)
    assert mixed.vendor is None and mixed.driver_version is None


def test_single_vendor_node_is_one_target():
    targets = _targets_from_node(_NODE, [_dev("amd", "6.10.5", "6.2.0")])
    assert len(targets) == 1 and targets[0].vendor == "amd"
    assert targets[0].runtime_version == "6.2.0"
