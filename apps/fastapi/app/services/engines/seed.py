"""
Built-in engine versions seeded on first boot (``001_init`` seeds the eleven
``engines`` rows but no ``engine_versions``).

Each entry is one pinned engine build with the launch template, health and
metrics config the node agent uses verbatim (``common.json#/$defs/EngineSpec``).
Template variables: ``port``, ``model_path``, ``model_dir``, ``context_size``,
``served_model_name``, ``tp_size``, ``pp_size``, ``device_indices``,
``cache_dir``, ``flags`` (expanded from ``launch.flags``).

llama.cpp runs in router mode (one ``llama-server --models-dir`` per node,
models loaded/unloaded through its admin API, agent-only); its exact
``/slots`` fields are pinned by the node agent.

Every native build is pinned to an upstream release asset (or source archive)
with its sha256 (GitHub release ``digest`` / PyPI ``digests.sha256``);
``app/services/engines/artifacts.py`` picks the build for the node's platform
and GPU (``<os>/<arch>[/cuda|rocm|vulkan]``). Container engines are pinned by
tag and index digest (``image:tag@sha256:...``) of their official images.
A node can always use an operator-installed engine instead (``engine_paths``).
"""

from __future__ import annotations

from typing import Any, Dict, List

ENGINE_IDS = {
    "llama_cpp": "00000000-0000-4000-8000-000000000001",
    "vllm": "00000000-0000-4000-8000-000000000002",
    "vllm_omni": "00000000-0000-4000-8000-000000000003",
    "sglang": "00000000-0000-4000-8000-000000000004",
    "sglang_diffusion": "00000000-0000-4000-8000-000000000005",
    "tensorrt_llm": "00000000-0000-4000-8000-000000000006",
    "mlx": "00000000-0000-4000-8000-000000000007",
    "mlx_audio": "00000000-0000-4000-8000-000000000008",
    "whisper_cpp": "00000000-0000-4000-8000-000000000009",
    "stable_diffusion_cpp": "00000000-0000-4000-8000-00000000000a",
    "custom": "00000000-0000-4000-8000-00000000000b",
}

_HEALTH_HTTP = {
    "path": "/health",
    "method": "GET",
    "interval_seconds": 5,
    "timeout_seconds": 3,
    "healthy_statuses": [200],
    "startup_timeout_seconds": 600,
    "failure_threshold": 3,
}
_METRICS_PROXY = {
    "path": None,
    "format": "none",
    "slots_path": None,
    "scrape_interval_seconds": 2,
    "derive_from_proxy": True,
    "metric_prefix": None,
}


def _prom(prefix: str, slots: "str | None" = None) -> Dict[str, Any]:
    return {
        "path": "/metrics",
        "format": "prometheus",
        "slots_path": slots,
        "scrape_interval_seconds": 2,
        "derive_from_proxy": False,
        "metric_prefix": prefix,
    }


# llama.cpp b8798 router mode as observed on a real node: what
# the scheduler / swap manager must assume. Mirrored into existing rows by migration 600.
LLAMACPP_METRICS = _prom("llamacpp:", "/slots")
LLAMACPP_ROUTER_FACTS: Dict[str, Any] = {
    "models_read_at_start": True,
    "unknown_model_load": "router_restart",
    "per_model_query_param": "model",
    "load_endpoint": "POST /models/load",
    "unload_endpoint": "POST /models/unload",
    "status_endpoint": "GET /models",
    "kv_cache_metrics": False,
    "prefer_one_router_per_model_group": True,
}

