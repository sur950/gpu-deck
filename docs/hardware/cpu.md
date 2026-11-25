# CPU-only

A machine with no supported accelerator reports no GPU devices; the node agent still joins and
runs engines whose builds have a CPU path. This page covers running GPU Deck on such a machine,
including the Linux-container multi-node test setup used in the project's own release testing
(simulated GPUs, real CPU inference — see [TESTING.md](../TESTING.md)).

Status: the CPU llama.cpp build is exercised constantly in automated testing (unit tests and the
multi-node container setup use small GGUF models on the CPU), but the release notes do not call
out a bare-metal CPU-only production deployment as separately verified — treat it the same as the
other "implemented, covered by tests" rows in [README.md](README.md#verification-status).

## What runs

| Engine | Runs on CPU? | Build |
| --- | --- | --- |
| llama.cpp | Yes | `linux/x86_64` or `linux/aarch64` platform key (no accelerator suffix), or the macOS build on a CPU-only Mac |
| whisper.cpp | Yes | Same Linux release builds used on every Linux node — they are CPU-only regardless of vendor (see [nvidia.md](nvidia.md#per-engine-notes) and [amd.md](amd.md#per-engine-notes)) |
| stable-diffusion.cpp | Yes | The baseline `linux/x86_64` artifact (no accelerator suffix) or the macOS build |
| vLLM, vLLM-Omni, SGLang, TensorRT-LLM | No | Each is pinned to an NVIDIA CUDA image; `requirements.vendors` on vLLM/vLLM-Omni/SGLang/TensorRT-LLM names `["nvidia"]` specifically, so these need an NVIDIA GPU to be useful even where the compatibility checker does not hard-block a vendor-less node |
| SGLang Diffusion | No | Its managed venv pulls PyTorch for "the node's CUDA" — no CPU path is described |
| MLX, mlx-audio | No | `supported_os` is `macos` only, and they need the Apple GPU |
| Custom | Depends | A custom engine can be anything, including a CPU-only container or binary you provide |

## Driver / runtime

None — a CPU-only node has no accelerator driver to install. The `compat.py` driver check only
applies when `target.vendor` is NVIDIA or AMD; a node with no GPU devices reports no vendor, so
no driver minimum is ever checked.

## What the agent installs vs. what you install

| | Agent | You |
| --- | --- | --- |
| llama.cpp, whisper.cpp, stable-diffusion.cpp | Downloads the pinned CPU build for the node's OS/arch | `libgomp1` (Linux); nothing on macOS |
| Container runtime | Nothing | Docker, Podman or nerdctl only if you plan to run a custom container engine |

## Multi-GPU / scaling

Not applicable in the GPU sense. The one thing to configure on a CPU node:
llama.cpp otherwise starts one thread per core per process, and several engines sharing a
machine slow each other down sharply — set `threads` in the deployment's launch flags when you
co-host more than one CPU-backed engine on the same box (noted in
[TESTING.md](../TESTING.md#known-requirements-and-limits)).

## The multi-node container test setup

GPU Deck's own multi-node testing runs entirely on CPU-backed containers that each report one
*simulated* NVIDIA GPU through a stand-in `nvidia-smi` (`apps/node-agent/testing/multinode/`):
Debian 12 and Ubuntu 24.04 containers, two to four at a time, joined to the same control plane.
llama.cpp really runs on the CPU in every container; only the reported GPU is fake. This
exercises scheduling, routing, failover, draining and automation rules without real GPU
hardware — see [TESTING.md's "Running the multi-node setup
yourself"](../TESTING.md#running-the-multi-node-setup-yourself) for the exact steps
(`FAKE_GPU_MEM_MIB` sizes a container's simulated GPU).

## Known limitations

- Every container/GPU-only engine (vLLM, vLLM-Omni, SGLang, TensorRT-LLM, SGLang Diffusion, MLX,
  mlx-audio) is unavailable; only llama.cpp, whisper.cpp, stable-diffusion.cpp and a custom
  engine you provide run here.
- CPU inference is far slower than GPU inference for the same model; size context and
  concurrency accordingly (`low_latency` / `high_throughput` presets still apply — see
  [engines.md](engines.md#presets)).

## Prepare this machine (checklist)

```text
[ ] Linux (any distro meeting llama.cpp's glibc needs) or macOS
[ ] Install libgomp1 on Linux
[ ] Set `threads` in launch flags if co-hosting more than one CPU-backed engine
[ ] Allow outbound 8443 (hub) and 8444-8445 (API-layer tunnels) to the control plane, and 443
    to Hugging Face / ModelScope (or your mirror); no inbound port is needed
[ ] Direct mode only (private networks): inbound 9443 from the API layer's addresses
[ ] Run the install command from Nodes -> Add node in the web UI
```

## Troubleshooting

| Refusal / symptom | Meaning |
| --- | --- |
| `artifact_missing` for llama.cpp | glibc on this machine is older than the CPU build needs (2.34+ on x86_64, 2.38+ on arm64) and no container runtime was found — see [nvidia.md's glibc table](nvidia.md#os-and-glibc) |
| vLLM / SGLang / TensorRT-LLM / vLLM-Omni never show up as placeable here | Expected — all four need an NVIDIA GPU |
| Inference is much slower than expected with several models loaded | Set `threads` in each deployment's launch flags; by default each llama.cpp process claims one thread per core |
