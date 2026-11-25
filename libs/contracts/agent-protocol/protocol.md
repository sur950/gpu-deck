# GPU Deck agent protocol (v1)

Contract between the Rust node agent (`apps/node-agent`) and the control plane's agent hub (`services/agent_hub`) on **:8443**, plus the data path the API layer uses to reach engines: the agent's **outbound tunnel** to the API layer (default, §13) or the agent proxy on **:9443** (direct mode, §6). Message shapes are the JSON Schemas in this folder; this file covers transport, lifecycle and semantics.

## 1. Transport

| Item | Value |
| --- | --- |
| Hub endpoint | `wss://<AGENT_HUB_HOST>:8443/agent/v1/ws` (env `AGENT_HUB_PORT`) |
| Join endpoint | `https://<AGENT_HUB_HOST>:8443/agent/v1/join` (same listener, plain TLS, see §2) |
| TLS | TLS 1.3 only. Server cert for the hub is issued by the internal CA (vault credential `agent-hub-tls`) or given via `AGENT_HUB_TLS_CERT_PATH/KEY_PATH`. Client auth **required** on `/agent/v1/ws`: the agent presents its node certificate (`CN=<node_id>`, `O=gpudeck-nodes`), chained to the internal CA, not in the CRL. |
| Framing | One JSON object per WebSocket **text** frame = one envelope (`envelope.json`). No batching of envelopes. Max frame 4 MiB; the agent splits `metrics_batch` and `event(log)` to stay under 1 MiB. Optional `permessage-deflate`. |
| Direction | The agent dials out. The control plane never connects to a node for control; in serving mode `tunnel` (default, §13) it never connects to a node at all. |
| Hub identity | The hub certificate must chain to the CA pinned at join **and**, once the agent holds a signed security policy (§13), its key (SubjectPublicKeyInfo sha256) must be one of `policy.hub.spki_sha256`. No redirects are followed; the join response's `hub_ws_url` is ignored (the agent only dials the `hub_url` it joined with). |
| Identity binding | The hub reads `node_id` from the client cert CN and rejects any frame whose `envelope.node_id` differs (close code 4001). |
| Keepalive | WebSocket ping from the hub every 15 s; agent must pong. Plus the application `heartbeat` message every `intervals.heartbeat_seconds` (10 s). |
| Offline detection | No heartbeat for `NODE_OFFLINE_AFTER_SECONDS` (30 s) → `nodes.status = offline`, instances on it → `health = unknown`, API layer routing drops them. |

Close codes used by the hub: `4001` identity mismatch, `4002` protocol error (schema violation; the offending `id` is in the close reason), `4003` rejected (see `hello_ack.reason`), `4004` superseded (a second connection for the same node replaced this one), `1012` hub restarting.

## 2. Join flow (one-time token → certificate)

1. Admin creates a join token: `POST /api/v1/nodes/join-tokens` → plaintext `gdj_<40 hex>` shown once, SHA-256 stored in `join_tokens`, TTL `JOIN_TOKEN_TTL_MINUTES` (default 15).
2. Operator runs `gpudeck-agent join --hub https://hub:8443 --token gdj_... [--name mac-1] [--ca-fingerprint <sha256>]`. The agent:
   - generates a key pair (`ecdsa_p256` default) in `<state_dir>/agent.key` (0600, agent user only);
   - builds a CSR with `CN=pending`, `O=gpudeck-nodes` (the hub overwrites the subject when signing);
   - `POST /agent/v1/join` (TLS, no client cert) with body:

```json
{
  "join_token": "gdj_...",
  "csr_pem": "-----BEGIN CERTIFICATE REQUEST-----...",
  "hostname": "mac-1", "name": "mac-1",
  "os": "macos", "arch": "aarch64", "agent_version": "1.0.0", "protocol_version": 1,
  "proxy_port": 9443, "labels": {"zone": "office"}
}
```

   The hub verifies the token (unused, unexpired, unrevoked; the use is claimed atomically, so a token is never used twice), creates the `nodes` row (`status = pending`, `cluster_id` from the token, name = `name` or `node_name_hint` or hostname, must be unique in the cluster), signs the CSR with the internal CA (`CN=<node_id>`, `O=gpudeck-nodes`, SAN `URI:gpudeck://node/<node_id>`, validity `NODE_CERT_VALIDITY_DAYS` default 365), inserts `node_certificates` (`kind=node_agent`), marks the token used, audits `node.joined`. Response `200`:

```json
{
  "node_id": "…", "node_name": "mac-1", "cluster_id": "…",
  "certificate_pem": "…", "ca_bundle_pem": "…", "serial": "…", "not_after": "2027-11-16T…Z",
  "hub_ws_url": "wss://hub:8443/agent/v1/ws",
  "intervals": { "heartbeat_seconds": 10, "metrics_seconds": 2, "inventory_seconds": 60 }
}
```

   Since 2026-11-24 the response also carries `security` (`common.json#/$defs/SignedPolicy`): the node security policy of §13, signed with the CA key. The agent verifies it against the CA it just pinned (and for its own `node_id`) before it stores anything, and from then on pins the hub and API-layer keys.

   Errors use the admin envelope: `401 JOIN_TOKEN_INVALID`, `410 JOIN_TOKEN_EXPIRED`, `409 NODE_NAME_TAKEN`, `422 CSR_INVALID`. The join endpoint is rate limited (`10/minute` per IP).
