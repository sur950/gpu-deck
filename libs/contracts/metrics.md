# Metrics catalog (v1)

Every metric GPU Deck collects, where it comes from per engine and per device vendor, where it lands in Postgres, and how it is exposed to Prometheus. Consumers: node agent scrapers, metrics ingest, rollups and exporter, rules, API layer metering, the web dashboards and the Grafana dashboards.

Conventions

- **Canonical name** is the key used in `GET /api/v1/metrics/*` (`metrics=` query) and in `TimeSeries.metric`. Units are in the name where ambiguous (`_bytes`, `_pct`, `_ms`, `_watts`, `_c`).
- **Prometheus name** = `gpudeck_` + canonical name with Prometheus unit conventions (`_seconds`, `_bytes`, `_total` for counters, `_ratio` 0-1 for percentages — the exporter divides `_pct` by 100 and renames to `_ratio`). Labels are listed per table.
- **Type**: gauge (G), counter (C, monotonic since engine/agent start; rollups store deltas), histogram (H, only in process-level Prometheus metrics; in the DB we store avg/p50/p95 per sample).
- **Raw samples** every `METRICS_INTERVAL_SECONDS` (2 s) → `metric_samples_*` (48 h). **1-minute rollups** → `metric_rollups_*_1m` (90 d). **Hourly usage** → `usage_rollups_hourly` (kept).
- Where an engine exposes nothing, the agent **derives** the metric from its mTLS proxy (§4). Derived metrics are marked `proxy`.
- Anything the agent cannot provide is sent as `null` (never 0).

## 1. GPU / device metrics → `metric_samples_gpu`, `metric_rollups_gpu_1m`

Prometheus labels: `node`, `node_id`, `device_id`, `vendor`, `index`, `device_name`.

