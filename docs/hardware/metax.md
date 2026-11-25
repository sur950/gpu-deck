# MetaX

Status: **fixture-only and unverified** — the device plugin's parser is tested against a format
taken from MetaX documentation and public logs, not a real MetaX GPU
(`apps/node-agent/crates/gpudeck-devices/src/metax.rs`: "Status: fixture-only, format from MetaX
documentation / public logs (unverified)."). Slicing support is explicitly marked unverified in
the source too (see below). Send a hardware report if you try this —
[SUPPORT.md](../SUPPORT.md).

## Detection

The plugin looks for `mx-smi` on `PATH` and runs it with no arguments. `mx-smi`'s default summary
table mirrors `nvidia-smi`'s layout: two table rows per GPU (`index name | bus-id | util` then
`temp power/cap | used/total MiB`), with the kernel-mode driver version and MACA runtime version
read from the banner (`"Kernel Mode Driver Version"`, `"MACA Version"`). Reported per device:
name, VRAM (used/total, parsed as MiB or GiB depending on the unit in the row), driver version,
MACA version (as `runtime_version`), PCI bus id, power draw and cap, utilization, temperature.

## What runs on it

`llama_cpp`'s `supported_vendors` includes `metax`, but `artifacts.py`'s `ACCELERATOR_ORDER` has
**no entry for `metax` at all** — unlike Hygon and Moore Threads, there is no accelerator-specific
build mapped for it. A MetaX node's `candidate_keys()` therefore only ever matches the plain
`linux/x86_64` (or `linux/aarch64`) key — the **CPU build**. llama.cpp is supported on MetaX in
the sense that it runs, but it does not use the MACA GPU.

`vllm`'s `supported_vendors` also includes `metax`, but — as with AMD and Hygon — only the
NVIDIA CUDA image is pinned by default; nothing MACA-specific ships. SGLang, vLLM-Omni, SGLang
Diffusion, TensorRT-LLM, MLX, mlx-audio, whisper.cpp and stable-diffusion.cpp do not list `metax`
in `supported_vendors` — not available.

## What the agent installs vs. what you install

| | Agent | You |
| --- | --- | --- |
| Driver / MACA | Nothing — reads `mx-smi` | The MetaX driver and MACA user space, `mx-smi` on `PATH` |
| llama.cpp | Downloads the pinned CPU build (no MACA-accelerated build exists) | `libgomp1` |
| Container runtime | Nothing | Docker, Podman or nerdctl if you add a container engine version yourself |

## Slicing

`slicing.rs::slice_support(Vendor::Metax)`: `SliceMode::MemoryCap`, memory not isolated, compute
not enforced, no granularity set. The source comment: "MetaX MACA exposes no partitioning API to
user space; slices are memory caps enforced by engine flags and accounted by the agent.
(unverified: vendor SDK)." Device selection env vars: `MACA_VISIBLE_DEVICES`,
`CUDA_VISIBLE_DEVICES` (MACA's CUDA-compatibility layer also honours the latter).

## Multi-GPU and multi-node

Not specified — no MACA-accelerated engine is pinned, so there is nothing to run tensor or
pipeline parallel on yet beyond llama.cpp's CPU build, which does not use multi-GPU parallelism
on a single node in a MetaX-specific way.

## Known limitations

- Every field above is unverified against real hardware.
- No engine currently uses the MACA GPU — llama.cpp falls back to the CPU build, and no
  MACA-accelerated image is pinned for vLLM.
- `mx-smi`'s table format is a best guess; a different layout from your `mx-smi` build may parse
  as empty rather than wrong fields.

## Prepare this machine (checklist)

```text
[ ] Install the MetaX driver and MACA user space; confirm `mx-smi` lists your GPU(s)
[ ] Install libgomp1 for the llama.cpp CPU build (it will not use the MACA GPU)
[ ] Allow outbound 8443 (hub) and 8444-8445 (API-layer tunnels) to the control plane, and 443
    to Hugging Face / ModelScope (or your mirror); no inbound port is needed
[ ] Direct mode only (private networks): inbound 9443 from the API layer's addresses
[ ] Run the install command from Nodes -> Add node in the web UI
[ ] Send a hardware report either way (docs/SUPPORT.md) — this plugin has not seen real hardware
```

## Troubleshooting

| Refusal / symptom | Meaning |
| --- | --- |
| No devices appear in inventory | `mx-smi` is not on `PATH`, or its table layout does not match the parser's expectations |
| llama.cpp runs but ignores the GPU | Expected currently — no MACA-accelerated build is pinned; it is the CPU build |
| vLLM never offered on this node | Expected — no MACA-eligible image is pinned by default |
| No driver-version refusal or warning ever appears for MetaX | Expected — `compat.py`'s driver check only compares NVIDIA and AMD versions (`_DRIVER_KIND`); MetaX's MACA version is reported in inventory but not checked against a minimum |