# llama.cpp release pinned for the built-in engine version (ggml-org/llama.cpp, release b12500).
# The CUDA builds need the matching cudart bundle (CUDA runtime + cuBLAS), extracted into the
# same folder; the agent unpacks ``parts`` after the main archive.
#
# Host glibc (``min_glibc``, checked by the agent before downloading; see
# app/services/engines/artifacts.py): upstream builds the x86_64 CPU and Vulkan tarballs on
# Ubuntu 22.04 (they need GLIBC_2.34 / GLIBCXX_3.4.30, checked with objdump on the b12500
# assets) and everything else (arm64, CUDA, ROCm) on Ubuntu 24.04 (GLIBC_2.38 /
# GLIBCXX_3.4.32: Ubuntu 24.04, Debian 13, RHEL 10 or newer). Older systems get, per
# accelerator: an older release still built on Ubuntu 22.04 (ROCm: b10375, the last release
# with an Ubuntu 22.04 ROCm build; it has the router flags the agent uses), else the official
# image. Images are published for some builds only; b12464 is the closest to b12500
# (ghcr.io/ggml-org/llama.cpp, index digests read from the registry API).
LLAMACPP_BUILD = "b12500"
LLAMACPP_IMAGE_BUILD = "b12464"
_LLAMA = "https://github.com/ggml-org/llama.cpp/releases/download/b12500/"
_LLAMA_ENTRY = "llama-b12500/llama-server"
_GLIBC_UBUNTU_22 = "2.34"
_GLIBC_UBUNTU_24 = "2.38"
_LLAMA_IMAGES = {
    "cpu": "server-b12464@sha256:5b43086e3ba7ea5794d72904654f6cd41224a9b1c6ac0398f4940ee380d30f23",
    "cuda12": "server-cuda-b12464"
    "@sha256:ae96e52f8b2106ea82bad786ee2d08bb9b0a8c93403df9c27ae1bf065a118089",
    "cuda13": "server-cuda13-b12464"
    "@sha256:d8d71f3b200080bfa67d65339cca8af7f3a506ede922323cc0dd35389a2a3534",
    "vulkan": "server-vulkan-b12464"
    "@sha256:5c73025f2072135ad37e306db9a57c685d8a349cfa5cb82b5c0f385b97567c94",
    "rocm": "server-rocm-b12464"
    "@sha256:7cff8bf82a43ba2901375eab4f78ba04753eded58ccf42b213707cbbbb0835df",
}


def _llama_image(flavour: str) -> Dict[str, Any]:
    """Official llama.cpp server image (CUDA 12.8.1 / 13.5.0, ROCm 7.2.1, Vulkan, CPU)."""
    return {
        "image": "ghcr.io/ggml-org/llama.cpp:" + _LLAMA_IMAGES[flavour],
        "entrypoint": "/app/llama-server",
        "version": LLAMACPP_IMAGE_BUILD,
    }


def _llama(asset: str, sha256: str, size: int, **extra: Any) -> Dict[str, Any]:
    return {
        "url": _LLAMA + asset,
        "sha256": sha256,
        "kind": "tarball",
        "entrypoint": _LLAMA_ENTRY,
        "size_bytes": size,
        **extra,
    }


def _cudart(asset: str, sha256: str, size: int) -> Dict[str, Any]:
    return {
        "url": _LLAMA + asset,
        "sha256": sha256,
        "kind": "tarball",
        "size_bytes": size,
        "dest": "llama-b12500",
        "strip_components": 1,
    }