| Canonical name | Unit | Type | Column | Prometheus | NVIDIA (NVML) | AMD (`amd-smi metric --json`) | Hygon / MetaX / Moore Threads / T-Head | Apple Silicon |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `gpu_utilization_pct` | % | G | `utilization_pct` | `gpudeck_gpu_utilization_ratio` | `nvmlDeviceGetUtilizationRates().gpu` | `gfx.gfx_activity` (fallback `usage.gfx_activity`) | `hy-smi` "GPU use (%)"; `mx-smi` GPU util; `mthreads-gmi -q` "GPU utilization"; T-Head SMI util column | IOKit `IOAccelerator` → `PerformanceStatistics["Device Utilization %"]` (fallback `"GPU Activity(%)"`) |
| `gpu_memory_util_pct` | % | G | `memory_util_pct` | `gpudeck_gpu_memory_utilization_ratio` | `UtilizationRates().memory` (memory controller busy) | `umc.umc_activity` | vendor SMI "Memory use (%)" | null |
| `gpu_memory_used_bytes` | bytes | G | `memory_used_bytes` | `gpudeck_gpu_memory_used_bytes` | `nvmlDeviceGetMemoryInfo_v2().used` | `mem_usage.used_vram` (MB→bytes) | SMI "Used Memory" | `IOAccelerator` `PerformanceStatistics["In use system memory"]` (bytes); fallback `"Alloc system memory"` |
| `gpu_memory_total_bytes` | bytes | G | `memory_total_bytes` | `gpudeck_gpu_memory_total_bytes` | `MemoryInfo.total` | `mem_usage.total_vram` | SMI total | `MTLDevice.recommendedMaxWorkingSetSize` (via `Metal` FFI) — the Metal wired limit; `devices.is_unified_memory = true` |
| `gpu_power_watts` | W | G | `power_watts` | `gpudeck_gpu_power_watts` | `nvmlDeviceGetPowerUsage()` mW/1000 | `power.socket_power` (W) | SMI power column | IOReport `GPU Power` channel (`IOReportCopyChannelsInGroup("Energy Model")`, no root); null if unavailable |
| `gpu_power_limit_watts` | W | G | `power_limit_watts` | `gpudeck_gpu_power_limit_watts` | `nvmlDeviceGetEnforcedPowerLimit()` | `power.power_cap` (`amd-smi static`) | SMI cap when printed | null |
| `gpu_temperature_c` | °C | G | `temperature_c` | `gpudeck_gpu_temperature_celsius` | `nvmlDeviceGetTemperature(NVML_TEMPERATURE_GPU)` | `temperature.edge` (hotspot in `extra`) | SMI temp column | SMC via `AppleSMC` IOKit keys `Tg0f`/`Tg05`/`TGDD` (model dependent, best effort) |
| `gpu_memory_temperature_c` | °C | G | `memory_temperature_c` | `gpudeck_gpu_memory_temperature_celsius` | `nvmlDeviceGetFieldValues(NVML_FI_DEV_MEMORY_TEMP)` | `temperature.mem` | null | null |
| `gpu_sm_clock_mhz` | MHz | G | `sm_clock_mhz` | `gpudeck_gpu_sm_clock_hertz` | `nvmlDeviceGetClockInfo(NVML_CLOCK_SM)` | `clock.gfx_0.clk` | SMI clock | null |
| `gpu_memory_clock_mhz` | MHz | G | `memory_clock_mhz` | `gpudeck_gpu_memory_clock_hertz` | `ClockInfo(NVML_CLOCK_MEM)` | `clock.mem_0.clk` | SMI mem clock | null |
| `gpu_fan_pct` | % | G | `fan_pct` | `gpudeck_gpu_fan_ratio` | `nvmlDeviceGetFanSpeed()` | `fan.speed` / `fan.max` | SMI fan | null |
| `gpu_encoder_pct` / `gpu_decoder_pct` | % | G | `encoder_pct`, `decoder_pct` | `gpudeck_gpu_encoder_ratio`, `_decoder_ratio` | `nvmlDeviceGetEncoderUtilization()`, `GetDecoderUtilization()` | `mm.mm_activity` (both) | null | null |
| `gpu_pcie_tx_bytes_per_s` / `_rx_` | B/s | G | `pcie_tx_bytes_per_s`, `pcie_rx_bytes_per_s` | `gpudeck_gpu_pcie_tx_bytes_per_second` | `nvmlDeviceGetPcieThroughput(TX/RX)` KB/s×1024 | `pcie.pcie_bandwidth` | null | null |
| `gpu_ecc_errors_total` | count | C | `ecc_errors_total` | `gpudeck_gpu_ecc_errors_total` | `nvmlDeviceGetTotalEccErrors(UNCORRECTED, AGGREGATE)` | `ecc.total_uncorrectable_count` | null | null |
| `gpu_error_count` | count | G (per interval) | `error_count` | `gpudeck_gpu_errors_total` (exporter accumulates) | Xid events via `nvmlDeviceRegisterEvents` (`nvmlEventTypeXidCriticalError`) count since last sample | `ras` block error delta | SMI error counters delta | null |
| `gpu_throttle_reasons` | set | G | `throttle_reasons[]` | `gpudeck_gpu_throttled{reason}` 0/1 | `nvmlDeviceGetCurrentClocksEventReasons()` bitmask → names (`hw_slowdown`, `sw_thermal`, `power_cap`, ...) | `throttle_status` | null | null |
| `gpu_process_count` | count | G | `process_count` | `gpudeck_gpu_processes` | `nvmlDeviceGetComputeRunningProcesses_v3()` length | `amd-smi process` count | null | count of engine processes the agent runs on it |
| `gpu_energy_wh` | Wh | rollup only | `metric_rollups_gpu_1m.energy_wh` | `gpudeck_gpu_energy_watthours_total` | ∫ power over the minute | same | same | same |
| `gpu_throttled_seconds` | s | rollup only | `metric_rollups_gpu_1m.throttled_seconds` | — | seconds with any throttle reason set | same | — | — |

Static device fields (inventory, `devices` table, not time series): `name` (`nvmlDeviceGetName` / `asic.market_name` / SMI name / `MTLDevice.name`), `uuid` (`nvmlDeviceGetUUID` / `asic.unique_id` / SMI UUID or serial / `apple-<chip>-<hw.serial>`), `driver_version` (`nvmlSystemGetDriverVersion` / `driver.version` / SMI / macOS build), `runtime_version` (`nvmlSystemGetCudaDriverVersion` / ROCm version / MUSA / `Metal <family>`), `compute_capability` (`nvmlDeviceGetCudaComputeCapability` / `asic.target_graphics_version` / SMI / `metal3`), `pci_bus_id` (`nvmlDeviceGetPciInfo` / `bus.bdf` / SMI / null), `core_count` (`NVML_FI_DEV_...` not available → null; AMD `asic.num_compute_units`; Apple `system_profiler SPDisplaysDataType` "Total Number of Cores" via `sysctl`/IOKit `gpu-core-count`), `max_power_watts`, `health` (§5).

Vendor CLIs are parsed from recorded fixture outputs (`apps/node-agent/fixtures/<vendor>/`) until hardware is available; the exact field names for Hygon/MetaX/Moore Threads/T-Head must be confirmed against those fixtures and this table updated (open item).

## 2. Engine / instance metrics → `metric_samples_engine`, `metric_rollups_engine_1m`

Prometheus labels: `node`, `node_id`, `instance_id`, `deployment`, `deployment_id`, `model`, `model_id`, `engine`.

