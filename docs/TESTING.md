# Testing and hardware coverage

This page says what GPU Deck 1.0.0 has been run against for real, what is covered only by
automated tests with recorded fixtures, and where reports from people with other hardware or
cloud accounts would help most. If you run GPU Deck on something listed under "needs reports",
please open an issue with the agent version, the device/driver versions and what worked.

## How the release was tested

- **Control plane** on macOS (Apple Silicon) with PostgreSQL 17 and Redis 7.4 in Docker: admin
  API, serving API, agent hub, workers, scheduler and the web UI.
- **One real GPU node**: an Apple Silicon Mac (Metal) running the agent with llama.cpp and MLX.
- **Linux nodes in containers**: the agent and llama.cpp built for aarch64 Linux, two to four
  containers (Debian 12 and Ubuntu 24.04, some booting systemd) joined to the same control plane.
  Each container reports one *simulated* NVIDIA GPU through a stand-in `nvidia-smi`; llama.cpp
  really runs on the CPU. This exercises everything that needs more than one node (scheduling,
  routing, failover, draining, automation rules) without GPU hardware. The setup is in
  `apps/node-agent/testing/multinode/`.
- **No model downloads**: tests use small GGUF models already on the machine (qwen3 0.6B,
  TinyLlama 1.1B).

Automated suites: the backend suite (unit + integration against real PostgreSQL/Redis, ~740
tests), the agent's Rust tests on macOS and Linux (including an end-to-end run against a fake
hub over mTLS with every frame validated against the protocol schemas), web unit tests and
Playwright flows, CLI tests and the Terraform provider's acceptance test.

## Verified on real systems

| Area | What was run |
| --- | --- |
| Install and join | One-line installer served by the hub (pinned CA, sha256-checked agent download) on macOS (per-user LaunchAgent) and on Linux as root under systemd (service user `gpudeck`); multi-use join tokens (a 2-use token enrolled two nodes, the third use was refused). |
| Engines | llama.cpp router mode on macOS (Metal) and Linux (CPU), including downloading the pinned upstream build, sha256 check and unpacking by the agent; MLX on macOS. `/slots` and `/metrics` parsing, live per-slot sessions. |
| Serving | OpenAI, Anthropic and Ollama formats through the official SDKs: chat, streaming, tools, completions, responses, embeddings, messages, batches. Size limits, timeouts and context overflow errors in each format. |
| Routing | Requests spread across nodes through each node's mTLS proxy; least-sessions, round-robin and weighted strategies; failover to another replica when a node drops off the network; a node whose certificate is revoked is cut off. |
| Scheduling | Spread placement, rolling updates that keep replicas spread, VRAM-aware placement on tight devices, node selectors. |
| Draining | Node drain with "move instances": copies load on other nodes first, traffic keeps flowing (600 of 600 requests succeeded during a drain), then the originals unload. |
| Automation | `idle_unload`, time-scheduled layouts, layouts that move models between nodes (unload before load on a full device), `make_room_by_priority` (evicts the lowest-priority idle model, never `always_on`), `auto_heal` (restart, then move to another node when restarts do not help; a hung engine process is detected). |
| Benchmarks | Load tests at several concurrency levels and auto-tuning of launch flags, on Metal and on Linux CPU nodes. |
| Security | Login lockout, CSRF, session revocation, IP allow/block rules for the admin and serving APIs, vault key rotation, mTLS proxy path policy, certificate revocation, pickle-only weights blocked by default. |
| Egress lock (Linux) | nftables rules applied for real by the agent (as a non-root service with `CAP_NET_ADMIN`): the hub and the model hubs on 443 reachable, everything else rejected, inbound limited to the proxy and SSH ports. |
| Metering | Usage events with token counts, hourly rollups, cost per price plan, GPU-minutes from uptime, CSV exports. |
| Tooling | CLI against a live control plane; Terraform provider acceptance test plus apply/plan/destroy of the complete example; Docker Compose stack with non-root, read-only containers; macOS desktop app. |

## Covered by automated tests only

These code paths are exercised with recorded or documented tool output, mocks or fakes, but not
yet on the real thing:

- **NVIDIA**: the NVML device plugin, the `nvidia-smi` fallback (ran against a simulated tool),
  MIG, Xid error tracking, GPU slices (MPS limits) and CUDA work inside an SSH instance.
- **AMD** (`amd-smi` / `rocm-smi`), **Hygon** (`hy-smi`), **MetaX** (`mx-smi`), **Moore Threads**
  (`mthreads-gmi`), **T-Head** (`ppu-smi`): parsers tested against output taken from vendor
  documentation.
- **Engines**: vLLM-Omni, SGLang Diffusion, whisper.cpp, stable-diffusion.cpp, mlx-audio and
  custom engines; vLLM, SGLang and TensorRT-LLM (native or as containers), and llama.cpp
  CUDA builds.
- **Multi-GPU and multi-node models**: tensor and pipeline parallel (including cross-node),
  prefill/decode split, shared KV cache, speculative decoding.
- **Cloud GPU VMs** on AWS, Azure, GCP and DigitalOcean (recorded API responses), cloud-init
  bootstrap on a real GPU image, idle auto-terminate.
- **Kubernetes** DaemonSet on GPU nodes.
- **Egress lock on macOS** (pf anchor rules are rendered and unit-tested; applying them needs root).
- Vision, speech, rerank, image and video model types.

## Needs reports from the community

Most useful, in order:

1. Linux + NVIDIA (any data-center or consumer GPU): agent inventory, VRAM numbers, llama.cpp CUDA
   build, vLLM or SGLang container deployments, MIG/MPS slices, SSH instances with GPUs.
2. Linux + AMD Instinct or Radeon (ROCm): inventory and a vLLM/llama.cpp deployment.
3. A cloud account (any of the four providers): one provision → join → terminate cycle.
4. Multi-node tensor/pipeline parallel with real interconnects.
5. Other accelerators listed above.

## Known requirements and limits

- The upstream llama.cpp Linux builds that GPU Deck pins need **glibc 2.38 or newer** (Ubuntu
  24.04, Debian 13 or later), except the x86_64 CPU and Vulkan builds (glibc 2.34+), and
  `libgomp1` (plus `libvulkan1` for the Vulkan build). On older distributions the agent uses an
  older pinned build (ROCm), the official llama.cpp image when Docker or Podman is installed, or
  an engine you installed (`engine_paths`); see
  [DEPLOYMENT.md](DEPLOYMENT.md#linux-requirements-per-platform). The container fallback has
  not run on a GPU machine yet.
- Engines keep running while the agent restarts or is upgraded and are re-adopted on their
  ports; only the node proxy is down for the restart itself (about a second or two). In the
  Kubernetes DaemonSet the engines live in the agent's container and restart with it.
- Native SGLang needs the CUDA toolkit on the machine (its kernels are compiled at run time); the
  agent checks for it before loading and names what is missing, and the container image includes
  it. TensorRT-LLM deployments do not parse tool calls or reasoning yet.
- When several CPU-backed engines share a machine, set `threads` in the deployment's launch flags;
  llama.cpp otherwise starts one thread per core per process and the processes slow each other
  down sharply.
- Blocked outbound connections under the egress lock are logged and counted by the kernel
  (`gpudeck-egress-blocked`); the agent raises an alert only when the lock cannot be applied.

## Running the multi-node setup yourself

1. Start the dev stack (`./infra/scripts/bootstrap.dev.sh bootstrap`) and make the hub reachable
   from containers: set `AGENT_HUB_PUBLIC_URL` to an address containers can resolve (for example
   `https://host.docker.internal:8443`) and add that name to `AGENT_HUB_TLS_SANS`.
2. Build the test node image: `docker build -t gpudeck-testnode apps/node-agent/testing/multinode`.
3. Start one or more nodes on a shared Docker network (see the comment at the top of the
   Dockerfile), mounting a directory with GGUF files read-only at the same path on every node.
4. Create a join token in the web UI (Fleet → Nodes → Join) and run its install command inside
   each container. Set `FAKE_GPU_MEM_MIB` in `/etc/fake-gpu.env` to make a node's simulated GPU
   smaller.
5. Register the GGUF files as local models and deploy them; they are scheduled onto the simulated
   GPUs and run on the CPU.
