# Hygon DCU

Status: **fixture-only and unverified** — the device plugin's parser is tested against output
taken from DTK documentation and public logs, not a real Hygon DCU
(`apps/node-agent/crates/gpudeck-devices/src/hygon.rs`: "Status: fixture-only, formats taken
from DTK documentation and public logs (unverified)."). Treat every field name and version
string below as the plugin's best guess until someone sends a hardware report — see
[SUPPORT.md](../SUPPORT.md).

## Detection

The plugin looks for `hy-smi` on `PATH`. `hy-smi` is a fork of `rocm-smi`, so its JSON output
(`--json`) is parsed with the same parser AMD uses, with `"DCU use (%)"` read in place of
`"GPU use (%)"`. When the installed `hy-smi` rejects `--json`, the plugin falls back to parsing
its default summary table plus `--showmeminfo vram` text output. Reported per device (when the
JSON form is available): name, VRAM, driver version, DTK/ROCm runtime version, PCI bus id, power
cap; the text fallback additionally reads `Temp`, `AvgPwr`/`PwrCap`, `VRAM%`, and utilization
under the keys `DCU%`, `HCU%` or `GPU%`, checked in that order.

## What runs on it

Only **llama.cpp** has any accelerator-specific support: `llama_cpp`'s `supported_vendors`
includes `hygon`, and `artifacts.py`'s `ACCELERATOR_ORDER` maps `hygon → rocm, vulkan` — a Hygon
node is offered the same pinned ROCm 10.0 build as AMD (with the same `b10375` ROCm 7.14
fallback), then Vulkan, on the same glibc terms as [amd.md](amd.md#os-and-glibc). The ROCm
version comparison uses whatever `hy-smi` reports as the runtime version; DTK and upstream ROCm
numbering are not guaranteed to line up, so treat the `min_runtime_version` checks as
best-effort.

vLLM's `supported_vendors` also lists `hygon`, but — like AMD — GPU Deck only ships vLLM's
NVIDIA CUDA image by default; nothing ROCm-specific is pinned for Hygon, and no public ROCm image
is known to be confirmed working on DTK. SGLang, vLLM-Omni, SGLang Diffusion, TensorRT-LLM, MLX,
mlx-audio, whisper.cpp and stable-diffusion.cpp do not list `hygon` in `supported_vendors` at
all — not available, full stop (`001_init/up.sql`).

## What the agent installs vs. what you install

| | Agent | You |
| --- | --- | --- |
| Driver / DTK | Nothing — reads `hy-smi` | The Hygon driver and DTK user space, `hy-smi` on `PATH` |
| llama.cpp | Downloads the pinned ROCm build it is offered (same as AMD) | `libgomp1`; glibc per [amd.md](amd.md#os-and-glibc) |
| Container runtime | Nothing | Docker, Podman or nerdctl if you add a container engine version yourself |

## Slicing

Not specified for Hygon specifically; `slicing.rs::slice_support` groups `Vendor::Hygon` with
`Vendor::Amd` under `SliceMode::ComputeMask` (`HSA_CU_MASK`-based CU masking, memory not
isolated) — the source comment says "Hygon DCU uses the same ROCm/DTK semantics." Device
selection env vars: `HIP_VISIBLE_DEVICES`, `ROCR_VISIBLE_DEVICES` (same as AMD).

## Multi-GPU and multi-node

Not specified beyond llama.cpp's engine-level pipeline parallel (`003_llamacpp_pipeline_parallel`
applies to every vendor llama.cpp runs on, Hygon included). No multi-GPU container engine is
pinned for Hygon.

## Known limitations

- Every field above is unverified against real hardware.
- Only llama.cpp has a pinned, Hygon-eligible build; everything else needs an operator-installed
  engine via `engine_paths`, if it runs at all (vLLM, SGLang, TensorRT-LLM, vLLM-Omni and SGLang
  Diffusion's `supported_vendors` do not include `hygon`).
- `hy-smi`'s text-table fallback format is a best guess; if your `hy-smi` build's table columns
  differ, inventory and metrics may come back empty rather than wrong — please report it.

## Prepare this machine (checklist)

```text
[ ] Install the Hygon driver and DTK; confirm `hy-smi` lists your DCU(s)
[ ] Install libgomp1 for the llama.cpp ROCm build
[ ] Allow outbound 8443 (hub) and 8444-8445 (API-layer tunnels) to the control plane, and 443
    to Hugging Face / ModelScope (or your mirror); no inbound port is needed
[ ] Direct mode only (private networks): inbound 9443 from the API layer's addresses
[ ] Run the install command from Nodes -> Add node in the web UI
[ ] Send a hardware report either way (docs/SUPPORT.md) — this plugin has not seen real hardware
```

## Troubleshooting

| Refusal / symptom | Meaning |
| --- | --- |
| No devices appear in inventory | `hy-smi` is not on `PATH`, or its output does not match either the JSON or text format the plugin expects — check `hy-smi --json` and `hy-smi` manually |
| `driver_too_old` / `driver_unverified` on llama.cpp | The ROCm runtime version `hy-smi` reports did not meet (or could not be parsed against) the pinned build's minimum |
| `artifact_missing` | Neither the ROCm nor the Vulkan llama.cpp build matches this node's glibc, and no container runtime was found |
| vLLM/SGLang never offered on this node | Expected — no Hygon-eligible engine version is pinned; SGLang in particular does not list `hygon` in `supported_vendors` at all |