| Canonical name | Unit | Type | Column (sample / rollup) | Prometheus | llama.cpp | vLLM (V1) | SGLang | TensorRT-LLM (`trtllm-serve`) | MLX / mlx-audio / whisper.cpp / sd.cpp / custom |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `active_sessions` | count | G | `active_sessions` / `active_sessions_avg,max` | `gpudeck_engine_active_sessions` | `/metrics` `llamacpp:requests_processing`; cross-checked with `/slots` count `is_processing=true` | `vllm:num_requests_running` | `sglang:num_running_reqs` | `/metrics` JSON `num_active_requests` (iteration stats) | proxy: in-flight requests |
| `waiting_sessions` | count | G | `waiting_sessions` / `waiting_sessions_avg,max` | `gpudeck_engine_waiting_sessions` | `llamacpp:requests_deferred` | `vllm:num_requests_waiting` | `sglang:num_queue_reqs` | `num_queued_requests` | proxy: queued (over `max_concurrent`) + for MLX (single-threaded server) in-flight−1 |
| `slots_total` / `slots_idle` | count | G | `slots_total`, `slots_idle` | `gpudeck_engine_slots{state}` | `/slots` length / count `is_processing=false` | `--max-num-seqs` from launch flags / total−running | `--max-running-requests` / total−running | `max_num_active_requests` | launch `max_concurrency` / total−active |
| `requests_total` | count | C | `requests_total` / `requests_delta` | `gpudeck_engine_requests_total` | `llamacpp:n_decode_total` is not requests → use proxy count; llama.cpp has no request counter | `vllm:request_success_total` (sum over `finished_reason`) + errors | `sglang:num_requests_total` | derived: proxy | proxy |
| `requests_success_total` | count | C | `requests_success_total` / `requests_success_delta` | `gpudeck_engine_requests_success_total` | proxy (2xx) | `vllm:request_success_total` | proxy (2xx) | proxy | proxy |
| `errors_total` | count | C | `errors_total` / `errors_delta` | `gpudeck_engine_errors_total` | proxy (5xx + connection errors + engine crash) | proxy | proxy | proxy | proxy |
| `prompt_tokens_total` | tokens | C | `prompt_tokens_total` / `prompt_tokens_delta` | `gpudeck_engine_prompt_tokens_total` | `llamacpp:prompt_tokens_total` | `vllm:prompt_tokens_total` | `sglang:prompt_tokens_total` | `num_ctx_tokens` accumulated per iteration | proxy: `usage.prompt_tokens` / `input_tokens` |
| `generated_tokens_total` | tokens | C | `generated_tokens_total` / `generated_tokens_delta` | `gpudeck_engine_generated_tokens_total` | `llamacpp:tokens_predicted_total` | `vllm:generation_tokens_total` | `sglang:generation_tokens_total` | `num_gen_tokens` accumulated | proxy: `usage.completion_tokens` / `output_tokens` |
| `cached_tokens_total` | tokens | C | `cached_tokens_total` / `cached_tokens_delta` | `gpudeck_engine_cached_tokens_total` | Σ over `/slots` of `n_past`-style reuse is not exposed as a counter: use per-response `usage.prompt_tokens_details.cached_tokens` (llama.cpp ≥ b4xxx reports `timings.prompt_n` vs cached via `cache_prompt`) captured by the proxy; fallback `llamacpp:prompt_tokens_total − prompt_seconds-based estimate` not used | `vllm:prefix_cache_hits_total` (tokens) | `sglang:cached_tokens_total` when present, else `cache_hit_rate × prompt_tokens` | `kv_cache_stats.reused_blocks × tokens_per_block` | proxy: `usage.prompt_tokens_details.cached_tokens` / Anthropic `cache_read_input_tokens`; null if absent |
| `computed_tokens` (derived) | tokens | rollup | = `prompt_tokens_delta − cached_tokens_delta` | `gpudeck_engine_computed_prompt_tokens_total` | derived | derived | derived | derived | derived |
| `prompt_tps` | tok/s | G | `prompt_tps` / `prompt_tps_avg` | `gpudeck_engine_prompt_tokens_per_second` | `llamacpp:prompt_tokens_seconds` | `rate(vllm:prompt_tokens_total)` (agent computes from counter delta) | `rate(sglang:prompt_tokens_total)` | rate of `num_ctx_tokens` | proxy: Σ prompt tokens / interval |
| `generation_tps` | tok/s | G | `generation_tps` / `generation_tps_avg` | `gpudeck_engine_generation_tokens_per_second` | `llamacpp:predicted_tokens_seconds` | `rate(vllm:generation_tokens_total)` | `sglang:gen_throughput` | rate of `num_gen_tokens` | proxy: Σ output tokens / interval (stream: per-chunk timing) |
| `ttft_ms_avg` / `_p50` / `_p95` | ms | G/H | `ttft_ms_*` / `ttft_ms_avg,p95` | `gpudeck_engine_ttft_seconds` (H in exporter) | per request from `/slots` `next_token.n_decoded==1` timing is unreliable → proxy (first SSE chunk with content) | `vllm:time_to_first_token_seconds` histogram (agent derives avg/p50/p95 from bucket deltas) | `sglang:time_to_first_token_seconds` | derived from proxy | proxy (first body byte for streams; = e2e for non-stream) |
| `e2e_ms_avg` / `_p50` / `_p95` | ms | G/H | `e2e_ms_*` / `e2e_ms_avg,p95` | `gpudeck_engine_e2e_latency_seconds` | proxy | `vllm:e2e_request_latency_seconds` | `sglang:e2e_request_latency_seconds` | proxy | proxy |
| `tpot_ms_avg` | ms/token | G | `tpot_ms_avg` | `gpudeck_engine_time_per_output_token_seconds` | 1000 / `predicted_tokens_seconds` per slot | `vllm:time_per_output_token_seconds` | `sglang:time_per_output_token_seconds` | `iter_latency_ms` / gen tokens per iter | proxy: (e2e − ttft) / output tokens |
| `queue_ms_avg` | ms | G | `queue_ms_avg` | `gpudeck_engine_queue_time_seconds` | proxy: time a request waited before the first slot was assigned (approx. via `/slots` state) | `vllm:request_queue_time_seconds` | `sglang:queue_time` where exposed, else null | `queued → scheduled` from iteration stats | proxy: queue wait when `max_concurrent` applied |
| `kv_cache_used_tokens` | tokens | G | `kv_cache_used_tokens` | `gpudeck_engine_kv_cache_used_tokens` | `llamacpp:kv_cache_tokens` | `vllm:kv_cache_usage_perc × total` (total from launch/`num_gpu_blocks × block_size`) | `sglang:num_used_tokens` | `kv_cache_stats.used_num_blocks × tokens_per_block` | MLX: Σ per-session (prompt+generated) tokens of in-flight requests; others null |
| `kv_cache_total_tokens` | tokens | G | `kv_cache_total_tokens` | `gpudeck_engine_kv_cache_total_tokens` | Σ `/slots[].n_ctx` | blocks × block_size | `sglang:max_total_num_tokens` (static) | `max_num_blocks × tokens_per_block` | MLX: `context_size × max_concurrency` |
| `kv_cache_usage_pct` | % | G | `kv_cache_usage_pct` / `_avg,_max` | `gpudeck_engine_kv_cache_usage_ratio` | `llamacpp:kv_cache_usage_ratio × 100` | `vllm:kv_cache_usage_perc × 100` (older: `vllm:gpu_cache_usage_perc`) | `sglang:token_usage × 100` | used/max blocks | used/total |
| `kv_cache_bytes` | bytes | G | `kv_cache_bytes` | `gpudeck_engine_kv_cache_bytes` | tokens × bytes/token (from `model_versions.config`: `2 × n_layers × n_kv_heads × head_dim × dtype_bytes`, `K/V quant` from launch flags) | same formula | same | same | same |
| `prefix_cache_hit_rate` | ratio | G | `prefix_cache_hit_rate` | `gpudeck_engine_prefix_cache_hit_ratio` | null | `vllm:prefix_cache_hits_total / vllm:prefix_cache_queries_total` (delta) | `sglang:cache_hit_rate` | reused/allocated blocks | null |
| `spec_accept_rate` | ratio | G | `spec_accept_rate` | `gpudeck_engine_speculative_accept_ratio` | `/slots[].speculative` + `timings.draft_n_accepted/draft_n` from responses (proxy) | `vllm:spec_decode_num_accepted_tokens_total / num_draft_tokens_total` | `sglang:spec_accept_length` / num_draft | null | null |
| `process_vram_bytes` | bytes | G | `process_vram_bytes` | `gpudeck_engine_process_vram_bytes` | NVML `GetComputeRunningProcesses` by engine pid; Apple: `IOAccelerator` per-process not available → instance share = device used × (instance kv+weights) / Σ | same | same | same | same |
| `process_rss_bytes` | bytes | G | `process_rss_bytes` | `gpudeck_engine_process_rss_bytes` | `/proc/<pid>/status` VmRSS / macOS `proc_pidinfo(PROC_PIDTASKINFO).pti_resident_size` | same (container: cgroup memory.current) | same | same | same |
| `health` | enum | G | `health` / `healthy_seconds` | `gpudeck_engine_healthy` 0/1 | `GET /health` 200 (`{"status":"ok"}`; 503 while loading) | `GET /health` 200 | `GET /health` 200 (`/health_generate` optional deep check) | `GET /health` 200 | mlx_lm.server `GET /health` (≥ 0.22) else `GET /v1/models`; mlx-audio `GET /health`; whisper.cpp server `GET /` 200; sd.cpp server `GET /` ; custom: `engine_versions.health_config` |
| `error_rate_pct` | % | rollup | `metric_rollups_engine_1m.error_rate_pct` | `gpudeck_engine_error_ratio` | errors_delta / requests_delta × 100 | same | same | same | same |

