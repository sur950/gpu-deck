# T-Head PPU

Status: **fixture-only and unverified, including the tool name itself** —
`apps/node-agent/crates/gpudeck-devices/src/thead.rs`: "Status: fixture-only; tool name and
field support are unverified until hardware is available." This is the least-verified
accelerator in the catalog. Send a hardware report if you try this —
[SUPPORT.md](../SUPPORT.md).

## Detection

The T-Head PPU SDK ships an `nvidia-smi`-compatible management CLI; the plugin assumes it is
called `ppu-smi` on `PATH` (override the path with `GPUDECK_PPU_SMI_PATH`) and queries it with
the same `--query-gpu=... --format=csv,noheader,nounits` dialect as NVIDIA, reusing the NVIDIA
CSV parser (`nvidia::smi`). The SDK/driver version is read from the tool's banner output, trying
the keys `"PPU SDK Version"`, `"SDK Version"`, then `"PPU Version"` in order.

## What runs on it

Only **llama.cpp** has `thead` in its `supported_vendors`. `artifacts.py`'s `ACCELERATOR_ORDER`
has no entry for `thead` at all (unlike Hygon and Moore Threads), so a T-Head node's
`candidate_keys()` only matches the plain CPU platform key — llama.cpp runs, but on the CPU, not
the PPU.

**No other built-in engine lists `thead` in `supported_vendors`** — not vLLM, not any other
engine (`001_init/up.sql`). vLLM's own vendor list is
`{nvidia,amd,hygon,metax,moorethreads}` — T-Head is the one accelerator in GPU Deck's catalog
that is not even nominally listed there. To run anything else on a T-Head node, install it
yourself and set `engine_paths`.

## What the agent installs vs. what you install

| | Agent | You |
| --- | --- | --- |
| Driver / PPU SDK | Nothing — reads `ppu-smi` (path overridable) | The T-Head driver and PPU SDK, with its management CLI on `PATH` or pointed at by `GPUDECK_PPU_SMI_PATH` |
| llama.cpp | Downloads the pinned CPU build (no PPU-accelerated build exists) | `libgomp1` |

## Slicing

`slicing.rs::slice_support(Vendor::Thead)`: `SliceMode::MemoryCap`, memory not isolated, compute
not enforced, no granularity set. The source comment: "T-Head PPU: memory cap through engine
flags; device selection env name is unverified until hardware is available." The container
device-arg builder sets `PPU_VISIBLE_DEVICES`, but this too is unverified.

## Multi-GPU and multi-node

Not specified — no PPU-accelerated engine is pinned.

## Known limitations

- Every field above, including the tool name `ppu-smi` itself, is unverified against real
  hardware.
- No engine uses the PPU; llama.cpp falls back to the CPU build.
- No other built-in engine lists T-Head as a supported vendor at all.

## Prepare this machine (checklist)

```text
[ ] Install the T-Head driver and PPU SDK
[ ] Confirm its management CLI (assumed `ppu-smi`) lists your device(s), or set GPUDECK_PPU_SMI_PATH
[ ] Install libgomp1 for the llama.cpp CPU build (it will not use the PPU)
[ ] Allow outbound 8443 (hub) and 8444-8445 (API-layer tunnels) to the control plane, and 443
    to Hugging Face / ModelScope (or your mirror); no inbound port is needed
[ ] Direct mode only (private networks): inbound 9443 from the API layer's addresses
[ ] Run the install command from Nodes -> Add node in the web UI
[ ] Send a hardware report either way (docs/SUPPORT.md) — nothing here has been run on real hardware
```

## Troubleshooting

| Refusal / symptom | Meaning |
| --- | --- |
| No devices appear in inventory | `ppu-smi` (or your `GPUDECK_PPU_SMI_PATH`) is not reachable, or its CSV output does not match the NVIDIA-style fields the parser expects |
| llama.cpp runs but ignores the PPU | Expected currently — no PPU-accelerated build is pinned |
| Any other engine refuses with `vendor_unsupported` | Expected — only llama.cpp lists `thead` as a supported vendor in the built-in catalog |
