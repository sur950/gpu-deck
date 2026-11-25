# Moore Threads

Status: **fixture-only and unverified** — the device plugin's parser is tested against a format
taken from MUSA SDK documentation and public logs, not a real Moore Threads GPU
(`apps/node-agent/crates/gpudeck-devices/src/moorethreads.rs`: "Status: fixture-only, format
from MUSA SDK documentation / public logs (unverified)."). Send a hardware report if you try
this — [SUPPORT.md](../SUPPORT.md).

## Detection

The plugin looks for `mthreads-gmi` on `PATH` and runs `mthreads-gmi -q`, an `nvidia-smi -q`
style report: one `GPU <bus id>` block per device with indented sections (`Memory Usage`,
`Utilization`, `Temperature`, `Power Readings`, `Clocks`). Global `Driver Version` and
`MUSA Version` (or `MUSA Runtime Version`) come from the top of the report. Per device: index
(falls back to position in the report when no `Index`/`Minor Number` field is present), total
and used memory (`Memory Usage/Total` or `FB Memory Usage/Total`, similarly for used), UUID
(`GPU UUID`), driver version, MUSA runtime version.

## What runs on it

`llama_cpp`'s `supported_vendors` includes `moorethreads`, and `artifacts.py`'s
`ACCELERATOR_ORDER` maps `moorethreads → vulkan` — a Moore Threads node is offered the pinned
**Vulkan** llama.cpp build (the same one shared with every other vendor's Vulkan fallback), not a
MUSA-native build; there is no `cuda`/`rocm`-equivalent MUSA build pinned. Needs `libvulkan1` and
the GPU's Vulkan driver, same as any other Vulkan node — see
[nvidia.md's OS and glibc table](nvidia.md#os-and-glibc) for the glibc the Vulkan builds need.

`vllm`'s `supported_vendors` also includes `moorethreads`, but only the NVIDIA CUDA image is
pinned by default — nothing MUSA-specific. SGLang, vLLM-Omni, SGLang Diffusion, TensorRT-LLM,
MLX, mlx-audio, whisper.cpp and stable-diffusion.cpp do not list `moorethreads` in
`supported_vendors` — not available.

## What the agent installs vs. what you install

| | Agent | You |
| --- | --- | --- |
| Driver / MUSA | Nothing — reads `mthreads-gmi` | The Moore Threads driver and MUSA user space, `mthreads-gmi` on `PATH` |
| llama.cpp | Downloads the pinned Vulkan build | `libvulkan1` and the GPU's Vulkan driver |
| Container runtime | Nothing | Docker, Podman or nerdctl if you add a container engine version yourself (the agent passes `--runtime=mthreads` and `MTHREADS_VISIBLE_DEVICES` for container device args — `launch.rs::container_device_args`) |

## Slicing

`slicing.rs::slice_support(Vendor::Moorethreads)`: `SliceMode::MemoryCap`, memory not isolated,
compute not enforced, no granularity set. The source comment: "Moore Threads MUSA: no MIG-like
partitioning; memory cap through engine flags. (unverified: vendor SDK)." Device selection env
var: `MUSA_VISIBLE_DEVICES`.

## Multi-GPU and multi-node

Not specified beyond llama.cpp's engine-level pipeline parallel, which applies to whichever
build the node runs (here, Vulkan).

## Known limitations

- Every field above is unverified against real hardware.
- llama.cpp runs through Vulkan, not a MUSA-native build — no MUSA-specific performance tuning is
  pinned.
- `mthreads-gmi -q`'s section layout is a best guess; a different layout from your build may
  parse as empty rather than wrong fields.

## Prepare this machine (checklist)

```text
[ ] Install the Moore Threads driver and MUSA user space; confirm `mthreads-gmi -q` lists your GPU(s)
[ ] Install libvulkan1 and confirm the GPU's Vulkan driver is present
[ ] Allow outbound 8443 (hub) and 8444-8445 (API-layer tunnels) to the control plane, and 443
    to Hugging Face / ModelScope (or your mirror); no inbound port is needed
[ ] Direct mode only (private networks): inbound 9443 from the API layer's addresses
[ ] Run the install command from Nodes -> Add node in the web UI
[ ] Send a hardware report either way (docs/SUPPORT.md) — this plugin has not seen real hardware
```

## Troubleshooting

| Refusal / symptom | Meaning |
| --- | --- |
| No devices appear in inventory | `mthreads-gmi` is not on `PATH`, or its `-q` output does not match the parser's section/indent expectations |
| llama.cpp uses Vulkan, not a MUSA-native path | Expected — no MUSA-native llama.cpp build is pinned |
| `artifact_missing` for llama.cpp | The Vulkan build's glibc requirement is not met and no container runtime was found |
| vLLM never offered on this node | Expected — no MUSA-eligible image is pinned by default |