Engine-specific extras go to `extra` (JSON) and are not promised: llama.cpp `llamacpp:n_busy_slots_per_decode`, `llamacpp:prompt_seconds_total`, `llamacpp:tokens_predicted_seconds_total`; vLLM `vllm:num_preemptions_total`, `vllm:request_prompt_tokens` histogram, `vllm:iteration_tokens_total`; SGLang `sglang:func_latency_seconds`; TRT-LLM `iter_latency_ms`, `num_scheduled_requests`; vLLM-Omni job queue depth (`/v1/videos` async jobs pending).

**llama.cpp specifics.** Scrape `GET /metrics` (needs `--metrics`) and `GET /slots` (`--slots`, on by default) on the engine port every 2 s; both are admin endpoints reachable only by the agent on localhost. `/slots` fields used: `id`, `id_task`, `n_ctx`, `is_processing`, `speculative`, `next_token[0].n_decoded`, `next_token[0].n_remain`; per-response `timings` (`cache_n`, `prompt_n`, `prompt_ms`, `predicted_n`, `predicted_ms`, `draft_n`, `draft_n_accepted`) and `usage.prompt_tokens_details.cached_tokens` captured by the proxy give exact per-request cached tokens, TTFT and TPOT.

*Verified on b8798 (`e39eba26f`, `~/.unsloth/llama.cpp/build/bin/llama-server`, Metal), 2026-11-17:*

