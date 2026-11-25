# Apple Silicon

Covers every Apple Silicon Mac through the node agent's Apple plugin (Metal, IOKit, IOReport,
SMC) and the engines whose `supported_vendors` includes `apple`.

Status: verified on real hardware — llama.cpp and MLX were run on an Apple Silicon Mac as part of
the release testing (NVIDIA is the other accelerator that has run for real, see
[nvidia.md](nvidia.md#status-on-real-hardware)). mlx-audio, whisper.cpp and
stable-diffusion.cpp run on the same hardware type but are covered only by fixture tests so far
(see [TESTING.md](../TESTING.md)).

## Detection

`apps/node-agent/crates/gpudeck-devices/src/apple/`, real on this platform, no root needed:

| Field | Source |
| --- | --- |
| Name, VRAM | Metal `MTLCreateSystemDefaultDevice()`: `name`, `recommendedMaxWorkingSetSize` (the wired memory limit the GPU may use, reported as `vram_total_bytes`, `is_unified_memory = true`) |
| Usable fraction | `recommendedMaxWorkingSetSize / hw.memsize` → `extra.usable_memory_fraction` |
| GPU cores, chip | IOKit `IOAccelerator` (`AGXAccelerator*`) properties `gpu-core-count`, `model` |
| Runtime / compute capability | Metal family support → `Metal 4` / `metal4` (else `Metal 3` / `metal3`) |
| Driver version | macOS product version + build (`kern.osproductversion`, `kern.osversion`), reported as `macOS <version> (<build>)` |
| UUID | `apple-<chip-slug>-<IOPlatformSerialNumber>` |
| Utilization, memory used | `IOAccelerator` → `PerformanceStatistics["Device Utilization %"]`, `["In use system memory"]` |
| Power | IOReport `Energy Model` / `GPU Energy` delta between samples (dlopen of `libIOReport.dylib`) |
| Temperature | SMC `Tg*` float keys via the `AppleSMC` user client (mean of valid sensors), best effort |

When the IOKit FFI is unavailable the plugin falls back to parsing `ioreg` / `system_profiler`
text output.

## OS requirements

| Engine | macOS requirement |
| --- | --- |
| llama.cpp | none pinned beyond the Metal build running |
| MLX, mlx-audio | Python 3.11+ (python.org or Homebrew — the Xcode Command Line Tools ship 3.9) |
| whisper.cpp | built from source with CMake; needs `cmake` and the Xcode Command Line Tools |
| stable-diffusion.cpp | macOS 26+ (the pinned macOS arm64 build's requirements note) |

No driver or GPU runtime to install — Metal and IOKit ship with macOS, and the compatibility
checker does not apply a driver minimum on Apple (`_DRIVER_KIND` in `compat.py` only has entries
for `nvidia` and `amd`).

## What the agent installs vs. what you install

| | Agent | You |
| --- | --- | --- |
| llama.cpp | Downloads the pinned macOS arm64 or x86_64 Metal build | Nothing |
| MLX | Creates a venv with the pinned `mlx-lm` 0.32.2 wheel | Python 3.11+ on `PATH` (or set `GPUDECK_AGENT_PYTHON`) |
| mlx-audio | Creates a venv with the pinned `mlx-audio` 0.6.7 wheel (`server`, `stt`, `tts` extras) | Python 3.11+ |
| whisper.cpp | Builds the pinned source archive (release `b5907`) with CMake (Metal on by default) | `cmake` and the Xcode Command Line Tools |
| stable-diffusion.cpp | Downloads the pinned macOS arm64 build | macOS 26+ |
| Container engines | — | **Not supported on macOS nodes at all** (`compat.py`: `container_on_macos` hard-fails any container-runtime engine when `os == "macos"`) |

The install script keeps your shell `PATH` in the launchd plist, so an engine you installed
yourself (and the Python the agent uses for MLX/mlx-audio venvs) resolves the same way it would
in a terminal.

## Per-engine notes

### llama.cpp (native, router mode, verified)

Metal build, macOS arm64 or x86_64, no Vulkan/CUDA/ROCm distinction — one artifact per
architecture (`macos/aarch64`, `macos/x86_64`). Router mode: one `llama-server --models-dir`
process serves every loaded model (see [components/node-agent.md](../components/node-agent.md)).

### MLX (`mlx_lm.server`, verified)

Pinned `mlx-lm` 0.32.2. The model given by `--model` is addressed as `default_model` in request
bodies — the agent's proxy rewrites `model` to that name. `GET /health` is `{"status": "ok"}`;
there is no `/metrics` endpoint, so every metric is derived from the proxy (tokens from SSE
`usage`, TTFT from the first content chunk, TPOT, KV estimate from prompt+generated tokens ×
bytes/token). Serves chat, thinking and vision model types.

### mlx-audio (`mlx_audio.server`)

Pinned `mlx-audio` 0.6.7. OpenAI-style `/v1/audio/speech` (TTS — Kokoro and others) and
`/v1/audio/transcriptions` (STT — Whisper, Parakeet and others). Models are named per request
(a Hugging Face repo id or local path) and loaded on first use; no metrics endpoint, so
`media_units` (audio duration / input characters) is derived from the proxy.

### whisper.cpp (`whisper-server`)

Built from source on macOS (Metal); the Linux release builds are CPU-only (see [cpu.md](cpu.md)
and [nvidia.md](nvidia.md)/[amd.md](amd.md) for why GPU-vendor Linux nodes also get the CPU
build). `--inference-path /v1/audio/transcriptions` maps the OpenAI transcription path directly
onto the server's own endpoint.

### stable-diffusion.cpp (`sd-server`)

macOS arm64 build pinned (needs macOS 26+); on an older macOS, build or install your own
`sd-server` and set `engine_paths`.

### Not available on Apple Silicon

- **vLLM, vLLM-Omni, SGLang**: `supported_os` for all three is `{linux}` only; the vLLM and
  SGLang adapters additionally `bail!` explicitly on Apple vendor devices
  ("vLLM does not run on Apple Silicon GPUs; use mlx or llama.cpp", "SGLang LLM serving needs
  CUDA/ROCm; use mlx or llama.cpp on Apple Silicon" — `vllm.rs`, `sglang.rs`).
- **SGLang Diffusion**: `supported_vendors` includes `apple` and `supported_os` includes
  `macos`, but no macOS artifact is pinned in `seed.py` (only `linux/x86_64` and
  `linux/aarch64`) — a Mac node would need the engine installed via `engine_paths`.
- **TensorRT-LLM**: NVIDIA only.
- **Custom container engines**: containers cannot use the Apple GPU at all — "Metal is not
  exposed to Linux VMs" (`launch.rs::container_device_args`); a custom engine on a Mac must run
  natively (`runtime=native`).

## Slicing: memory caps

From `slicing.rs::slice_support(Vendor::Apple)` (`SliceMode::MemoryCap`, memory **not**
isolated, compute not enforced, VRAM granularity 256 MiB):

Unified memory, one GPU per Mac. `vram_total_bytes` is Metal's `recommendedMaxWorkingSetSize`
(the wired limit, roughly 75-80% of RAM). A slice is a memory budget, not a hardware partition:
llama.cpp honours it through `-ngl` / `-c` / `--cache-type-k`/`v`, and the agent refuses a load
whose weights + KV estimate exceed the free budget. `mlx_lm` has no cap flag at all — the agent
sizes `context_size` instead and accounts RSS. `compute_pct` is informational only; Metal
time-slices compute itself.

## Multi-GPU and multi-node

Not applicable — one GPU per Mac, and llama.cpp's cross-node pipeline parallel targets Linux
`rpc-server` workers; macOS is not a documented RPC worker platform in `distributed.rs`. Not
specified beyond that.

## Known limitations

- Container engines do not run on macOS nodes at all.
- MLX 0.29.1 has a known engine bug with Gemma 3's sliding-window cache: a second request that
  reuses the prompt cache can crash the generation thread
  (`BatchRotatingKVCache.merge` broadcast error) and leave later requests hanging while health
  stays `ok`; the proxy's request timeout surfaces it as errors and the agent's restart policy
  recovers the instance (`mlx.rs`).
- SGLang Diffusion has no pinned macOS build.

## Prepare this machine (checklist)

```text
[ ] macOS with Apple Silicon (no driver to install — Metal/IOKit ship with the OS)
[ ] For MLX / mlx-audio: Python 3.11+ on PATH (python.org or Homebrew), or set GPUDECK_AGENT_PYTHON
[ ] For whisper.cpp: install cmake and the Xcode Command Line Tools
[ ] For stable-diffusion.cpp: macOS 26 or newer, or provide your own sd-server via engine_paths
[ ] Do not plan on container engines (vLLM, SGLang, TensorRT-LLM, custom containers) on this node
[ ] Allow outbound 8443 (hub) and 8444-8445 (API-layer tunnels) to the control plane, and 443
    to Hugging Face / ModelScope (or your mirror); no inbound port is needed
[ ] Direct mode only (private networks): inbound 9443 from the API layer's addresses
[ ] Run the install command from Nodes -> Add node in the web UI (installs as a per-user LaunchAgent,
    or a LaunchDaemon with sudo)
```

## Troubleshooting

| Refusal / symptom | Meaning |
| --- | --- |
| `container_on_macos` | A deployment tried to place a container-runtime engine on a Mac node; pick llama.cpp, MLX, mlx-audio, whisper.cpp or stable-diffusion.cpp instead |
| `os_unsupported` for vLLM, vLLM-Omni, SGLang, TensorRT-LLM | These engines' `supported_os` does not include `macos` |
| `artifact_missing` for SGLang Diffusion | No macOS artifact is pinned; install it yourself and set `engine_paths` |
| MLX requests hang after a cache reuse (Gemma 3 models) | Known 0.29.1 engine bug (see "Known limitations"); the agent restarts the instance automatically |
| `slice_too_small` | Weights + KV estimate exceed the memory budget you set; lower context size, quantization or the budget |
