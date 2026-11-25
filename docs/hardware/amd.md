# AMD Instinct and Radeon (ROCm)

Covers AMD data-center (Instinct) and consumer (Radeon) GPUs through the node agent's AMD device
plugin and the engines whose `supported_vendors` includes `amd`.

Status: implemented and covered by fixture tests (the device plugin's parsers are tested against
recorded `amd-smi` / `rocm-smi` JSON output), not yet run on real AMD hardware — see
[TESTING.md](../TESTING.md). This is the second most wanted hardware report after NVIDIA.

## Detection

`apps/node-agent/crates/gpudeck-devices/src/amd.rs`: `amd-smi` JSON first (ROCm 6.0+), falling
back to `rocm-smi --json` on older stacks. `amd-smi` output comes in two shapes that the parser
handles: a top-level array of per-GPU objects (ROCm 6.0-6.3) and `{"gpu_data": [...]}` (ROCm
6.4+); values are plain numbers/strings or `{"value": n, "unit": "MB"}` objects, and `"N/A"`
parses as absent. Reported per device: product name, VRAM, driver version, ROCm version
(`amd-smi version --json`), compute capability (`target_graphics_version`), PCI bus id, NUMA
node, power cap, compute unit count, and MI300X compute/memory partition info when present.

## OS and glibc

Linux only (ROCm has no macOS or Windows user-space stack GPU Deck targets). Glibc needs, per
engine build:

| Build | Built on | glibc needed |
| --- | --- | --- |
| llama.cpp ROCm 10.0 (`linux/x86_64/rocm`) | Ubuntu 24.04 | 2.38+ |
| llama.cpp ROCm fallback (`b10375`, ROCm 7.14) | Ubuntu 22.04 | 2.35+ |
| vLLM, SGLang (containers, if you add a ROCm image — see below) | n/a | whatever the image needs |
| stable-diffusion.cpp ROCm 10.1 (`linux/x86_64/rocm`) | Ubuntu 24.04 | that new |

## Driver and runtime minimums