- **Router mode** = `llama-server` started with `--models-dir DIR` and/or `--models-preset FILE.ini` (no `--model`). Flags: `--models-max N` (default 4, 0 = unlimited), `--models-autoload/--no-models-autoload` (GPU Deck always passes `--no-models-autoload` so only the agent loads models). Every load spawns a **child `llama-server` per model** on a random localhost port (`--port 0` in its argv) with the router's own CLI args merged in (so `--metrics`, `--host 127.0.0.1` etc. are inherited); SIGTERM to the router stops its children; the agent still kills the whole process group.
- **The model list is fixed at router start**: `--models-dir` (`<name>.gguf` → model id `<name>`) and the preset INI are read once; files added later are not seen. The agent therefore keeps one preset INI per router (`[<served_model_name>]` sections, keys = long CLI flag names without `--`, e.g. `model = /path.gguf`, `ctx-size = 8192`, `parallel = 2`, `n-gpu-layers = 99`, `jinja = true`, `alias`, `control-vector-scaled`), and a `load_model` for a name the router does not list rewrites the INI and restarts the router, then re-loads the models that were loaded before.
- **Admin endpoints** (router port): `GET /models` (also `/v1/models`) → `{"data":[{"id","aliases","tags","object":"model","owned_by":"llamacpp","created","status":{"value":"unloaded|loading|loaded|sleeping","args":[argv],"preset":"<ini>","exit_code"?,"failed"?:true}}],"object":"list"}`; `POST /models/load {"model":"<id>"}` → `{"success":true}` immediately (async; poll `/models` until `loaded`, or `failed:true`), `400 "model is already running"`, `404 "model is not found"`; `POST /models/unload {"model":"<id>"}` → `{"success":true}`, `400 "model is not running"` / `"model is not found"`. `GET /props` on the router returns `{"role":"router","max_instances","models_autoload",...}`.
- **Per-model routing**: POST bodies are routed by the JSON `model` field; GET endpoints need `?model=<id>`: `GET /metrics?model=<id>`, `GET /slots?model=<id>`, `GET /props?model=<id>` (without it: `400 "model name is missing from the request"`). `GET /health` is the router's own (always `{"status":"ok"}`); per-model readiness = `/models` status `loaded`. `engine_versions.metrics_config` for llama.cpp: `{"path":"/metrics","format":"prometheus","slots_path":"/slots","metric_prefix":"llamacpp:","scrape_interval_seconds":2}`; in router mode the agent appends `?model=<served_model_name>` to both paths.
- **`/metrics` on b8798** exposes only: `llamacpp:prompt_tokens_total`, `prompt_seconds_total`, `tokens_predicted_total`, `tokens_predicted_seconds_total`, `n_decode_total`, `n_tokens_max`, `n_busy_slots_per_decode`, `prompt_tokens_seconds`, `predicted_tokens_seconds`, `requests_processing`, `requests_deferred`. There is **no** `llamacpp:kv_cache_tokens` / `kv_cache_usage_ratio` in this build: `kv_cache_used_tokens` = Σ over processing slots of (prompt tokens + `next_token[0].n_decoded`) from the proxy's in-flight view, `kv_cache_usage_pct` = used / total.
- **Throughput is computed by the agent, not read** (finding of 2026-11-17): `llamacpp:prompt_tokens_seconds` / `predicted_tokens_seconds` are *lifetime* averages on b8798 (the metrics bucket is never reset), so `prompt_tps` / `generation_tps` = Δ`prompt_tokens_total` / Δt and Δ`tokens_predicted_total` / Δt between consecutive scrapes (aggregate tokens per wall second, 0 when idle; the table rows above naming `llamacpp:prompt_tokens_seconds` / `predicted_tokens_seconds` mean this rate). TPOT = Δ`tokens_predicted_seconds_total` / Δ`tokens_predicted_total`; per-slot decode speed goes to `extra.decode_tps_per_slot`.
- **KV cache is a lower bound** on b8798: with no `kv_cache_*` gauge and no `n_past` in `/slots`, `kv_cache_used_tokens` = Σ `next_token[0].n_decoded` of busy slots (+ prompt tokens when the proxy saw the request), flagged `extra.kv_cache_used_is_lower_bound = true`; `kv_cache_total_tokens` = Σ `/slots[].n_ctx` (max of them with `--kv-unified`). When a build exports `llamacpp:kv_cache_tokens` / `kv_cache_usage_ratio`, those win and the flag is absent.
- **Cross-node pipeline parallel** (`launch.distributed`, agent-protocol §9): only the head rank (0) serves HTTP and is scraped; worker ranks run `ggml-rpc-server` (`rpc-server` in older llama.cpp builds) and have no metrics endpoint (they report liveness only through inventory). The head's `/metrics` covers the whole group.
- **`/slots` on b8798** has no `n_past` / `cache_tokens`; idle slots carry only `id`, `n_ctx`, `speculative`, `is_processing`; processing slots add `id_task`, `params`, `next_token:[{has_next_token,has_new_line,n_remain,n_decoded}]` (an array). `n_ctx` per slot = `ctx-size / parallel`, **except** with the unified KV cache (the default when `--parallel` is not given: 4 slots each reporting the full context), so `kv_cache_total_tokens` = `/props?model=` `default_generation_settings.n_ctx` × `total_slots` only when `ctx-size` and `parallel` were both set, else max(`n_ctx`).