LLAMACPP_ARTIFACTS: Dict[str, Any] = {
    "macos/aarch64": _llama(
        "llama-b12500-bin-macos-arm64.tar.gz",
        "afe4188c1ffab52b9b39ea688c8fa49f1012b826282b5c11eee5e9b9c757e1aa",
        17282621,
    ),
    "macos/x86_64": _llama(
        "llama-b12500-bin-macos-x64.tar.gz",
        "480a5ab691ccd1eb41c7a982852fd7b499789f32608e18a2d6efcb6ce49bb4e6",
        13054030,
    ),
    "linux/x86_64": _llama(
        "llama-b12500-bin-ubuntu-x64.tar.gz",
        "decd26836af2cb9e0925e3bd5c79f7aa3740df9f2dbe88bbc57b1b4249cfd7ea",
        23783691,
        min_glibc=_GLIBC_UBUNTU_22,
        container=_llama_image("cpu"),
    ),
    "linux/x86_64/cuda": _llama(
        "llama-b12500-bin-ubuntu-cuda-12.8-x64.tar.gz",
        "4c2f19bc448bff848967e27ce8e2957e0b479990c4228d30ce247f05b65063b8",
        222794518,
        min_driver_version="525.60.13",
        min_glibc=_GLIBC_UBUNTU_24,
        parts=[
            _cudart(
                "cudart-llama-b12500-bin-ubuntu-cuda-12.8-x64.tar.gz",
                "0fe823156bf1fbd52a8904909673d82520f0d852b4352ba640837c700b49eb87",
                594376034,
            )
        ],
        container=_llama_image("cuda12"),
    ),
    "linux/x86_64/rocm": _llama(
        "llama-b12500-bin-ubuntu-rocm-10.0-x64.tar.gz",
        "107927aacea6dfef270b4022b413a626fc2e82181317735ed947fc7214a7cb81",
        283461667,
        min_runtime_version="10.0",
        min_glibc=_GLIBC_UBUNTU_24,
        fallbacks=[
            {
                "url": "https://github.com/ggml-org/llama.cpp/releases/download/b10375/"
                "llama-b10375-bin-ubuntu-rocm-7.14-x64.tar.gz",
                "sha256": "712cee42f49d4ae627f621eaa352ccfcacf51547d0afc58ef4d1873c0d9d1e25",
                "kind": "tarball",
                "entrypoint": "llama-b10375/llama-server",
                "size_bytes": 204484498,
                "min_runtime_version": "7.14",
                "min_glibc": "2.35",
            }
        ],
        container=_llama_image("rocm"),
    ),
    "linux/x86_64/vulkan": _llama(
        "llama-b12500-bin-ubuntu-vulkan-x64.tar.gz",
        "589602d6d2179e98d985c5ec347d039c3a840b4d55dd623697ed828b242ed59c",
        36725800,
        min_glibc=_GLIBC_UBUNTU_22,
        container=_llama_image("vulkan"),
    ),
    "linux/aarch64": _llama(
        "llama-b12500-bin-ubuntu-arm64.tar.gz",
        "ea0f4155e1cc8d634e3b432a16d85362071a8259ef8a02ea14ca46923e15db8f",
        19377197,
        min_glibc=_GLIBC_UBUNTU_24,
        container=_llama_image("cpu"),
    ),
    "linux/aarch64/cuda": _llama(
        "llama-b12500-bin-ubuntu-cuda-13.5-arm64.tar.gz",
        "5d2496d2da1d35621cfadc54e35b1d40acef343ee001c35e5df6e2b03a5595cc",
        222750158,
        min_driver_version="580",
        min_glibc=_GLIBC_UBUNTU_24,
        parts=[
            _cudart(
                "cudart-llama-b12500-bin-ubuntu-cuda-13.5-arm64.tar.gz",
                "ee86dfc458683a23059c171e8c1d837f57fc3547e7dc9d5ab2fcf55ebfd7aa21",
                559873337,
            )
        ],
        container=_llama_image("cuda13"),
    ),
    "linux/aarch64/vulkan": _llama(
        "llama-b12500-bin-ubuntu-vulkan-arm64.tar.gz",
        "63fb5026a1677321878bcbcd33ec06f1e6f2ef1817fe53b5df5c370b1912b6c3",
        29925305,
        min_glibc=_GLIBC_UBUNTU_24,
        container=_llama_image("vulkan"),
    ),
}

# Selection metadata the seed refreshes on existing rows of the same pinned build (see
# EngineService.seed_builtin_versions): it changes which hosts a build is offered to, never
# what an existing install runs.
COMPAT_KEYS = ("min_glibc", "fallbacks", "container")

_PYPI = "https://files.pythonhosted.org/packages/"
_WHISPER = "https://github.com/ggml-org/whisper.cpp/releases/download/b5907/"
_SDCPP = "https://github.com/leejet/stable-diffusion.cpp/releases/download/master-1106-532d2c1/"