3. The agent stores cert + CA bundle in `<state_dir>/`, writes `node_id` to `<state_dir>/agent.toml`, and opens the WebSocket. **`--ca-fingerprint` is required** (since 2026-11-24): `gpudeck-agent join` refuses to run without it unless `--insecure-skip-ca-pin` is given, and the automatic join of `run` needs `GPUDECK_AGENT_CA_FINGERPRINT` (or `GPUDECK_AGENT_INSECURE_SKIP_CA_PIN=1`); every installer passes it. The agent first calls `GET https://<hub>:8443/agent/v1/ca` (**unauthenticated**, no client cert, returns the CA bundle PEM), checks the SHA-256 of the root against the pin, and only then posts to `/agent/v1/join` verifying the hub certificate against that CA. The hub must keep `/agent/v1/ca` unauthenticated.
4. Revocation: `POST /api/v1/nodes/{id}/certificates/{cert_id}/revoke` sets `revoked_at`, pushes `config_push.crl` to every agent (their proxies refuse the API layer? no — the CRL on agents is for *client* certs hitting :9443; the hub itself re-reads the CRL and closes the revoked node's socket with 4003), and the API layer drops the node from routing. A removed node (`DELETE /nodes/{id}`) gets all its certs revoked.
5. Rotation: hub sends `rotate_cert` (default 30 days before `not_after`, `NODE_CERT_ROTATE_BEFORE_DAYS`), agent returns `csr_pem` in the result, hub signs and sends `install_cert`, agent reconnects with the new cert and says `hello`; the old cert is revoked 10 minutes after the new one connected.

## 3. Connection lifecycle

```
agent                                hub
  |--- TLS (mTLS) + WS upgrade ------->|
  |--- hello ------------------------->|  validate cert CN == nodes.id, status != removed
  |<-- hello_ack (correlation=hello) --|  accepted | rejected(+close 4003)
  |<-- config_push (full) -------------|  only if config_version differs
  |--- event(config_applied) --------->|
  |--- inventory --------------------->|  authoritative snapshot; reconcile devices/instances/cache
  |<-- command (replay of pending) ----|  every agent_commands row in (pending, sent) for this node
  |--- command_result (accepted|…) --->|
  |--- heartbeat every 10 s ---------->|
  |--- metrics_batch every 2 s ------->|
  |--- event(...) as things happen --->|
  |<-- command / config_push ----------|
```

**Reconnect and backoff.** On any close or error the agent reconnects with exponential backoff starting at 1 s, factor 2, full jitter, cap 60 s; reset after a connection that stayed up 60 s. During disconnect the agent keeps serving (engines keep running, proxy keeps proxying), buffers `event`s (ring buffer `EVENT_BUFFER_MAX`, default 10 000; `log` events are dropped first) and terminal `command_result`s, and **drops** `metrics_batch` and `heartbeat`. It keeps draining/unloading operations it had already started.

**Replay after reconnect.** Hub side: every `agent_commands` row for the node with `state IN (pending, sent)` and `expires_at > now()` is re-sent with the **same envelope id**. Agent side: a command id it has already seen is not re-executed; it replies with the latest known `command_result` for that id (running/terminal). Agent persists the set of seen command ids and their terminal results to `<state_dir>/commands.json` (last 1 000) so this survives an agent restart.

**Single connection per node.** A new accepted connection for the same `node_id` closes the previous one with `4004`.

**Round-trip time.** The hub sends a WebSocket ping every `HUB_PING_INTERVAL_SECONDS` (RFC 6455 control frame; every agent answers with a pong, no protocol frame needed) and closes a connection whose pong does not come back within the same interval. The time to each pong is the control plane → agent round trip, kept as a smoothed value with jitter per node (admin API `GET /metrics/latency/live`, history `GET /metrics/latency`). The serving path's round trip is measured separately by every API layer (serving.md §5).

## 4. Ordering, acks, idempotency

- Frames on one connection are delivered in order (WebSocket guarantee). Across reconnects there is no ordering guarantee between `metrics_batch`es, so every sample carries its own `ts`.
- **Commands are idempotent by envelope id** (§3). The hub generates the id when it inserts `agent_commands` and uses it as the correlation id on results.
- **Ack** = the first `command_result` with `status: accepted` (within `intervals.command_ack_timeout_seconds`, default 10 s). No ack → hub marks the command `timed_out` after `expires_at`, and the swap manager treats that as failure.
- Commands touching the same `instance_id` or `engine_process_id` are serialized on the agent in arrival order; others run concurrently (bounded by `AGENT_MAX_CONCURRENT_COMMANDS`, default 4; downloads additionally by `AGENT_MAX_CONCURRENT_DOWNLOADS`, default 2).
- `cancel` applies to running downloads, benchmarks and drain waits; a cancelled command ends with `failed(code=cancelled)`.
- `config_push` is idempotent on `config_version`; the agent acks with `event(kind=config_applied, details.config_version)`.
- `metrics_batch`, `heartbeat`, `inventory` are never acked. `event` is at-least-once (hub dedups on envelope id for 10 minutes).

## 5. Versioning

- `envelope.v` is the protocol major. `hello.protocol_version` must equal the hub's supported major or the hub rejects with `protocol_unsupported`.
- Additive changes (new optional fields, new enum values the receiver may ignore, new event kinds) do not bump `v`. Receivers must ignore unknown **event kinds** and unknown **enum values in `extra`/`details`**; they must **reject** unknown top-level fields (schemas use `additionalProperties: false`) so drift is caught in tests.
- New commands need a hub-side capability check: agents >= 1.1.0 list the commands they implement in `hello.capabilities.commands` (and `capabilities.ssh_instances`); older agents list them implicitly by `agent_version`; the hub keeps a `min_agent_version` per command (hub setting `COMMAND_MIN_AGENT_VERSIONS`) and refuses to send unsupported ones (`agent_commands.state = rejected`, error `unsupported_by_agent`).
- Schema `$id`s carry `/v1/`; a `v2` directory would hold incompatible schemas.

## 6. Agent proxy on :9443 (data path, direct mode)

The agent runs a reverse proxy in front of the engines on the node. Engines bind to `127.0.0.1` only. The same request handler serves the outbound tunnel (§13, the default serving mode: no listener at all); this section is the direct mode (`serving_mode = "direct"` in `agent.toml`, a local opt-in the control plane cannot change), where the proxy is the only thing listening on a network interface.

| Item | Value |
| --- | --- |
| Listener | `https://<nodes.proxy_address>:<nodes.proxy_port>` (default 9443), TLS 1.3, **client certificate required**. HTTP/2 (ALPN `h2`: many concurrent requests over one connection, one handshake per node) or HTTP/1.1 with keep-alive. Bound to `proxy_bind`: `auto` = `proxy_address` when it is an address of the node, else the first private address (RFC 1918, CGNAT, ULA), else loopback — never every interface unless set explicitly. |
| Before TLS | The source address must be in `policy.direct.allowed_source_cidrs` (§13; empty = private ranges and loopback); otherwise the connection is reset without a byte. Per source, handshakes are limited to `handshakes_per_minute` (burst `handshake_burst`), and at most `max_pending_handshakes` run at once; excess connections are reset. |
| Accepted clients | Inside the handshake: chained to the internal CA, not in the CRL from the last `config_push`, matching the subject rules (`policy.api_layer.subjects`, default `CN=api-layer,O=gpudeck-services`; without a policy `config_push.settings.proxy.allowed_client_subjects`, default `O=gpudeck-services`) and, once a policy pins them, carrying one of the API layer's keys (`policy.api_layer.spki_sha256`). Anything else gets a TLS alert, never an HTTP response. |
| Rejections | Counted per reason (`source_not_allowed`, `rate_limited`, `too_many_pending_handshakes`, `no_client_certificate`, `untrusted_client_certificate`, `client_identity_rejected`, `tls_incompatible`, `tls_handshake_failed`, `handshake_timeout`) and source, reported as one `event(ingress_rejected)` per interval (§13). |
| Addressing | Path prefix: `/instances/{instance_id}/{engine_path}`. The proxy looks `instance_id` up in its routing table (from `config_push.routing`), checks `engine_path` starts with one of `allowed_path_prefixes`, and forwards to `http://127.0.0.1:{port}/{engine_path}` with the query string unchanged. |
| Unknown instance | `404 {"error":{"code":"instance_not_found"}}` |
| Draining instance (`accepting=false`) | `503` + `Retry-After: 2` + `{"error":{"code":"instance_draining"}}`. In-flight requests continue. |
| Disallowed path | `403 {"error":{"code":"path_not_allowed"}}` (engine admin endpoints such as llama.cpp `/slots`, `/models/load`, `/props` POST, `/metrics` are never allowed from the network). |
| Model name rewrite | When `rewrite_model=true` and the body is JSON with a top-level `model`, the proxy replaces it with `served_model_name` (the API layer already does this; the proxy is the safety net for router-mode engines). Multipart bodies are passed through untouched. |
| Headers in | The API layer sends `X-Request-ID` (uuid), `X-GPUDeck-Api-Key-Id`, `X-GPUDeck-Deployment`, `X-GPUDeck-Format` (openai/anthropic/ollama). The proxy records them for session samples (`SessionSample.request_id`) and strips `X-GPUDeck-*` before forwarding to the engine. |
| Headers out | Adds `X-GPUDeck-Node: <node_id>`, `X-GPUDeck-Instance: <instance_id>`, echoes `X-Request-ID`, and `X-GPUDeck-Proxy-Timing: queue;dur=<ms>, engine;dur=<ms>, node;dur=<ms>` (Server-Timing syntax): waiting for a concurrency slot, request sent to the engine → its response headers, request accepted by the proxy → response headers sent. The API layer derives the request's network time from it (its own upstream wait − `node`), removes it and reports the phases in `Server-Timing` (serving.md §10). A header the engine itself sent with that name is overwritten. Older API layers ignore it. |
| Streaming | Transparent: SSE and chunked bodies are flushed as received (no buffering, `X-Accel-Buffering: no`). WebSocket upgrade is **not** supported in v1 (no realtime API). |
| Timeouts | `settings.proxy.request_timeout_seconds` (default 600 total), idle-stream timeout 120 s, `max_body_bytes` (default 64 MiB; the API layer enforces the public limits first). |
| Concurrency | Optional `max_concurrent` per instance; excess waits up to `queue_timeout_seconds` (default 30) then `503 {"error":{"code":"instance_busy"}}`. Queued requests count as `waiting_sessions` in derived metrics. |
| Health | `GET /instances/{id}/_health` → `200 {"state":"ready","health":"healthy","active_sessions":n,"waiting_sessions":n}` or `503`. `GET /_health` → proxy itself. |
| Metrics derivation | The proxy measures every request: start, first body byte (TTFT for streams), end, status, bytes, and parses `usage` from JSON responses / final SSE frames. This is the universal metrics source for engines without `/metrics` (`metrics_config.derive_from_proxy=true`) and the source of `active_sessions`/`waiting_sessions` for all engines when the engine does not expose them. See `metrics.md` §4. |
| Host firewall | §13: inbound to the proxy port only from the allow-listed sources (tunnel mode: from nobody), metrics and engine ports never from the network, the operator's SSH ports always open. With the egress lock (opt-in) outbound goes only to the hub, the tunnel endpoints, the model sources (`config_push.model_sources` + `egress_allowlist`) and localhost, and inbound is default deny besides the above, the SSH-instance range (§8) and, while a cross-node group runs, its peers (§9). |

Example: API layer → `POST https://10.0.0.5:9443/instances/6f1c…/v1/chat/completions` with body `{"model":"qwen3-0.6b", …}` → proxy → `POST http://127.0.0.1:10240/v1/chat/completions` with `{"model":"qwen3-0.6b-q4","…"}`.

## 7. Agent local files and runtime

| Path (Linux / macOS) | Purpose |
| --- | --- |
| `/etc/gpudeck/agent.toml` / `/usr/local/etc/gpudeck/agent.toml` | `hub_url`, `node_id`, `state_dir`, `cache_dir`, `proxy_port`, `labels`, `log_level`, `engine_paths` |
| `<state_dir>` = `/var/lib/gpudeck` / `/usr/local/var/gpudeck` | `agent.key`, `agent.crt`, `ca.pem`, `config.json` (last config_push), `commands.json`, `instances/<id>.json` / `engine_processes/<id>.json` (last LoadModelArgs / StartEngineArgs + port, pid and pid start time, for re-adoption and restart), `logs/<id>.log` / `<id>.err.log` (engine stdout / stderr, written by the engine itself, rotated at 16 MiB), `tmp/` (engines' `TMPDIR`; the systemd unit binds it at `/tmp`), `hub_features.json` (last `hello_ack.features`) |
| `<cache_dir>` = `<state_dir>/cache/<model_version_id>/<path>` | model files; `.partial` suffix while downloading; `.sha256ok` marker after verification; `.gpudeck-hashes.json` (sha256 of node files by path + size + mtime, from `hash_files` and verifications: never re-read on load) |
| `<state_dir>/engines/<name>/<version>/` | extracted native engine artifacts |
| Prometheus | `http://127.0.0.1:9100/metrics` (agent self metrics + last scraped engine metrics, `AGENT_PROM_PORT`); loopback only, scraped through the proxy at `/_metrics` by the control plane if enabled |
| Service | systemd `gpudeck-agent.service` (user `gpudeck`, `ProtectSystem=strict`, device access via udev group, `KillMode=process`, `BindPaths=<state_dir>/tmp:/tmp`), launchd `io.github.sur950.gpudeck-agent.plist` (`AbandonProcessGroup`), Kubernetes DaemonSet `gpudeck-agent` (hostNetwork, device plugin mounts) |

**Engines outlive the agent.** Engines run detached from the agent: own session / process group (`setsid`), output to files instead of pipes, on Linux under systemd their own transient scope when the agent may create one (`systemd-run --scope`), otherwise the unit's `KillMode=process` leaves them running. An agent restart, crash or upgrade therefore does not touch them: the next agent process **re-adopts** every engine whose persisted pid is still the same process (pid + start time) and passes its health check (routers: plus the model loaded) — same pid, same port, no reload, nothing sent besides the normal `inventory` (no `engine_started`). Only engines that are gone (or unhealthy) are relaunched, on their previous port when it is free. Adopted engines are watched by polling (exit status unknown: `engine_crashed` carries no code / signal) and crash-restarted like the others. `gpudeck-agent stop-engines` stops them deliberately; agent setting `keep_engines_on_exit = false` restores stop-on-shutdown. In a Kubernetes DaemonSet the engines live in the agent's container and stop with it.

Secrets handling: `ModelSource.token` arrives inside commands over mTLS, is held in memory for the download only, never written to `commands.json` or logs (the agent redacts the field before persisting).

Sample documents for every message added after v1.0 live in `examples/` (`<schema>.<what>.json` validates against `<schema>.json`; `command.<Def>.json` against `command.json#/$defs/<Def>`). They are checked by `apps/node-agent/crates/gpudeck-protocol/tests/schema_validation.rs`. History: `CHANGELOG.md`.

## 8. SSH GPU instances (`start_ssh_instance` / `stop_ssh_instance`)

An SSH instance is a container with GPUs attached that a user logs into with their own key. The agent manages its whole lifecycle; the control plane only sends the two commands and reads `inventory.ssh_instances` / `event(ssh_instance_state_changed)`.

| Item | Rule |
| --- | --- |
| Runtime | Container only: `docker` (Docker Engine, OrbStack, Docker Desktop) or `podman` found on `PATH` (agent setting `container_runtime` to force one). No runtime → `failed(no_container_runtime)`; `hello.capabilities.ssh_instances=false`. |
| GPUs | NVIDIA: `--gpus "device=<index,...>"` (needs nvidia-container-toolkit). AMD/Hygon: `--device /dev/kfd --device /dev/dri/renderD<128+index> --group-add video` + `HIP_VISIBLE_DEVICES`. Other vendors: their `/dev` nodes per vendor docs, else no passthrough. Apple: containers cannot use Metal — the instance runs CPU-only and the result says `gpu_attached=false` with a warning. |
| Limits | `--cpus`, `--memory` (+ equal `--memory-swap`), `--shm-size`, `--pids-limit` from `limits`. VRAM/compute per device from `DeviceAssignment.slice`, else `limits.vram_bytes/compute_pct`: NVIDIA `CUDA_MPS_PINNED_DEVICE_MEM_LIMIT=0=<n>M`, `CUDA_MPS_ACTIVE_THREAD_PERCENTAGE` (enforced only with the MPS daemon; otherwise advisory and the result carries a warning), AMD `GPUDECK_DEVICE_CORES`; always `GPUDECK_VRAM_LIMIT_BYTES`, `GPUDECK_COMPUTE_PCT`. |
| Hardening | `--cap-drop ALL --cap-add CHOWN,SETUID,SETGID,DAC_OVERRIDE,FOWNER,SYS_CHROOT,AUDIT_WRITE,NET_BIND_SERVICE,KILL --security-opt no-new-privileges`, never `--privileged`, never host network, labels `io.github.sur950.gpudeck.ssh_instance=<id>` (+ `node_id`) for cleanup. |
| Image contract | Default image: `ghcr.io/sur950/gpu-deck-ssh:<version>` (`apps/node-agent/packaging/ssh-image`: Ubuntu 24.04 + OpenSSH on 2222 — glibc, so the host `nvidia-smi` / driver libraries the NVIDIA runtime mounts run inside; Alpine-based images such as `linuxserver/openssh-server` cannot run them). The image runs `sshd` on `container_port` (default 22). The agent provides the keys three ways so most images work unchanged: file `/etc/gpudeck/authorized_keys` (read-only mount), env `PUBLIC_KEY` (all keys, newline-joined) + `PUBLIC_KEY_FILE=/etc/gpudeck/authorized_keys` + `USER_NAME=<ssh_user>` (linuxserver/openssh-server convention), and `GPUDECK_SSH_USER`, `GPUDECK_AUTHORIZED_KEYS_FILE`. GPU Deck images should `AuthorizedKeysFile /etc/gpudeck/authorized_keys` and create `ssh_user`. Password login must be disabled in the image. |
| Volumes | `volume` → named volume `gpudeck-ssh-<id>-<source>` (removed on stop unless `keep`), `bind` → only below the agent `cache_dir` (forced read-only) or a root in agent setting `ssh_bind_roots`; anything else → `failed(invalid_args)`. `tmpfs` → `--tmpfs target:size=`. |
| **Network rule** | The container publishes sshd on **`127.0.0.1` only** (random host port). The only network listener is the agent's **TCP forwarder** on `bind_address:ssh_port` — default bind = agent setting `ssh_bind`, else the node's `proxy_address` when it is an address of the node, else its first private address, else loopback; a public or every-interface address is refused unless the node's own `ssh_bind` names it (the control plane cannot open one); port from `ssh_port_range` (default **2200–2299/tcp**), skipping the operator's SSH ports. The forwarder resets clients outside `allowed_source_cidrs`, else outside the policy's `ssh.default_allowed_cidrs`; **with neither, nobody gets in** (fail closed, since 2026-11-24: an empty list used to mean any) and the result carries a warning. Rejections are reported like the proxy's (§13). It counts connections (`active_connections`, `last_connection_at` → hub idle-TTL) and is closed first on stop, cutting open sessions. The hub never proxies SSH: users reach `host_address:host_port` directly over the private network/VPN; the host firewall opens the range only to those CIDRs. Never expose the range to the public internet. Keys only: no password login, no root login in the default image. |
| Host key | After start the agent waits for the SSH banner, reads the host keys (`ssh-keyscan` against the loopback port, else `cat /etc/ssh/ssh_host_*_key.pub` inside the container) and returns OpenSSH fingerprints (`SHA256:<base64>`); the UI shows them for first-connect verification. |
| Lifecycle | `starting` → `running` (banner + host key) → `stopping` → `stopped`; `error` when the container exits or health fails (health = container running + loopback TCP connect, every 15 s). `ttl_seconds` → the agent stops it at `expires_at`. State persists in `<state_dir>/ssh/<id>/instance.json`; after an agent restart running containers are re-adopted (forwarder re-opened on the same port), unknown `gpudeck-ssh-*` containers are left alone and reported. `stop_ssh_instance` is idempotent. |

## 9. Cross-node pipeline parallel (`launch.distributed`)

The scheduler splits a model's layers over nodes. The hub sends one `load_model` (or `start_engine`) per node, **same `instance_id`**, each with its own `devices` and `launch.distributed.rank` (`common.json#/$defs/DistributedSpec`). Rank 0 (head) is the only rank with an HTTP API and the only one in `config_push.routing`; workers (rank > 0) are reported with `InstanceStatus.distributed_rank > 0` and must not change `instances.node_id`/`device_ids` on the hub. Send workers first; unload the head first.

| Engine | Head (rank 0) | Workers (rank > 0) |
| --- | --- | --- |
| vLLM | `--pipeline-parallel-size <pp_size> --nnodes W --node-rank 0 --master-addr A --master-port P` | same + `--headless` (no API server) |
| SGLang | `--pp-size <pp_size> --nnodes W --node-rank 0 --dist-init-addr A:P` | same with `--node-rank R` (no API needed) |
| llama.cpp | `--rpc <addr:rpc_port of every worker>` (layers offloaded to the RPC backends) on a single-model `llama-server` (never a shared router; a router-mode template's `--models-dir` becomes `--model <file>`) | `ggml-rpc-server --host <bind_address> --port <rpc_port>` (`rpc-server` before the rename; binary next to `llama-server`) |
| others | env only (`MASTER_ADDR`, `MASTER_PORT`, `WORLD_SIZE`, `RANK`, `NODE_RANK`, `GPUDECK_DISTRIBUTED`) + template placeholders | same |

Workers are `ready` when their process is up (llama.cpp: `rpc_port` accepts TCP; others: alive for 5 s); the head when its HTTP health passes. A worker crash marks the worker instance `error`; the hub reloads the group.

**Network rule.** Group traffic (torch/NCCL/Gloo rendezvous on `master_port`, NCCL data ports, llama.cpp RPC) flows **directly between the nodes' private addresses**, not through the :9443 proxy and not through the hub. Nodes of one group must be on the same private network (or VPN). When the egress lock is on, the agent opens, for the life of the group: outbound to every peer `address`, inbound from every peer address (TCP, any port — NCCL picks ephemeral ports), and nothing else. llama.cpp `ggml-rpc-server` has **no authentication**: it binds only `bind_address` (never `0.0.0.0` unless explicitly set) and with the egress lock accepts only peers; without it the operator must firewall `rpc_port` to the group.

## 10. Stable device ids

The agent derives every device id itself and the hub must store the same value as `devices.id`:

```
device_id = uuid5(namespace = UUID(node_id), name = f"{vendor}:{index}")    # e.g. "nvidia:0", "apple:0"
```

`vendor` is the `device_vendor` enum value, `index` the vendor ordinal (`Device.index`). Python: `uuid.uuid5(uuid.UUID(node_id), f"{vendor}:{index}")`. Agents >= 1.1.0 send it as `Device.device_id` in inventory; it is used for `GpuSample.device_id`, `InstanceStatus.device_ids`, `SshInstanceStatus.device_ids`, `event.device_id`, `engine_processes[].device_ids`. If a command's `DeviceAssignment` carries a different `device_id` for a `(vendor, index)`, the agent learns and reuses it (`<state_dir>/devices.json`) for backward compatibility with hubs that minted random ids; such a hub should migrate to the uuid5 ids. Ids survive agent restarts and reboots; a device moved to another index gets the id of its new index (the vendor `uuid` is in `Device.uuid` / `GpuSample.extra.uuid` to detect that).

## 11. Node-side file commands (`scan_path`, `hash_files`)

`scan_path` walks a path on the node (read-only, `~` expanded to the agent user's home, symlinks to files resolved, directory symlinks not followed, hidden directories skipped below the root except `.lmstudio`/`.ollama`) and returns `ScanCandidate`s shaped like admin.yaml `LocalScanCandidate` plus `absolute_path` per file and raw `metadata`. A diffusers pipeline (`model_index.json` at a directory's root) is **one** candidate with every component file (paths relative to the root, e.g. `transformer/diffusion_pytorch_model-00001-of-00005.safetensors`; pickle weights next to safetensors optional), `suggested.type` `image` / `video`, `suggested.pipeline_class`, `metadata.pipeline = "diffusers"`. `suggested` carries `name`, `family`, `type`, `quantization` (GGUF file type, then the name — `FP8`, `Q4_K_M`, `AWQ` —, then `config.json` `quantization_config`: fp8, awq, gptq, bitsandbytes, compressed-tensors, modelopt, mxfp4, MLX bits), `context_size` (`max_position_embeddings`, also inside `text_config`) and `architecture`, with the same rules as the control plane's `local_scanner.py` / `detect.py` (earlier agent builds left `family` / `type` `null`); a value still `null` is the control plane's to fill. Importing a candidate uses `ModelSource{kind: local_path}` with the files' absolute paths; the agent uses them in place (never copies or deletes them). `hash_files` computes sha256 (1 MiB reads, one file at a time, `running` progress frames) for files the control plane wants to pin; the agent also keeps the hashes (path + size + mtime) so a later `load_model` of those files does not read them again. A `load_model` whose `local_path` files have a `sha256` the agent has not hashed yet starts the engine and verifies in the background (agent setting `verify_local_files`); a mismatch is reported on the cache item (`cache_changed`, `error_message`). Both run as the agent user, can only read what that user can read, and are cancellable with `cancel`.

## 12. Observed (external) engines

A node can run model servers GPU Deck did not start (a vLLM someone launched by hand, a FastAPI image service). `agent.toml` lists them as `[[observe]]` entries; the agent watches them **read-only** and the control plane shows them as **external** deployments with one instance each. GPU Deck never acts on them.

| Item | Rule |
| --- | --- |
| Ids | `observe_id = uuid5(UUID(node_id), "observe:<name>")` (the instance id), `deployment_id = uuid5(UUID(node_id), "observe-deployment:<name>")`. The hub ignores entries whose ids are not those of the reporting node. |
| Sources | `url`: only `GET <url><metrics_path>` (default `/metrics`), `GET <url>/health`, `GET <url>/v1/models` (every 60 s); 2 s timeout, no redirects, no proxy, capped bodies, one poll per `interval_seconds` (default 10), backoff 2 s doubling to 60 s while it does not answer. `log_file`: opened read-only (never locked, written or truncated), followed from the end, rotation by inode and truncation by size detected, at most 1 MiB read per cycle. With no `url` the entry makes no network request. |
| Metrics | `/metrics` is parsed with the managed adapters' parsers (vLLM, SGLang, llama.cpp Prometheus only, never `/slots`) plus a TGI mapping; `openai` = health + models, `generic` = health only. From the log: vLLM / SGLang periodic stats lines (sessions, throughput, KV cache, prefix-cache hit rate; token totals estimated from the logged throughput; no TTFT / end-to-end histograms) and access lines (uvicorn, gunicorn, nginx combined: request totals, status classes, top endpoints, latency only when the line carries it). Probe paths (`/health`, `/metrics`, `/v1/models`) and progress bars are not counted. Samples carry `observed: true`. |
| Logs | New log lines are sent as `event(log)` for the external instance: obvious credentials redacted (`Bearer`, `api_key=`, `token:`, `sk-` / `hf_` / `gd-` / AWS / JWT shapes), progress bars and probe lines dropped, at most 200 lines or 128 KiB per entry per metrics interval (a summary line counts the rest). |
| Processes | `listen_port` (default: the port of a local `url`) → the listening socket in `/proc/net/tcp{,6}` → the pid holding it (`/proc/<pid>/fd`, needs the server's user, root or `CAP_SYS_PTRACE`), or `pid_cmd_contains` → pids whose command line contains the text (matched locally, never reported). The matched pids and all their descendants (vLLM's EngineCore child holds the VRAM) are tagged in `GpuSample.processes[].instance_id`, summed into `EngineSample.process_vram_bytes` and reported as `ObservedEngine.pid` / `device_ids`. |
| Never | Start, stop or signal the process; bind or bind-test its port (observed local ports are excluded from the agent's port allocation); send anything but the GETs above. |
| Hub | Creates / updates the external deployment (`origin = external`, no catalog model or engine version) and instance (`traffic_weight = 0`, no port) from each inventory, independent of the node's status (a drained node keeps them). Entries no longer reported become stale (instance `stopped`) and are removed after a day. The scheduler, reconciler, rules, swaps, layouts, routing table, benchmarks and command dispatch skip them; every mutating admin API answers `409 EXTERNAL_DEPLOYMENT`. Their engine samples land in the normal metric tables; no usage events are created. |
| Compatibility | The agent sends `EngineSample.observed`, observed log events, `inventory.observed_engines` and `GpuSample.processes` only when `hello_ack.features` lists `observed_engines` / `gpu_processes`. `hello.observed_engines` goes out from the first `hello` (the last `hello_ack.features` is kept in `<state_dir>/hub_features.json`) unless that hub is known not to list `observed_engines`; a hub that closes a first-contact connection before `hello_ack` gets hellos without it. |

## 13. Serving modes, the outbound tunnel and the node security policy (since 2026-11-24)

**Serving modes.** `agent.toml` `serving_mode` (local opt-in; the control plane cannot open a listener on a node):

| Mode | Data path | Inbound ports on the GPU machine |
| --- | --- | --- |
| `tunnel` (default) | The agent dials every API-layer tunnel endpoint of the policy and serves the API layer's requests over those connections | **none** |
| `direct` | The API layer dials the agent proxy (§6) | `proxy_port` from the allow-listed sources only |

The agent reports its mode in `hello.proxy.mode` (and the listen address in `hello.proxy.bind`) to hubs listing the `secure_transport` feature; the hub stores it in `nodes.metadata.serving_mode`, and the routing table addresses a tunnel node as `proxy_url = tunnel://<node_id>`. Hubs and agents without the feature keep the direct proxy.

**Outbound tunnel.** TLS 1.3 from the agent to `policy.tunnel.endpoints` (`host:port`, one per API-layer worker), `policy.tunnel.connections` connections per endpoint (default 2), ALPN `h2` only. The agent presents its node certificate; the API layer requires it (chain to the internal CA, `O=gpudeck-nodes`, `CN = <node_id>` with the matching `URI:gpudeck://node/<id>` SAN, not in the CRL). The agent accepts the server only when its certificate chains to the pinned CA, matches `policy.api_layer.subjects` and carries one of `policy.api_layer.spki_sha256` (the host name is not the trust anchor). On the connection the roles flip: the API layer is the HTTP/2 client, the agent serves the §6 request handler (routing, path allow-list, model rewrite, drain → 503 + `Retry-After`, concurrency caps, in-flight accounting, unbuffered SSE) with `max_concurrent_streams` 1024. The API layer sends `GET /_health` on every connection every `AGENT_TUNNEL_KEEPALIVE_SECONDS` (also its liveness probe); the agent closes a connection on which nothing arrived for `policy.tunnel.idle_timeout_seconds` and redials (1 s ×2 backoff, cap 60 s). A node with no open tunnel is unreachable to that API-layer process: requests fail with a connect error and the serving pipeline fails over / ejects as for an unreachable proxy.

**Signed security policy** (`security_policy.json`, carried as `common.json#/$defs/SignedPolicy` in the join response and in `config_push.security`): base64 JSON bytes plus a signature by the internal CA key (`ecdsa-p384-sha384` for the default P-384 CA, `ecdsa-p256-sha256`, `ed25519`, `rsa-pkcs1-sha256`). The agent

1. verifies the signature with the CA it pinned at join (never a key that arrives with the policy; the algorithm follows the CA key type),
2. refuses a policy for another `node_id` or with a `serial` lower than the one it holds (rollback; the hub keeps the serial monotonic per node in `nodes.metadata.security_serial`),
3. refuses the **whole** `config_push` when `security` does not verify (`event(config_rejected)`), and
4. persists the signed form in `<state_dir>/security_policy.json`, re-verified on every start (`gpudeck-agent security reset-policy` forgets it).

| Section | Applies to |
| --- | --- |
| `hub.spki_sha256` | Hub TLS (§1): current key, the pre-published next key and, for `SERVICE_KEY_ROTATION_GRACE_HOURS` after a rotation, the previous key |
| `api_layer.spki_sha256`, `subjects` | The only client the direct proxy accepts and the only server the tunnel accepts (same pin rules) |
| `tunnel.endpoints`, `connections`, `idle_timeout_seconds` | The only hosts the agent dials for serving |
| `direct.allowed_source_cidrs`, `handshakes_per_minute`, `handshake_burst`, `max_pending_handshakes` | §6 "Before TLS" (cluster `config.node_security.proxy_allowed_source_cidrs`, else `NODE_PROXY_ALLOWED_SOURCE_CIDRS`, else private ranges) |
| `ssh.default_allowed_cidrs` | §8 forwarders without their own CIDRs (cluster `config.node_security.ssh_default_allowed_cidrs`, else `SSH_INSTANCE_DEFAULT_ALLOWED_CIDRS`, else nobody) |
| `ingress_report.interval_seconds`, `burst_threshold` | `event(ingress_rejected)`: one event per interval with rejected attempts (`details`: `total`, `reasons`, `listeners`, top 20 `sources`, `distinct_sources`), `critical` at the burst threshold — the hub raises a `node.ingress_rejected` alert for critical ones only |

**Key rotation without lock-out.** Service certificates are re-issued for the same key (renewal never changes a pin). Every service key has a pre-published *next* key that agents pin from the start; `python -m app.services.pki.cli rotate-key --service agent-hub|api-layer` promotes it, generates a new next key and re-pushes the policy; the previous key stays pinned during the grace period while replicas restart. A revoked service certificate moves to the next key automatically. An agent that missed a rotation entirely is recovered with `gpudeck-agent security reset-policy` (or a re-join).

**Host firewall** (`gpudeck-agent firewall status|apply|remove`, nftables table `inet gpudeck` / pf anchor `com.apple/gpudeck`, programmed by the installer as root and kept current by the agent with `CAP_NET_ADMIN`; `firewall = "off"` opts out): the operator's SSH ports (sshd configuration, listening sshd sockets, the installing session's `SSH_CONNECTION`, `firewall_preserve_ports`) are accepted first; the proxy port only from the direct-mode sources (tunnel mode: from nobody); the SSH-instance range only from the instances' CIDRs; the metrics port and the engine port range never from the network. The ruleset carries a plan hash; `firewall status` exits 1 when the loaded rules are not the current plan.

**Self-check.** At start the agent lists its listening sockets with the `gpudeck-agent security audit` rules (metrics and engine ports must be loopback; tunnel mode: nothing on the proxy port; direct mode: proxy on a private address) and logs findings; an engine left running that answers on a network interface is not re-adopted but stopped and relaunched on loopback.

## 14. Agent self-update (`stage_update` / `apply_update`)

Nodes move to a new agent version without anyone checking for one and without model downtime. The control plane decides the version (the newest signed release it has, or `AGENT_UPDATE_TARGET_VERSION`); everything is capability-gated: hubs list the feature `agent_updates` in `hello_ack.features`, agents list `stage_update` and `apply_update` in `hello.capabilities.commands`. Agents that predate it keep working; they are shown as "update manually" and are never sent the commands. An agent sends `hello` / `inventory.agent_update` (`common.json#/$defs/AgentUpdateStatus`) and `event(agent_update)` only to a hub known to list the feature.

| Item | Rule |
| --- | --- |
| Release manifest | `gpudeck-agent-<version>-manifest.json` (`common.json#/$defs/ReleaseManifest`): per target the release archive and the `gpudeck-agent` binary inside it (sha256, size). `...manifest.json.sig` is the standard base64 of the raw ed25519 signature over the exact file bytes, made by the release workflow with the release signing key. The control plane offers a release only when it verifies with `AGENT_RELEASE_PUBLIC_KEYS`. |
| Key distribution | The release public keys reach nodes in the CA-signed security policy (`security_policy.json` `release.public_keys`, §13: join response and `config_push.security`), plus optional `release_public_keys` in `agent.toml`. A key that arrives any other way is never trusted. |
| `stage_update` | Sent ahead of time (pre-staging, `AGENT_UPDATE_PRESTAGE`) or when an admin updates. Carries the signed manifest and a download token bound to the node, version and target (`AGENT_UPDATE_TOKEN_TTL_SECONDS`, only its sha256 is stored). The agent verifies the signature, picks its own target, checks free disk space (two copies plus a margin), downloads `GET <hub_url>/agent/v1/updates/<version>/<target>` from the hub it joined (pinned TLS, its node certificate, header `X-GPUDeck-Update-Token`; never another URL), verifies the sha256 and size, runs `<binary> version` to check it runs on the host and keeps it in `<state_dir>/update/staged/<version>/` (one staged version at a time). The running binary is not touched. Air-gapped control planes serve the archive from `AGENT_DIST_DIR`. |
| `apply_update` | Nothing is downloaded. The agent re-verifies the staged binary (signature + sha256) and writes `<state_dir>/update/request.json`. The **applier** (`gpudeck-agent update apply`) verifies again, copies the binary next to the installed one and verifies the copy, keeps the installed binary as `<binary>.prev` (hard link) and renames the new one over it (atomic), restarts the service, then waits up to `health_timeout_seconds` for `update/health.json` from the new agent. Otherwise it renames `.prev` back, restarts again and reports `rolled_back` (or `failed` when the previous agent does not come back either). |
| Who applies | `helper`: a root-owned helper the installers add (`gpudeck-agent update install-helper`: systemd `gpudeck-agent-update.path` + `.service`, launchd daemon `io.github.sur950.gpudeck-agent-update`) runs the applier when `request.json` changes, so the binary stays root-owned and the agent unprivileged. Running as root it trusts only keys anchored outside the agent's writable state: `agent.toml` keys, and policy keys only from a policy that verifies against the CA certificate whose fingerprint `agent.toml` pins; it opens state files with `O_NOFOLLOW`. `self`: the agent user owns the binary (per-user macOS install) and a service manager restarts it; the agent starts the applier itself. `managed`: Kubernetes (the DaemonSet image is updated with Helm), never. `unsupported`: update by re-running the installer (`reason` says why). |
| Healthy | The new agent re-adopted its engines (§7: same pids and ports, no reload), reconnected and got `hello_ack`. It then writes `health.json` and the applier records `succeeded`. Engines keep running throughout; only the agent's own connections (hub, tunnels or proxy) are down for the restart. |
| Progress | `event(agent_update)` `details.phase`: `staging`, `staged`, `stage_failed` (from staging); `swapping`, `restarting` (the old agent); `readopting` (with `adopted` / `relaunched` counts), `health_check`, then `succeeded`, `rolled_back` or `failed` (the agent that runs after the restart). The hub also counts a disconnect while swapping as `restarting` and a reconnect with the new version as `readopting`. The `apply_update` result is sent by that agent too: `succeeded` with the steps, or `failed` with code `rolled_back`. |
| Control plane | One update run per node (`agent_update_runs`), rolling updates per cluster (`agent_update_rollouts`: one node at a time, stop at the first rolled-back or failed node), policy per cluster in `clusters.config.agent_updates` (`manual`, or `auto` inside a maintenance window). While a node updates its offline alert is held back (`nodes.metadata.agent_update_active`). |
| Errors | `update_unsupported`, `signature_invalid`, `not_staged`, `update_in_progress`, `insufficient_disk`, `artifact_download_failed`, `artifact_checksum_mismatch`, `rolled_back`. |