**MLX specifics (`mlx_lm.server`).** No metrics endpoint; single request at a time (serial HTTP server). The agent derives everything from the proxy plus process stats: `active_sessions` ∈ {0,1}, `waiting_sessions` = queued at proxy, tokens from `usage` in the response (stream: `usage` on the last chunk when `stream_options.include_usage` is injected by the API layer; the proxy also counts `choices[].delta.content` chunks as a fallback), TTFT = first content chunk, TPOT = (e2e − ttft)/output tokens, `generation_tps` = output tokens/(e2e − ttft), KV estimate = (prompt + generated) × bytes/token, `process_rss_bytes` via `proc_pidinfo`, `process_vram_bytes` ≈ RSS growth over the model's file size (unified memory). `mlx-audio` server (`mlx_audio.server`) is treated the same (`media_units` from audio duration in the request/response).

**Rollup rules** (`metric_rollups_engine_1m`): `*_delta` = max(counter) − min(counter) in the minute, reset-aware (if a counter decreases, treat the drop as a restart: delta = last value after reset + accumulated before); `*_avg` = mean of non-null samples; `*_max` = max; `*_p95` = p95 of per-sample p95 values (approximation; the exporter's histograms are the exact source); `healthy_seconds` = samples with `health=healthy` × interval; `error_rate_pct` = `errors_delta / NULLIF(requests_delta,0) × 100`.

## 3. Session / slot metrics → `metric_samples_session`

Prometheus: not exported per session (cardinality); only `gpudeck_engine_sessions{state}` counts. The UI reads them via `/metrics/sessions` and `/instances/{id}/sessions`.

| Canonical name | Unit | Column | llama.cpp (`/slots[]`) | vLLM / SGLang / TRT-LLM | proxy-derived (MLX, others) |
| --- | --- | --- | --- | --- | --- |
| `session_state` | enum | `state` | `is_processing` → `processing` if `next_token.n_decoded==0` else `generating`; `idle` otherwise | `waiting` (queued) / `generating` (running) from per-request views where available (`vllm` `/v1/...` has no per-request API → proxy) | `waiting` while queued at proxy, `processing` until first byte, `generating` after |
| `session_key` | string | `session_key` | `slot-<id>` + `id_task` | request id | proxy connection id |
| `request_id` | uuid | `request_id` | matched via proxy: the request the slot is serving (by `id_task` ↔ in-flight map when the engine echoes `id_slot` in the response, else by timing) | from `X-Request-ID` | from `X-Request-ID` |
| `prompt_tokens` | tokens | `prompt_tokens` | `n_prompt_tokens` (or `prompt` length when exposed) / response `timings.prompt_n` | response `usage` | response `usage` |
| `generated_tokens` | tokens | `generated_tokens` | `next_token.n_decoded` | `usage.completion_tokens` (final) | counted chunks / final `usage` |
| `cached_tokens` | tokens | `cached_tokens` | `timings.cache_n` (response) / `n_past − prompt_n` when `cache_prompt` reused | `usage.prompt_tokens_details.cached_tokens` | same, null if absent |
| `kv_cache_tokens` | tokens | `kv_cache_tokens` | `n_past` (newer builds) else `prompt_tokens + generated_tokens` | prompt+generated (approx.) | prompt+generated |
| `kv_cache_bytes` | bytes | `kv_cache_bytes` | `kv_cache_tokens × bytes/token` | same | same |
| `context_size` | tokens | `context_size` | `n_ctx` | `max_model_len` | launch `context_size` |
| `ttft_ms` | ms | `ttft_ms` | proxy | proxy / `vllm` histogram not per request → proxy | proxy |
| `elapsed_ms` | ms | `elapsed_ms` | now − slot start (proxy start) | proxy | proxy |
| `tokens_per_second` | tok/s | `tokens_per_second` | `next_token.n_decoded / elapsed` | generated / elapsed | generated / elapsed |

## 4. Proxy-derived metrics (universal fallback)

The agent proxy (`protocol.md` §6) wraps every inference request to every instance. For each request it records: `t_start`, `t_first_byte` (first body byte after headers; for SSE the first `data:` frame with content), `t_end`, HTTP status, upstream connect/transport errors, bytes, and parses the response body for usage:

| Response | Fields |
| --- | --- |
| OpenAI chat/completions JSON | `usage.prompt_tokens`, `usage.completion_tokens`, `usage.prompt_tokens_details.cached_tokens`, `usage.completion_tokens_details.reasoning_tokens`, llama.cpp `timings.*` |
| OpenAI SSE | last `data:` frame containing `usage` (the API layer injects `stream_options.include_usage=true`); chunk count as fallback for `completion_tokens` |
| OpenAI responses API | `usage.input_tokens`, `output_tokens`, `input_tokens_details.cached_tokens`, `output_tokens_details.reasoning_tokens`; SSE: `response.completed` event |
| Embeddings / rerank | `usage.prompt_tokens` / `usage.total_tokens` |
| Audio/image/video | no tokens; `media_units` from request (`duration` of uploaded audio via ffprobe-like header parse for wav/mp3/flac/ogg/webm; TTS `input` chars/1000; images `n`; video seconds) |
| Anthropic (engines that speak it natively) | `usage.input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`; SSE `message_start` + `message_delta` |

From these the agent builds `EngineSample` fields for `derive_from_proxy=true` engines and fills the proxy-sourced cells above for all engines. Counters are per instance since agent start.

## 5. Health and node metrics

| Canonical name | Source | Lands in |
| --- | --- | --- |
| `device_health` | NVIDIA: `nvmlDeviceGetHealth`-like composite = unhealthy if uncorrectable ECC delta > 0, retired pages pending (`nvmlDeviceGetRetiredPagesPendingStatus`), Xid 79/48/63 seen, or NVML returns `NVML_ERROR_GPU_IS_LOST`; degraded if throttled by `hw_slowdown`/`sw_thermal` for > 60 s or temperature > 90 °C. AMD: `ras` uncorrectable, `amd-smi` "GPU reset" events, temp > 95 °C. Others: SMI error counters. Apple: `unknown` → `healthy` when Metal device enumerates, `unhealthy` if `MTLCreateSystemDefaultDevice` fails. | `devices.health`, `health_reason`; `event(device_health_changed)` |
| `instance_health` | `health_config` probe result × `failure_threshold`; `unhealthy` on `failure_threshold` consecutive failures; `unknown` while loading or node offline | `instances.health`, `metric_samples_engine.health` |
| `node_cpu_pct`, `node_memory_used_bytes`, `node_load_avg_1m`, `node_disk_free_bytes{mount}`, `node_cache_used_bytes` | `/proc` / `sysctl` / `statvfs` | `heartbeat.load`, `inventory.system`; Prometheus `gpudeck_node_*` |
| `node_up` | hub: 1 while the node's agent is connected and heartbeating, else 0 | Prometheus `gpudeck_node_up{node}` (admin/hub exporter) |
| `agent_*` (`cpu_pct`, `rss_bytes`, `proxy_active_connections`, `proxy_requests_total`, `proxy_errors_total`, `scrape_errors_total`, `commands_running`) | agent self | `metrics_batch.agent`; Prometheus `gpudeck_agent_*` |

## 6. Usage / metering metrics → `usage_events`, `usage_rollups_hourly`

Recorded by the API layer (not the agent) per request; see `openapi/serving.md` §6 for extraction rules. Served by `/metrics/usage` and `/usage/*`; Prometheus `gpudeck_usage_requests_total{api_key,model,format,endpoint,status}`, `gpudeck_usage_tokens_total{kind=input|output|cached|reasoning,...}`, `gpudeck_usage_gpu_seconds_total`, `gpudeck_usage_cost_usd_total`, `gpudeck_usage_latency_seconds` (H), `gpudeck_usage_ttft_seconds` (H).

## 7. Process-level metrics (every Python process, `/metrics` on its own port)

`http_requests_total`, `http_request_duration_seconds`, `http_requests_in_progress`, `celery_tasks_total{task,state}`, `cache_hits_total`, `cache_misses_total` (standard set) plus `gpudeck_hub_connected_nodes`, `gpudeck_hub_messages_total{type,direction}`, `gpudeck_hub_ingest_rows_total{table}`, `gpudeck_hub_ingest_lag_seconds`, `gpudeck_rules_evaluations_total{rule_type,outcome}`, `gpudeck_swaps_total{kind,state}`, `gpudeck_swap_duration_seconds{kind}` (H), `gpudeck_jobs_leader` 0/1 (rules/swaps/leader: `app/services/ha/automation_metrics.py`), `gpudeck_hub_dropped_frames_total{type}`. Processes without an HTTP app expose their own listener: Celery worker on `WORKER_METRICS_PORT` (9464; `celery_tasks_total`), jobs/scheduler on `SCHEDULER_METRICS_PORT` (9465; rules, swaps, `gpudeck_jobs_leader`). The serving process also exports the §6 usage families `gpudeck_usage_requests_total`, `gpudeck_usage_tokens_total{kind}`, `gpudeck_usage_latency_seconds`, `gpudeck_usage_ttft_seconds` (gpu_seconds / cost_usd are computed by the metering worker and are read from the usage rollups / billing API, not Prometheus).

API layer process (`app/services/serving/metrics.py`): `gpudeck_serving_requests_total{format,endpoint,status}`, `gpudeck_serving_latency_seconds{format,endpoint}` (H, request start → last byte), `gpudeck_serving_ttfb_seconds{format,endpoint}` (H), `gpudeck_serving_inflight` (gauge), `gpudeck_serving_routing_version` (routing table version loaded), `gpudeck_serving_upstream_failures_total{code}`. There is no per-engine upstream histogram: engine-side latency is `gpudeck_engine_e2e_latency_seconds` (§2). HTTP-layer labels: `endpoint` is the matched route template (`unmatched` for 404s); `http_requests_in_progress` is labelled `endpoint="*"`.

## 8. Dashboard → metric mapping (for the web dashboards and the Grafana dashboards)

| Screen | Metrics |
| --- | --- |
| Overview tiles | `gpu_utilization_pct` (avg), `gpu_memory_used_bytes`/`total`, `gpu_power_watts` (sum), `active_sessions`, `waiting_sessions`, `requests_delta` (rpm), `generation_tps`, `ttft_ms_p95`, `error_rate_pct`, cost (usage) |
| Node / device detail | all §1 for the device; `process_count`; instances on it with `kv_cache_bytes`, `process_vram_bytes` |
| Model / deployment detail | `active_sessions`, `waiting_sessions`, `prompt_tps`, `generation_tps`, `ttft_ms_*`, `e2e_ms_*`, `tpot_ms_avg`, `kv_cache_usage_pct`, `cached_tokens_delta` vs `computed_tokens`, `errors_delta`, `error_rate_pct`, `prefix_cache_hit_rate`, `spec_accept_rate` |
| Sessions panel | §3 live snapshot + KV per session sparkline |
| Rules | thresholds overlaid on `requests_delta`, `waiting_sessions_avg`, `kv_cache_usage_pct_avg`, `error_rate_pct`, idle time |
| Billing | §6 |