SEED_ENGINE_VERSIONS: List[Dict[str, Any]] = [
    {
        "engine": "llama_cpp",
        "version": LLAMACPP_BUILD,
        "image": None,
        "artifacts": LLAMACPP_ARTIFACTS,
        "launch_template": {
            "command": [
                "llama-server",
                "--host",
                "127.0.0.1",
                "--port",
                "{{port}}",
                "--models-dir",
                "{{model_dir}}",
                "--metrics",
                "--slots",
                "{{flags}}",
            ],
            "env": {},
            "working_dir": None,
            "router_mode": True,
        },
        "default_launch_flags": {
            "jinja": True,
            "cont-batching": True,
            "flash-attn": "auto",
        },
        "health_config": _HEALTH_HTTP,
        "metrics_config": LLAMACPP_METRICS,
        "requirements": {
            "note": "Metal on macOS; CUDA 12.8 (driver >= 525.60, x86_64) / CUDA 13.5 "
            "(driver >= 580, arm64), ROCm 10.0, Vulkan or CPU builds on Linux. Linux needs "
            "libgomp1 (Vulkan: libvulkan1) and glibc >= 2.38 (Ubuntu 24.04 / Debian 13), "
            "except the x86_64 CPU and Vulkan builds (glibc >= 2.34: Ubuntu 22.04, Debian 12). "
            "Older systems: ROCm falls back to b10375 (Ubuntu 22.04 build), otherwise the agent "
            "runs the official image (ghcr.io/ggml-org/llama.cpp b12464) when Docker or Podman "
            "is installed, or use an installed llama-server (engine_paths)",
            "router": LLAMACPP_ROUTER_FACTS,
        },
        "is_default": True,
    },
    {
        "engine": "vllm",
        "version": "0.34.0",
        "image": "vllm/vllm-openai:v0.34.0"
        "@sha256:20fdb089454afbb324efa4110a2c025ff7b27cc1a086e07aa580b18df8ad1ce1",
        "artifacts": {},
        "launch_template": {
            "command": [
                "vllm",
                "serve",
                "{{model_dir}}",
                "--host",
                "127.0.0.1",
                "--port",
                "{{port}}",
                "--served-model-name",
                "{{served_model_name}}",
                "--tensor-parallel-size",
                "{{tp_size}}",
                "--pipeline-parallel-size",
                "{{pp_size}}",
                "--max-model-len",
                "{{context_size}}",
                "{{flags}}",
            ],
            "env": {"CUDA_VISIBLE_DEVICES": "{{device_indices}}"},
            "working_dir": None,
            "router_mode": False,
        },
        "default_launch_flags": {
            "enable-prefix-caching": True,
            "gpu-memory-utilization": 0.9,
        },
        "health_config": _HEALTH_HTTP,
        "metrics_config": _prom("vllm:"),
        "min_driver_version": "580",
        "requirements": {
            "cuda": ">=13.0",
            "vendors": ["nvidia"],
            "note": "Official image (CUDA 13.0, linux/amd64 + arm64). AMD: add a version "
            "with a ROCm image (rocm/vllm)",
        },
        "is_default": True,
    },
    {
        "engine": "vllm_omni",
        "version": "0.32.0",
        "image": "vllm/vllm-omni:v0.32.0"
        "@sha256:f713b909f16cf8c7901f17c3aee3f471cbee5fbde5603b7dbe2631630887d7c2",
        "artifacts": {},
        "launch_template": {
            "command": [
                "vllm-omni",
                "serve",
                "{{model_dir}}",
                "--host",
                "127.0.0.1",
                "--port",
                "{{port}}",
                "--served-model-name",
                "{{served_model_name}}",
                "--tensor-parallel-size",
                "{{tp_size}}",
                "{{flags}}",
            ],
            "env": {"CUDA_VISIBLE_DEVICES": "{{device_indices}}"},
            "working_dir": None,
            "router_mode": False,
        },
        "default_launch_flags": {},
        "health_config": {**_HEALTH_HTTP, "startup_timeout_seconds": 1200},
        "metrics_config": _prom("vllm:"),
        "min_driver_version": "580",
        "requirements": {"cuda": ">=13.0", "vendors": ["nvidia"]},
        "is_default": True,
    },
    {
        "engine": "sglang",
        "version": "0.5.23",
        "image": "lmsysorg/sglang:v0.5.23"
        "@sha256:f832b5c39559c9caa900b22ad37c76fcce66470676855c42052102c537812f1f",
        "artifacts": {},
        "launch_template": {
            "command": [
                "python3",
                "-m",
                "sglang.launch_server",
                "--host",
                "127.0.0.1",
                "--port",
                "{{port}}",
                "--model-path",
                "{{model_dir}}",
                "--served-model-name",
                "{{served_model_name}}",
                "--tp-size",
                "{{tp_size}}",
                "--pp-size",
                "{{pp_size}}",
                "--context-length",
                "{{context_size}}",
                "--enable-metrics",
                "{{flags}}",
            ],
            "env": {"CUDA_VISIBLE_DEVICES": "{{device_indices}}"},
            "working_dir": None,
            "router_mode": False,
        },
        "default_launch_flags": {"mem-fraction-static": 0.88},
        "health_config": _HEALTH_HTTP,
        "metrics_config": _prom("sglang:"),
        "min_driver_version": "580",
        "requirements": {
            "cuda": ">=13.0",
            "vendors": ["nvidia"],
            "note": "Official image (CUDA 13.0). AMD: add a version with "
            "lmsysorg/sglang:v0.5.23-rocm720-mi30x (or -mi35x)",
        },
        "is_default": True,
    },
    {
        "engine": "sglang_diffusion",
        "version": "0.5.23",
        "image": None,
        # Managed Python venv (Python 3.12, glibc >= 2.34): pip installs the pinned wheel with
        # its [diffusion] extra into <state>/engines/sglang_diffusion/0.5.23/venv.
        "artifacts": {
            "linux/x86_64": {
                "url": _PYPI + "ca/5f/fb9c2417fc4c650f62ef98ddee2cc25988456c94e8a6ca8661ef0cbe0b09/"
                "sglang-0.5.23-cp312-cp312-manylinux_2_34_x86_64.whl",
                "sha256": "8d257a319395b322bb1be327d05e5528859358153c8563b04c0a4a3c8af164b6",
                "kind": "python_venv",
                "entrypoint": "venv/bin/sglang",
                "size_bytes": 39046590,
                "pip_extras": ["diffusion"],
                "python": "==3.12",
            },
            "linux/aarch64": {
                "url": _PYPI + "f5/f2/ec22edeedc4528052b367e5c1ab0fff3d64cbee4a02e98b550be163ae0d4/"
                "sglang-0.5.23-cp312-cp312-manylinux_2_34_aarch64.whl",
                "sha256": "2d538a89a6e7b1b0707e2ab7f8edef4af6a7beb5da661f9a231cddcdf5308417",
                "kind": "python_venv",
                "entrypoint": "venv/bin/sglang",
                "size_bytes": 37755434,
                "pip_extras": ["diffusion"],
                "python": "==3.12",
            },
        },
        "launch_template": {
            "command": [
                "sglang",
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                "{{port}}",
                "--model-path",
                "{{model_dir}}",
                "--served-model-name",
                "{{served_model_name}}",
                "{{flags}}",
            ],
            "env": {},
            "working_dir": None,
            "router_mode": False,
        },
        "default_launch_flags": {},
        "health_config": {**_HEALTH_HTTP, "startup_timeout_seconds": 1200},
        "metrics_config": _METRICS_PROXY,
        "requirements": {
            "note": "Linux only (no macOS wheel): python3.12 with venv on the node; "
            "pip pulls PyTorch for the node's CUDA"
        },
        "is_default": True,
    },
    {
        "engine": "tensorrt_llm",
        "version": "1.3.0",
        "image": "nvcr.io/nvidia/tensorrt-llm/release:1.3.0"
        "@sha256:bb9a0c29b6b5f52e142c324eb2f4e2f114fc7573dc1c6d4d1495e4ebc6afdfbf",
        "artifacts": {},
        "launch_template": {
            "command": [
                "trtllm-serve",
                "{{model_dir}}",
                "--host",
                "127.0.0.1",
                "--port",
                "{{port}}",
                "--tp_size",
                "{{tp_size}}",
                "--pp_size",
                "{{pp_size}}",
                "--max_seq_len",
                "{{context_size}}",
                "{{flags}}",
            ],
            "env": {"CUDA_VISIBLE_DEVICES": "{{device_indices}}"},
            "working_dir": None,
            "router_mode": False,
        },
        "default_launch_flags": {"backend": "pytorch"},
        "health_config": {**_HEALTH_HTTP, "startup_timeout_seconds": 1800},
        "metrics_config": {
            "path": "/metrics",
            "format": "json",
            "slots_path": None,
            "scrape_interval_seconds": 2,
            "derive_from_proxy": False,
            "metric_prefix": None,
        },
        "min_driver_version": "580",
        "requirements": {"cuda": ">=13.0", "vendor": "nvidia"},
        "is_default": True,
    },
    {
        "engine": "mlx",
        "version": "0.32.2",
        "image": None,
        # Managed Python venv: <state>/engines/mlx/0.32.2/venv with the pinned mlx-lm wheel.
        "artifacts": {
            "macos/aarch64": {
                "url": _PYPI + "d1/4f/000eefdeb41388782b47e7f39904cf2d9e7e49a60df5e172097c358dba8e/"
                "mlx_lm-0.32.2-py3-none-any.whl",
                "sha256": "5c0991248cfff5872503d74f466d2532dd8cb7fb3670cb9be7a06d7305dadc04",
                "kind": "python_venv",
                "entrypoint": "venv/bin/mlx_lm.server",
                "size_bytes": 546040,
                "python": ">=3.11",
            },
        },
        "launch_template": {
            "command": [
                "mlx_lm.server",
                "--host",
                "127.0.0.1",
                "--port",
                "{{port}}",
                "--model",
                "{{model_dir}}",
                "{{flags}}",
            ],
            "env": {},
            "working_dir": None,
            "router_mode": False,
        },
        "default_launch_flags": {},
        "health_config": {**_HEALTH_HTTP, "path": "/v1/models"},
        "metrics_config": _METRICS_PROXY,
        "requirements": {
            "os": "macos",
            "vendor": "apple",
            "note": "Python >= 3.11 on the Mac (python.org or Homebrew; the Xcode tools "
            "ship 3.9)",
        },
        "is_default": True,
    },
    {
        "engine": "mlx_audio",
        "version": "0.6.7",
        "image": None,
        # The launch template runs ``python3 -m mlx_audio.server``: the venv's python.
        "artifacts": {
            "macos/aarch64": {
                "url": _PYPI + "7d/fd/45df0ef1d0709bb3b69881d62cbeea6ca1dca65a1c54cb831de031c8e52b/"
                "mlx_audio-0.6.7-py3-none-any.whl",
                "sha256": "ed7c2abc5d20d9eadf33086e222ce51bf3c4be77384dc55a8c5729485bbc1b56",
                "kind": "python_venv",
                "entrypoint": "venv/bin/python3",
                "size_bytes": 2790482,
                "pip_extras": ["server", "stt", "tts"],
                "python": ">=3.11",
            },
        },
        "launch_template": {
            "command": [
                "python3",
                "-m",
                "mlx_audio.server",
                "--host",
                "127.0.0.1",
                "--port",
                "{{port}}",
                "--model",
                "{{model_dir}}",
                "{{flags}}",
            ],
            "env": {},
            "working_dir": None,
            "router_mode": False,
        },
        "default_launch_flags": {},
        "health_config": _HEALTH_HTTP,
        "metrics_config": _METRICS_PROXY,
        "requirements": {
            "os": "macos",
            "vendor": "apple",
            "note": "Python >= 3.11 on the Mac (python.org or Homebrew)",
        },
        "is_default": True,
    },
    {
        "engine": "whisper_cpp",
        "version": "b5907",
        "image": None,
        # Linux: the release's CPU build. macOS has no prebuilt server, so the agent builds the
        # pinned source archive with CMake (Metal on by default; needs cmake + Xcode tools).
        "artifacts": {
            "linux/x86_64": {
                "url": _WHISPER + "whisper-bin-ubuntu-x64.tar.gz",
                "sha256": "a6199027fe5b4239c84171d2bca4c0ff44ec86a515494d5a39b237b212d429bd",
                "kind": "tarball",
                "entrypoint": "whisper-bin-ubuntu-x64/whisper-server",
                "size_bytes": 10413708,
            },
            "linux/aarch64": {
                "url": _WHISPER + "whisper-bin-ubuntu-arm64.tar.gz",
                "sha256": "da2e86ac058e1db0281167355428665b82c0d92e4506d186a3894bec55ceefef",
                "kind": "tarball",
                "entrypoint": "whisper-bin-ubuntu-arm64/whisper-server",
                "size_bytes": 4623550,
            },
            "macos/aarch64": {
                "url": "https://github.com/ggml-org/whisper.cpp/archive/refs/tags/b5907.tar.gz",
                "sha256": "6896da37782405144d9c26160d51bac9eca265dfe6b15c744b25342aa46fd56b",
                "kind": "source",
                "entrypoint": "build/bin/whisper-server",
                "size_bytes": 10065406,
                "strip_components": 1,
                "build": {
                    "system": "cmake",
                    "args": ["-DBUILD_SHARED_LIBS=OFF", "-DWHISPER_BUILD_TESTS=OFF"],
                    "targets": ["whisper-server"],
                },
            },
        },
        "launch_template": {
            "command": [
                "whisper-server",
                "--host",
                "127.0.0.1",
                "--port",
                "{{port}}",
                "-m",
                "{{model_path}}",
                "--inference-path",
                "/v1/audio/transcriptions",
                "{{flags}}",
            ],
            "env": {},
            "working_dir": None,
            "router_mode": False,
        },
        "default_launch_flags": {},
        "health_config": {**_HEALTH_HTTP, "path": "/"},
        "metrics_config": _METRICS_PROXY,
        "requirements": {
            "note": "Linux builds are CPU (glibc >= 2.35, libgomp1); macOS builds from source "
            "with CMake (Metal)"
        },
        "is_default": True,
    },
    {
        "engine": "stable_diffusion_cpp",
        "version": "master-1106-532d2c1",
        "image": None,
        "artifacts": {
            "linux/x86_64": {
                "url": _SDCPP + "sd-master-532d2c1-bin-Linux-Ubuntu-26.04-x86_64.zip",
                "sha256": "0a02f7f51feb0691128f60cf6ff1c686ec23089561cfe7c3b72645f446646e13",
                "kind": "zip",
                "entrypoint": "sd-server",
                "size_bytes": 29124086,
            },
            "linux/x86_64/vulkan": {
                "url": _SDCPP + "sd-master-532d2c1-bin-Linux-Ubuntu-26.04-x86_64-vulkan.zip",
                "sha256": "ca30fe17f1735cdfb4c8561b75799070c25cc86a9e5438f4c0e1b163baae4e12",
                "kind": "zip",
                "entrypoint": "sd-server",
                "size_bytes": 42212329,
            },
            "linux/x86_64/rocm": {
                "url": _SDCPP + "sd-master-532d2c1-bin-Linux-Ubuntu-24.04-x86_64-rocm-10.1.0.zip",
                "sha256": "e0f7c33b6f75cb7319bad77c71a65972c2e8969ab5d2c78022fd42c42b9c465d",
                "kind": "zip",
                "entrypoint": "sd-server",
                "size_bytes": 321970428,
                "min_runtime_version": "10.1",
            },
            "macos/aarch64": {
                "url": _SDCPP + "sd-master-532d2c1-bin-Darwin-macOS-26.7.2-arm64.zip",
                "sha256": "8146d6ffc75f8ff8f64b62a80fffee8fb714ab65e368e202084135fa07cc6f36",
                "kind": "zip",
                "entrypoint": "sd-server",
                "size_bytes": 38331254,
            },
        },
        "launch_template": {
            "command": [
                "sd-server",
                "--host",
                "127.0.0.1",
                "--port",
                "{{port}}",
                "-m",
                "{{model_path}}",
                "{{flags}}",
            ],
            "env": {},
            "working_dir": None,
            "router_mode": False,
        },
        "default_launch_flags": {},
        "health_config": {**_HEALTH_HTTP, "path": "/"},
        "metrics_config": _METRICS_PROXY,
        "requirements": {
            "note": "Release builds: Linux ones are built on Ubuntu 26.04 (ROCm on 24.04) and "
            "need a glibc that new; the macOS one needs macOS 26. Older systems: install "
            "sd-server yourself (engine_paths)"
        },
        "is_default": True,
    },
    {
        "engine": "custom",
        "version": "template",
        "image": "ghcr.io/example/custom-engine:latest",
        "artifacts": {},
        "launch_template": {
            "command": [
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                "{{port}}",
                "--model",
                "{{model_dir}}",
                "{{flags}}",
            ],
            "env": {},
            "working_dir": None,
            "router_mode": False,
        },
        "default_launch_flags": {},
        "health_config": _HEALTH_HTTP,
        "metrics_config": _METRICS_PROXY,
        "requirements": {"note": "Edit image + launch/health/metrics config before use"},
        "is_default": True,
        "status": "disabled",
    },
]