AMD versions are compared as the ROCm user-space version (`devices.runtime_version`, e.g.
`"6.2.0"`), never the amdgpu kernel driver version — the two are not comparable across ROCm
releases (`compat.py`'s comment on `DEFAULT_MIN_DRIVER`).

| Engine | ROCm minimum | Source |
| --- | --- | --- |
| llama.cpp ROCm 10.0 build | 10.0 | artifact `min_runtime_version` |
| llama.cpp `b10375` fallback | 7.14 | artifact `min_runtime_version` |
| llama.cpp (default, no accelerator match) | 6.0 | `DEFAULT_MIN_DRIVER["llama_cpp"]["amd"]` |
| vLLM (if you add a ROCm image) | 6.1 | `DEFAULT_MIN_DRIVER["vllm"]["amd"]` |
| SGLang (if you add a ROCm image) | 6.2 | `DEFAULT_MIN_DRIVER["sglang"]["amd"]` |
| stable-diffusion.cpp ROCm 10.1 build | 10.1 | artifact `min_runtime_version` |
| whisper.cpp (default; not actually used — see below) | 6.0 | `DEFAULT_MIN_DRIVER["whisper_cpp"]["amd"]` |

## What the agent installs vs. what you install

| | Agent | You |
| --- | --- | --- |
| Driver / ROCm user space | Nothing — reads `amd-smi` / `rocm-smi` | The amdgpu driver and ROCm user space (`amd-smi` or `rocm-smi`) |
| Container runtime | Nothing | Docker, Podman or nerdctl for container engines; `/dev/kfd` and `/dev/dri` are passed through automatically, plus `--group-add=video --security-opt=seccomp=unconfined` (`launch.rs::container_device_args`) |

No ROCm equivalent of the NVIDIA Container Toolkit is needed — the container device args above
are all `docker run` needs; the agent's service user must still be able to run the container
CLI (typically `docker` group membership).

## Per-engine notes

### llama.cpp (native, router mode)

`linux/x86_64/rocm`: the pinned `b12500` ROCm 10.0 build, with a same-accelerator fallback to
`b10375` (ROCm 7.14, built on Ubuntu 22.04) for older ROCm/glibc; after both, Vulkan, then the
official `server-rocm-b12464` image (ROCm 7.2.1 inside) when a container runtime is present.
Selection order is `rocm` then `vulkan` (`artifacts.py` `ACCELERATOR_ORDER["amd"]`).

### vLLM, SGLang (containers — add the image yourself)

`supported_vendors` includes `amd` for both engines, but GPU Deck only ships their NVIDIA CUDA
images by default. To run them on AMD:

- **vLLM**: add an engine version with a ROCm image (for example `rocm/vllm`) —
  [DEPLOYMENT.md](../DEPLOYMENT.md#engines-at-a-glance).
- **SGLang**: add a version with `lmsysorg/sglang:v0.5.23-rocm720-mi30x` (or `-mi35x` for
  MI350-class) — same table.

Create the engine version through the admin API or the web UI's Engines page (not a code change):
point `image` at the ROCm tag + digest, keep the same `launch_template` (the command line does
not change) and set `requirements.vendors: ["amd"]` if you want to keep it off NVIDIA nodes.

### vLLM-Omni, TensorRT-LLM

Not available: `supported_vendors` is `{nvidia}` only for both.

### SGLang (diffusion image/video serving)

`sglang_diffusion`'s `supported_vendors` includes `amd`, but its pinned Python wheel's
requirements note says `pip` "pulls PyTorch for the node's CUDA" — the catalog does not say how
an AMD node would get a ROCm-enabled PyTorch build through the managed venv. Not specified; if
you need this on AMD, install the engine yourself and set `engine_paths`.

### whisper.cpp

`supported_vendors` includes `amd`, but the only Linux artifacts pinned (`linux/x86_64`,
`linux/aarch64`) carry no accelerator suffix — they are the CPU-only release build. No
ROCm-accelerated whisper.cpp build is pinned.

### stable-diffusion.cpp (native)

`linux/x86_64/rocm`: pinned ROCm 10.1 build, built on Ubuntu 24.04. This is the one built-in
engine with a real ROCm-accelerated artifact on AMD out of the box.

## Slicing: compute-unit masking

From `slicing.rs::slice_support(Vendor::Amd)` (`SliceMode::ComputeMask`, memory **not**
isolated, compute enforced, 1% granularity):

`HSA_CU_MASK=<gpu>:<mask>` limits the compute units an engine can schedule on — `compute_pct`
becomes a fraction of CUs, rounded to whole CUs (the device's `core_count` is its CU count).
Memory is a **soft cap only**, enforced through engine flags (`--gpu-memory-utilization` and
similar), not by the driver — two slices on the same device are not memory-isolated. MI300X
compute/memory partitions (CPX/NPS4) appear as separate devices in inventory and need no slice at
all. Device selection env vars: `HIP_VISIBLE_DEVICES`, `ROCR_VISIBLE_DEVICES`.

## Multi-GPU and multi-node

Tensor and pipeline parallel are capability-gated per engine version, not per vendor: vLLM and
SGLang support both (`capabilities.tensor_parallel` / `pipeline_parallel = true`), so a ROCm
image you add inherits the same TP/PP flags as the NVIDIA image. llama.cpp's cross-node pipeline
parallel (`--rpc`) works the same way regardless of accelerator. See
[nvidia.md's multi-GPU and multi-node section](nvidia.md#multi-gpu-and-multi-node) for the flag
details — they are engine-level, not NVIDIA-specific.

## Known limitations

- The AMD device plugin's parsers are tested only against recorded `amd-smi` / `rocm-smi`
  output, not real hardware.
- vLLM and SGLang need an operator-added ROCm engine version; neither ships pinned on AMD.
- whisper.cpp has no ROCm-accelerated build; it runs CPU-only.
- SGLang Diffusion on AMD is not specified in the catalog (CUDA-oriented PyTorch install note).
- TensorRT-LLM and vLLM-Omni do not run on AMD at all (NVIDIA-only).

## Prepare this machine (checklist)

```text
[ ] Install the amdgpu kernel driver and ROCm user space
[ ] Confirm it: `amd-smi` (preferred) or `rocm-smi` lists your GPU(s)
[ ] For container engines: install Docker, Podman or nerdctl
    [ ] Add the agent's service user to the group that can run the container CLI
    [ ] No extra toolkit is needed; the agent passes /dev/kfd and /dev/dri through
[ ] To run vLLM or SGLang here, add a ROCm engine version first (see "Per-engine notes" above)
[ ] Allow outbound 8443 (hub) and 8444-8445 (API-layer tunnels) to the control plane, and 443
    to Hugging Face / ModelScope (or your mirror); no inbound port is needed
[ ] Direct mode only (private networks): inbound 9443 from the API layer's addresses
[ ] Run the install command from Nodes -> Add node in the web UI
```

## Troubleshooting

| Refusal / symptom | Meaning |
| --- | --- |
| `driver_too_old` | The reported ROCm version is below the engine's minimum (table above) |
| `driver_unverified` (warning) | ROCm version could not be parsed from the node's report |
| `artifact_missing` for llama.cpp | glibc too old for the ROCm 10.0 build and the `b10375` fallback, and no container runtime found |
| `vendor_unsupported` for vLLM-Omni, TensorRT-LLM, SGLang Diffusion's CUDA-only PyTorch path | These engines (or paths) do not run on AMD in the current catalog |
| vLLM/SGLang placement never offered on an AMD node | No ROCm engine version exists yet — add one (see "Per-engine notes") |
| `slice_too_small` | The CU-masked share is smaller than the VRAM/compute estimate needs — remember memory is **not** isolated between slices on AMD, so another instance's usage can also push you over |
