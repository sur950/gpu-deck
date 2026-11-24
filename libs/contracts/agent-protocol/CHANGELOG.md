# Agent protocol changelog

All changes are additive within protocol major `v1` (`envelope.v = 1`); older agents/hubs ignore or never send the new parts. Agent builds implementing them report `agent_version >= 1.0.0` (the first public release) and list them in `hello.capabilities.commands`.

## 1.5 — 2026-11-24

No new frames or fields; behaviour found on real hardware (H200):

- **Engines outlive the agent** (protocol.md §7): an agent restart / upgrade no longer stops engines; the next agent process re-adopts them (pid + start time + health) with the same pid and port and sends no `engine_started` for them; only engines that are gone are relaunched, on their previous port. `engine_crashed` of an adopted engine has no exit code / signal (not observable).
- **`hello.observed_engines`** is sent from the first `hello` (the last `hello_ack.features` is remembered across restarts); it is withheld only from a hub known not to list `observed_engines`, or after a hub closed the connection before `hello_ack` on a first contact.
- **`scan_path`** (§11): diffusers pipelines are one candidate (component paths like `transformer/x.safetensors`, `suggested.pipeline_class`, `metadata.pipeline`); `suggested.family` / `type` are filled; quantization also from `config.json` `quantization_config` (FP8, AWQ, GPTQ, compressed-tensors, ...), context also from `text_config`.
- **`hash_files`** hashes are remembered by the agent (path + size + mtime): a later `load_model` of the same `local_path` files does not re-read them; unhashed ones are verified in the background (agent `verify_local_files`), a mismatch shows on the cache item.
- **SSH instances** (§8): default image `ghcr.io/sur950/gpu-deck-ssh` (Ubuntu + OpenSSH, glibc).
- **Proxy :9443** (§6): HTTP/2 via ALPN `h2` besides HTTP/1.1 keep-alive.
- **vLLM `EngineSample.requests_total`** is the engine's own count of finished requests (was the proxy's count only: requests that bypass the proxy, e.g. benchmarks on loopback, left it at 0).

## 1.4 — 2026-11-23

- **Observed (external) engines** (protocol.md §12): `inventory.observed_engines[]` and `hello.observed_engines[]` (`common.json#/$defs/ObservedEngine`: `observe_id` = uuid5(node_id, "observe:<name>"), `deployment_id` = uuid5(node_id, "observe-deployment:<name>"), name, engine (`observed_engine_kind`: vllm, sglang, llamacpp, tgi, openai, generic), `host:port` endpoint, sources, models, version, health, pid, device_ids), `EngineSample.observed` for their samples, and `event(log)` frames for their log lines (`instance_id` / `deployment_id` = the observed ids). The hub registers them as external deployments and never sends commands for them.
- **Per-process GPU memory**: `GpuSample.processes[]` (`GpuProcess`: pid, used_memory_bytes, executable name only, `instance_id` of the managed instance or observed engine whose process tree holds it). NVIDIA through NVML `running_compute_processes`.
- **Hub features**: `hello_ack.features[]` (`observed_engines`, `gpu_processes`). Agents send the new fields only to a hub that lists them, so a newer agent keeps working with an older hub.

## 1.3 — 2026-11-23

- **Host compatibility of engine builds**: `Artifact.min_glibc` (oldest host glibc the build runs on), `Artifact.fallbacks[]` (same-accelerator builds for older systems, tried in order), `Artifact.container` (`{image, entrypoint, version}`: the engine's official image, run when no native build fits and a container runtime is available). The agent picks the build (operator `engine_paths` first) and otherwise fails with `unsupported_engine` and an explanation. `hello.capabilities.glibc_version` reports the host glibc so the control plane can choose too.
- **llama.cpp pipeline parallel**: the head is a single-model `llama-server` (never a shared router) with `--rpc`; workers run `ggml-rpc-server` (the b12500 name; `rpc-server` still found).

## 1.2 — 2026-11-23

- **Pinned engine builds**: `Artifact.kind` adds `python_venv` (a managed venv with the pinned wheel and its dependencies, `pip_extras`, `python`) and `source` (a pinned source archive built with CMake, `build`); `Artifact.parts[]` (further sha256-pinned archives unpacked into the same folder, e.g. a CUDA runtime bundle, with `dest` / `strip_components`) and `Artifact.strip_components`. The agent refuses an all-zero sha256.
- **Container engines** run their template's first word as the container entrypoint (`--entrypoint`), independent of the image's own ENTRYPOINT.
- Hub HTTP (no frame changes): `GET /agent/v1/ca.pem` (the CA as PEM, pinned by sha256 in the install command); `install.sh` and `downloads/` are what GPU machines use, so they need outbound 8443 only. Join tokens may be multi-use (`max_uses`).

## 1.1 — 2026-11-17

- **Commands** `scan_path` (model candidates under a node path: Ollama, LM Studio, HF cache, MLX, plain), `hash_files` (sha256 with streaming `running` progress), `start_ssh_instance` / `stop_ssh_instance` (SSH GPU containers). Result shapes: `command.json#/$defs/{ScanPathResult,HashFilesResult,SshInstanceResult}`.
- **Cross-node pipeline parallel**: `LaunchSpec.distributed` (`common.json#/$defs/DistributedSpec`: rank, world_size, master_addr/port, bind_address, rpc_port, nccl_socket_ifname, peers) on `load_model` / `start_engine`; `InstanceStatus.distributed_rank`.
- **SSH instances in state**: `inventory.ssh_instances[]` (`SshInstanceStatus`), event kind `ssh_instance_state_changed` + `event.ssh_instance_id`.
- **Stable device ids**: `Device.device_id` = `uuid5(node_id, "<vendor>:<index>")` (protocol.md §10).
- **Capabilities**: `hello.capabilities.commands[]`, `hello.capabilities.ssh_instances`.
- New error codes: `path_not_found`, `permission_denied`, `no_container_runtime`, `image_pull_failed`, `ssh_instance_not_found`, `distributed_unsupported`.
- protocol.md: §2 `GET /agent/v1/ca` (unauthenticated, pinned join), §8 SSH instances (network rule, image contract), §9 PP network rule, §10 device ids, §11 scan/hash; sample documents in `examples/`.

## 1.0 — 2026-11-16

Initial protocol (hello, hello_ack, inventory, metrics_batch, event, command, command_result, config_push, heartbeat; 13 commands).
