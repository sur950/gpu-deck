//! llama.cpp `llama-server` adapter (verified against b8798 on this Mac).
//!
//! **Single model:** `llama-server -m <gguf> --alias <served> --host 127.0.0.1 --port P --metrics
//! --slots --jinja -c <ctx × slots> -np <slots> -ngl 999`. `-c` is the *total* context in
//! llama.cpp; GPU Deck's `context_size` is per request, so the adapter multiplies by the slot count
//! (each `/slots[].n_ctx` then equals `context_size`).
//!
//! **Router mode** (`router_mode=true`): `llama-server --models-dir D [--models-preset INI]
//! --models-max N --no-models-autoload` with the same global flags. Findings on b8798:
//! * the model list is read once at start-up from `--models-dir` / `--models-preset`
//!   (`server-models.cpp`: "TODO: allow refreshing cached model list"): `POST /models/load` for a
//!   name not known at start returns 404 → the agent must restart the router with an updated
//!   preset to add a model ([`RouterError::NotRegistered`]);
//! * `POST /models/load {"model": name}` returns `{"success":true}` immediately; the model goes
//!   `unloaded → loading → loaded` in `GET /models` (`status.value`, plus `failed`/`exit_code` on
//!   failure). Loading an already running model → 400 "model is already running";
//! * `POST /models/unload` → `{"success":true}`; unloading a model that is not loaded → 400
//!   "model is not loaded";
//! * per-model admin GETs take `?model=<name>`: `/metrics?model=`, `/slots?model=`,
//!   `/props?model=`; without it they return 400 "model name is missing from the request".
//!   `/health` answers for the router itself (also for `?model=`, even when that model's
//!   child hangs), so instance health also probes `/props?model=`. Each loaded model runs in a
//!   child `llama-server` on its own loopback port, spawned by the router.
//!
//! Instance URLs for router models are `base?model=<name>` ([`instance_url`]).
//!
//! **Metrics (b8798):** `llamacpp:prompt_tokens_total`, `prompt_seconds_total`,
//! `tokens_predicted_total`, `tokens_predicted_seconds_total`, `n_decode_total`, `n_tokens_max`,
//! `n_busy_slots_per_decode`, `prompt_tokens_seconds`, `predicted_tokens_seconds`,
//! `requests_processing`, `requests_deferred`. There is no KV-cache gauge in this build, and the
//! two `*_tokens_seconds` gauges are lifetime averages (the metrics bucket is never reset), so
//! `prompt_tps` / `generation_tps` are computed as counter rates between scrapes (aggregate
//! tokens per wall-clock second, 0 when idle); per-slot decode speed goes to `extra`.
//! `/slots[]`: `id`, `n_ctx`, `speculative`, `is_processing`, `id_task`,
//! `next_token[0].{n_decoded,n_remain}` (no prompt length / `n_past` while processing), so a
//! busy slot's KV usage is `n_past` when present, else `n_prompt_tokens + n_decoded`, else a
//! lower bound of `n_decoded` (flagged in `extra.kv_cache_used_is_lower_bound`).

use std::collections::{BTreeMap, HashMap};
use std::time::{Duration, Instant};

use anyhow::{anyhow, bail};
use async_trait::async_trait;
use serde_json::{json, Value};

use crate::proxy::PROMPT_EXCLUDES_CACHED;

use crate::flags::Flags;
use crate::http::{self, split_model, url};
use crate::kv;
use crate::launch::{add_library_path, base_env, bind_host, finish};
use crate::presets;
use crate::prometheus::{self, Snapshot};
use crate::state::{self, Hints, Prev};
use crate::types::*;

/// Short/long spellings of the same llama-server flag.
pub const ALIASES: &[&[&str]] = &[
    &["--ctx-size", "-c"],
    &["--n-gpu-layers", "-ngl", "--gpu-layers"],
    &["--parallel", "-np"],
    &["--flash-attn", "-fa"],
    &["--batch-size", "-b"],
    &["--ubatch-size", "-ub"],
    &["--cache-type-k", "-ctk"],
    &["--cache-type-v", "-ctv"],
    &["--model-draft", "-md"],
    &["--alias", "-a"],
    &["--model", "-m"],
    &["--threads", "-t"],
    &["--mmproj", "-mm"],
    &["--draft-max", "--draft", "--draft-n"],
    &["--cont-batching", "-cb"],
    &["--kv-unified", "-kvu"],
    &["--split-mode", "-sm"],
];

/// Slots when neither `max_concurrency` nor a preset sets `--parallel` (llama.cpp's own auto
/// value is 4).
pub const DEFAULT_SLOTS: u64 = 4;
/// Context per slot when the spec has none.
pub const DEFAULT_CONTEXT: u64 = 4096;
/// How long `load_model` waits for a router model to reach `loaded`.
pub const LOAD_TIMEOUT: Duration = Duration::from_secs(600);
pub const UNLOAD_TIMEOUT: Duration = Duration::from_secs(60);

#[derive(Debug, thiserror::Error)]
pub enum RouterError {
    #[error("model {0:?} is not registered with the llama.cpp router (it reads --models-dir/--models-preset only at start-up); restart the router with an updated preset")]
    NotRegistered(String),
    #[error("model {name:?} failed to load in the llama.cpp router (exit code {exit_code:?})")]
    LoadFailed {
        name: String,
        exit_code: Option<i64>,
    },
    #[error("timed out after {0:?} waiting for model {1:?}")]
    Timeout(Duration, String),
}

/// URL of one model served by a router process (`base?model=<name>`); `None` → the base URL.
pub fn instance_url(base_url: &str, model: Option<&str>) -> String {
    let (base, _) = split_model(base_url);
    match model {
        Some(m) => format!("{base}?model={}", http::percent_encode(m)),
        None => base,
    }
}

/// One `[section]` of a router `--models-preset` INI.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct RouterModel {
    /// Router model id (= served model name).
    pub name: String,
    pub model_path: String,
    pub mmproj_path: Option<String>,
    /// Per-model llama-server flags (`ctx-size`, `n-gpu-layers`, `cache-type-k`...).
    pub flags: BTreeMap<String, Value>,
    pub load_on_startup: bool,
}

fn ini_value(v: &Value) -> Option<String> {
    match v {
        Value::Null | Value::Bool(false) => None,
        Value::Bool(true) => Some("true".into()),
        Value::String(s) => Some(s.clone()),
        Value::Number(n) => Some(n.to_string()),
        Value::Array(a) => Some(a.iter().filter_map(ini_value).collect::<Vec<_>>().join(",")),
        Value::Object(_) => Some(v.to_string()),
    }
}

/// Render a router preset INI (`[*]` global section + one section per model). Keys are flag names
/// without leading dashes, as llama.cpp expects.
pub fn router_preset_ini(global: &BTreeMap<String, Value>, models: &[RouterModel]) -> String {
    let mut out = String::from("version = 1\n");
    let section = |out: &mut String, flags: &BTreeMap<String, Value>| {
        for (k, v) in flags {
            if let Some(s) = ini_value(v) {
                out.push_str(&format!("{} = {}\n", k.trim_start_matches('-'), s));
            }
        }
    };
    if !global.is_empty() {
        out.push_str("\n[*]\n");
        section(&mut out, global);
    }
    for m in models {
        out.push_str(&format!("\n[{}]\nmodel = {}\n", m.name, m.model_path));
        if let Some(mm) = &m.mmproj_path {
            out.push_str(&format!("mmproj = {mm}\n"));
        }
        out.push_str(&format!("load-on-startup = {}\n", m.load_on_startup));
        section(&mut out, &m.flags);
    }
    out
}

#[derive(Debug, Default, Clone)]
pub struct LlamaCppAdapter;

impl LlamaCppAdapter {
    pub fn new() -> Self {
        LlamaCppAdapter
    }

    /// Flags (without the program) plus the scrape hints for `spec`.
    pub fn build_args(spec: &LaunchSpec) -> anyhow::Result<(Vec<String>, Hints)> {
        let mut f = Flags::new(ALIASES);
        f.set("--host", bind_host(spec))
            .set("--port", spec.port as u64);
        if spec.router_mode {
            if spec.models_dir.is_none() && spec.models_preset.is_none() {
                bail!("llama.cpp router mode needs models_dir or models_preset");
            }
            if let Some(d) = &spec.models_dir {
                f.set("--models-dir", d.clone());
            }
            if let Some(p) = &spec.models_preset {
                f.set("--models-preset", p.clone());
            }
            if let Some(m) = spec.models_max {
                f.set("--models-max", m as u64);
            }
            f.set("--no-models-autoload", true);
        } else {
            f.set("--model", spec.model_path.clone())
                .set("--alias", spec.served_model_name.clone());
            if let Some(mm) = &spec.mmproj_path {
                f.set("--mmproj", mm.clone());
            }
        }
        f.set("--metrics", true).set("--slots", true);
        f.set(
            "--n-gpu-layers",
            if spec.devices.is_empty() {
                0u64
            } else {
                999u64
            },
        );
        if spec.tp_size > 1 {
            f.set("--split-mode", "row");
        }

        match spec.model_type {
            ModelType::Embedding => {
                f.set("--embeddings", true);
            }
            ModelType::Rerank => {
                f.set("--rerank", true);
            }
            ModelType::Chat | ModelType::Thinking | ModelType::Vision => {
                let parser = spec.tool_parser.clone().or_else(|| {
                    presets::default_tool_parser(EngineKind::LlamaCpp, None).map(str::to_string)
                });
                match parser.as_deref() {
                    Some("none") => {
                        f.set("--no-jinja", true);
                    }
                    Some("jinja") | None => {
                        f.set("--jinja", true);
                    }
                    Some(template) => {
                        f.set("--jinja", true)
                            .set("--chat-template", template.to_string());
                    }
                }
                let reasoning = spec.reasoning_parser.clone().or_else(|| {
                    presets::default_reasoning_parser(
                        EngineKind::LlamaCpp,
                        spec.model_family.as_deref().or(Some("any")),
                    )
                    .map(str::to_string)
                });
                if let Some(r) = reasoning {
                    f.set("--reasoning-format", r);
                }
            }
            other => bail!("llama.cpp does not serve {other:?} models"),
        }

        // Speculative decoding.
        match spec.spec_decoding.method.as_deref() {
            None | Some("none") => {}
            Some("draft_model") => {
                let p = spec.spec_decoding.draft_model_path.clone().ok_or_else(|| {
                    anyhow!("spec_decoding.draft_model_path is required for draft_model")
                })?;
                f.set("--model-draft", p);
                if let Some(n) = spec.spec_decoding.num_draft_tokens {
                    f.set("--draft-max", n as u64);
                }
            }
            Some("ngram") => {
                f.set("--spec-type", "ngram-simple");
                if let Some(n) = spec.spec_decoding.num_draft_tokens {
                    f.set("--draft-max", n as u64);
                }
            }
            Some(other) => {
                bail!("llama.cpp supports spec_decoding draft_model|ngram, not {other:?}")
            }
        }
        if spec.pd_split {
            bail!("llama.cpp has no prefill/decode disaggregation");
        }

        // Presets are defaults; explicit flags win; max_concurrency wins over both for --parallel.
        if let Some(p) = spec.preset {
            f.merge_defaults(&presets::preset_flags(EngineKind::LlamaCpp, p));
        }
        f.merge_user(&spec.launch_flags);
        if let Some(n) = spec.max_concurrency {
            f.set("--parallel", n as u64);
        }
        let slots = f.get_u64("--parallel").unwrap_or(DEFAULT_SLOTS).max(1);
        f.set("--parallel", slots);
        let kv_unified = f.has("--kv-unified");
        let user_ctx = spec
            .launch_flags
            .keys()
            .any(|k| matches!(k.trim_start_matches('-'), "c" | "ctx-size"));
        let per_slot = if user_ctx {
            let total = f.get_u64("--ctx-size").unwrap_or(0);
            if kv_unified || total == 0 {
                total
            } else {
                total / slots
            }
        } else {
            let per_slot = spec
                .context_size
                .map(|c| c as u64)
                .unwrap_or(DEFAULT_CONTEXT);
            f.set("--ctx-size", per_slot * slots);
            per_slot
        };

        let mut args = f.to_args();
        if !spec.control_vectors.is_empty() {
            let joined = spec
                .control_vectors
                .iter()
                .map(|cv| format!("{}:{}", cv.path, cv.scale))
                .collect::<Vec<_>>()
                .join(",");
            args.push("--control-vector-scaled".into());
            args.push(joined);
            if let Some((s, e)) = spec.control_vectors.iter().find_map(|cv| cv.layer_range) {
                args.push("--control-vector-layer-range".into());
                args.push(s.to_string());
                args.push(e.to_string());
            }
        }

        let kv_bpt = kv::bytes_per_token(
            &spec.model_config,
            f.get_str("--cache-type-k").as_deref(),
            f.get_str("--cache-type-v").as_deref(),
        );
        let hints = Hints {
            served_model_name: Some(spec.served_model_name.clone()),
            context_size: Some(per_slot),
            max_concurrency: Some(slots),
            kv_unified,
            kv_bytes_per_token: kv_bpt,
            memory_fraction: None,
            router_mode: spec.router_mode,
            models_dir: spec.models_dir.clone(),
        };
        Ok((args, hints))
    }
}

/// Build an `EngineSample` from a `/metrics` snapshot and the `/slots` array.
pub fn sample_from(
    metrics: &Snapshot,
    slots: Option<&Value>,
    hints: Option<&Hints>,
    prev: Option<&Prev>,
    now: Instant,
) -> EngineSample {
    let mut s = EngineSample::now();
    let m = |n: &str| metrics.get(&format!("llamacpp:{n}"));
    s.active_sessions = m("requests_processing").map(|v| v as u64);
    s.waiting_sessions = m("requests_deferred").map(|v| v as u64);
    s.prompt_tokens_total = m("prompt_tokens_total").map(|v| v as u64);
    if s.prompt_tokens_total.is_some() {
        // llamacpp:prompt_tokens_total counts only *evaluated* prompt tokens (cache hits are
        // not in it); merge_proxy_stats swaps in the proxy's usage-based total (incl. cached).
        s.extra_insert(PROMPT_EXCLUDES_CACHED, json!(true));
    }
    s.generated_tokens_total = m("tokens_predicted_total").map(|v| v as u64);
    s.prompt_tps = state::rate(
        prev,
        "llamacpp:prompt_tokens_total",
        m("prompt_tokens_total"),
        now,
    );
    s.generation_tps = state::rate(
        prev,
        "llamacpp:tokens_predicted_total",
        m("tokens_predicted_total"),
        now,
    );

    // Interval decode speed per busy slot and TPOT from counter deltas.
    let d_tok = state::delta(
        prev,
        "llamacpp:tokens_predicted_total",
        m("tokens_predicted_total"),
    );
    let d_sec = state::delta(
        prev,
        "llamacpp:tokens_predicted_seconds_total",
        m("tokens_predicted_seconds_total"),
    );
    if let (Some(t), Some(sec)) = (d_tok, d_sec) {
        if t > 0.0 && sec > 0.0 {
            s.tpot_ms_avg = Some(sec * 1000.0 / t);
            s.extra_insert("decode_tps_per_slot", json!(t / sec));
        }
    }
    let p_tok = state::delta(
        prev,
        "llamacpp:prompt_tokens_total",
        m("prompt_tokens_total"),
    );
    let p_sec = state::delta(
        prev,
        "llamacpp:prompt_seconds_total",
        m("prompt_seconds_total"),
    );
    if let (Some(t), Some(sec)) = (p_tok, p_sec) {
        if t > 0.0 && sec > 0.0 {
            s.extra_insert("prefill_tps_per_slot", json!(t / sec));
        }
    }
    for (k, name) in [
        ("prompt_tokens_seconds_lifetime", "prompt_tokens_seconds"),
        (
            "predicted_tokens_seconds_lifetime",
            "predicted_tokens_seconds",
        ),
        ("n_busy_slots_per_decode", "n_busy_slots_per_decode"),
        ("n_decode_total", "n_decode_total"),
        ("n_tokens_max", "n_tokens_max"),
    ] {
        if let Some(v) = m(name) {
            s.extra_insert(k, json!(v));
        }
    }
    // Newer/older builds that still export KV gauges.
    if let Some(v) = m("kv_cache_tokens") {
        s.kv_cache_used_tokens = Some(v as u64);
    }
    if let Some(v) = m("kv_cache_usage_ratio") {
        s.kv_cache_usage_pct = Some(v * 100.0);
    }

    let kv_bpt = hints.and_then(|h| h.kv_bytes_per_token);
    if let Some(Value::Array(arr)) = slots {
        let mut total_ctx = 0u64;
        let mut max_ctx = 0u64;
        let mut idle = 0u64;
        let mut busy = 0u64;
        let mut used = 0u64;
        let mut lower_bound = false;
        for slot in arr {
            let id = slot.get("id").and_then(|v| v.as_u64());
            let n_ctx = slot.get("n_ctx").and_then(|v| v.as_u64()).unwrap_or(0);
            total_ctx += n_ctx;
            max_ctx = max_ctx.max(n_ctx);
            let processing = slot
                .get("is_processing")
                .and_then(|v| v.as_bool())
                .unwrap_or(false);
            if !processing {
                idle += 1;
                continue;
            }
            busy += 1;
            let nt = match slot.get("next_token") {
                Some(Value::Array(a)) => a.first(),
                Some(o @ Value::Object(_)) => Some(o),
                _ => None,
            };
            let decoded = nt.and_then(|t| t.get("n_decoded")).and_then(|v| v.as_u64());
            let n_past = slot.get("n_past").and_then(|v| v.as_u64());
            let n_prompt = slot.get("n_prompt_tokens").and_then(|v| v.as_u64());
            let kv_tokens = n_past.or(match (n_prompt, decoded) {
                (Some(p), Some(d)) => Some(p + d),
                _ => None,
            });
            let kv_tokens = match kv_tokens {
                Some(k) => Some(k),
                None => {
                    lower_bound = true;
                    decoded
                }
            };
            used += kv_tokens.unwrap_or(0);
            let state = if decoded.unwrap_or(0) == 0 {
                SessionState::Processing
            } else {
                SessionState::Generating
            };
            let task = slot.get("id_task").and_then(|v| v.as_i64());
            let key = match (id, task) {
                (Some(i), Some(t)) => format!("slot-{i}:{t}"),
                (Some(i), None) => format!("slot-{i}"),
                _ => format!("slot-{busy}"),
            };
            let mut ss = SlotSample::new(key, state);
            ss.slot_id = id.map(|i| i as u32);
            ss.prompt_tokens = n_prompt;
            ss.generated_tokens = decoded;
            ss.cached_tokens = match (n_past, n_prompt) {
                (Some(p), Some(np)) if p > np => Some(p - np),
                _ => None,
            };
            ss.kv_cache_tokens = kv_tokens;
            ss.kv_cache_bytes = kv_tokens.and_then(|t| kv_bpt.map(|b| t * b));
            ss.context_size = Some(n_ctx);
            if let Some(rem) = nt.and_then(|t| t.get("n_remain")).and_then(|v| v.as_i64()) {
                ss.extra = Some(serde_json::Map::from_iter([(
                    "n_remain".to_string(),
                    json!(rem),
                )]));
            }
            s.sessions.push(ss);
        }
        s.slots_total = Some(arr.len() as u64);
        s.slots_idle = Some(idle);
        if s.active_sessions.is_none() {
            s.active_sessions = Some(busy);
        }
        let unified = hints.map(|h| h.kv_unified).unwrap_or(false);
        let total = if unified { max_ctx } else { total_ctx };
        if s.kv_cache_total_tokens.is_none() && total > 0 {
            s.kv_cache_total_tokens = Some(total);
        }
        if s.kv_cache_used_tokens.is_none() {
            s.kv_cache_used_tokens = Some(used);
            if lower_bound {
                s.extra_insert("kv_cache_used_is_lower_bound", json!(true));
            }
        }
    }
    if s.kv_cache_usage_pct.is_none() {
        if let (Some(u), Some(t)) = (s.kv_cache_used_tokens, s.kv_cache_total_tokens) {
            if t > 0 {
                s.kv_cache_usage_pct = Some(u as f64 * 100.0 / t as f64);
            }
        }
    }
    if let (Some(u), Some(b)) = (s.kv_cache_used_tokens, kv_bpt) {
        s.kv_cache_bytes = Some(u * b);
    }
    s
}

/// Counters remembered between scrapes.
pub fn prev_from(metrics: &Snapshot, now: Instant) -> Prev {
    let mut counters = HashMap::new();
    for n in [
        "prompt_tokens_total",
        "prompt_seconds_total",
        "tokens_predicted_total",
        "tokens_predicted_seconds_total",
    ] {
        let name = format!("llamacpp:{n}");
        if let Some(v) = metrics.get(&name) {
            counters.insert(name, v);
        }
    }
    Prev {
        at: now,
        counters,
        histograms: HashMap::new(),
    }
}

/// Router `GET /models` entry status for `model`: (`value`, failed, exit_code).
pub fn router_status(models: &Value, model: &str) -> Option<(String, bool, Option<i64>)> {
    let list = models.get("data").and_then(|d| d.as_array())?;
    let entry = list.iter().find(|m| {
        m.get("id").and_then(|v| v.as_str()) == Some(model)
            || m.get("aliases")
                .and_then(|a| a.as_array())
                .is_some_and(|a| a.iter().any(|x| x.as_str() == Some(model)))
    })?;
    let st = entry.get("status")?;
    Some((
        st.get("value")
            .and_then(|v| v.as_str())
            .unwrap_or("unknown")
            .to_string(),
        st.get("failed").and_then(|v| v.as_bool()).unwrap_or(false),
        st.get("exit_code").and_then(|v| v.as_i64()),
    ))
}

async fn wait_router_status(
    base: &str,
    model: &str,
    want: &str,
    timeout: Duration,
) -> anyhow::Result<()> {
    let deadline = Instant::now() + timeout;
    loop {
        let models = http::get_json(&url(base, "/models", None)).await?;
        match router_status(&models, model) {
            None => return Err(RouterError::NotRegistered(model.to_string()).into()),
            Some((value, failed, code)) => {
                if value == want || (want == "loaded" && value == "sleeping") {
                    return Ok(());
                }
                if want == "loaded" && value == "unloaded" && failed {
                    return Err(RouterError::LoadFailed {
                        name: model.to_string(),
                        exit_code: code,
                    }
                    .into());
                }
            }
        }
        if Instant::now() >= deadline {
            return Err(RouterError::Timeout(timeout, model.to_string()).into());
        }
        tokio::time::sleep(Duration::from_millis(250)).await;
    }
}

#[async_trait]
impl EngineAdapter for LlamaCppAdapter {
    fn kind(&self) -> EngineKind {
        EngineKind::LlamaCpp
    }

    fn command(&self, spec: &LaunchSpec) -> anyhow::Result<ProcessSpec> {
        let (args, hints) = Self::build_args(spec)?;
        state::register(&spec.base_url(), hints);
        let program = spec
            .executable
            .clone()
            .unwrap_or_else(|| "llama-server".into());
        let mut env = base_env(spec);
        add_library_path(&mut env, spec, "libllama");
        finish(
            spec,
            program,
            args,
            env,
            Some("ghcr.io/ggml-org/llama.cpp:server"),
        )
    }

    async fn health(&self, base_url: &str) -> HealthStatus {
        let (base, model) = split_model(base_url);
        match model {
            Some(m) => match http::get_json(&url(&base, "/models", None)).await {
                Ok(models) => match router_status(&models, &m) {
                    // The router reports "loaded" for as long as the child process exists, and
                    // its own /health answers even when the child hangs: ask the child itself
                    // (`/props?model=` is proxied to it and served off the inference path).
                    Some((v, _, _)) if v == "loaded" => {
                        match http::get_text(&url(&base, "/props", Some(&m))).await {
                            Ok((code, _)) if (200..300).contains(&code) => HealthStatus::Healthy,
                            Ok((503, _)) => HealthStatus::Loading,
                            Ok((code, _)) => {
                                HealthStatus::unhealthy(format!("model {m}: GET /props -> {code}"))
                            }
                            Err(e) => HealthStatus::unhealthy(format!(
                                "model {m} process is not responding: {e:#}"
                            )),
                        }
                    }
                    Some((v, _, _)) if v == "sleeping" => HealthStatus::Healthy,
                    Some((v, _, _)) if v == "loading" => HealthStatus::Loading,
                    Some((_, true, code)) => HealthStatus::unhealthy(format!(
                        "model {m} failed to load (exit code {code:?})"
                    )),
                    Some((v, _, _)) => HealthStatus::unhealthy(format!("model {m} is {v}")),
                    None => HealthStatus::unhealthy(format!(
                        "model {m} is not registered with the router"
                    )),
                },
                Err(e) => HealthStatus::unhealthy(e.to_string()),
            },
            None => http::probe(&base, &self.health_config()).await,
        }
    }

    async fn scrape(&self, base_url: &str) -> anyhow::Result<EngineSample> {
        let (base, model) = split_model(base_url);
        let instance = instance_url(&base, model.as_deref());
        let metrics_res = http::get_ok(&url(&base, "/metrics", model.as_deref())).await;
        let slots_res = http::get_json(&url(&base, "/slots", model.as_deref())).await;
        if let (Err(e), Err(_)) = (&metrics_res, &slots_res) {
            return Err(anyhow!("llama.cpp scrape failed: {e}"));
        }
        let snap = metrics_res
            .as_deref()
            .map(prometheus::parse)
            .unwrap_or_default();
        let now = Instant::now();
        let prev = state::swap_prev(&instance, prev_from(&snap, now));
        let hints = state::hints(&base);
        let mut s = sample_from(
            &snap,
            slots_res.as_ref().ok(),
            hints.as_ref(),
            prev.as_ref(),
            now,
        );
        s.health = Some(InstanceHealth::Healthy);
        if let Err(e) = &metrics_res {
            s.extra_insert("metrics_error", json!(e.to_string()));
        }
        Ok(s)
    }

    fn supports_hot_swap(&self) -> bool {
        // Router mode only; the agent decides per process (`LaunchSpec.router_mode`).
        true
    }

    async fn load_model(&self, base_url: &str, model: &ModelRef) -> anyhow::Result<()> {
        let (base, _) = split_model(base_url);
        let (code, body) = http::post_json(
            &url(&base, "/models/load", None),
            &json!({"model": model.name}),
            http::SCRAPE_TIMEOUT,
        )
        .await?;
        match code {
            200 => {}
            400 if body.contains("already running") => {}
            404 => return Err(RouterError::NotRegistered(model.name.clone()).into()),
            other => bail!(
                "llama.cpp /models/load {}: {other} {}",
                model.name,
                http::truncate(&body, 300)
            ),
        }
        wait_router_status(&base, &model.name, "loaded", LOAD_TIMEOUT).await
    }

    async fn unload_model(&self, base_url: &str, model: &str) -> anyhow::Result<()> {
        let (base, _) = split_model(base_url);
        let (code, body) = http::post_json(
            &url(&base, "/models/unload", None),
            &json!({"model": model}),
            http::SCRAPE_TIMEOUT,
        )
        .await?;
        match code {
            200 => {}
            400 if body.contains("not loaded") => return Ok(()),
            404 => return Err(RouterError::NotRegistered(model.to_string()).into()),
            other => bail!(
                "llama.cpp /models/unload {model}: {other} {}",
                http::truncate(&body, 300)
            ),
        }
        state::forget_instance(&instance_url(&base, Some(model)));
        wait_router_status(&base, model, "unloaded", UNLOAD_TIMEOUT).await
    }

    fn health_config(&self) -> HealthConfig {
        HealthConfig {
            path: "/health".into(),
            ..HealthConfig::default()
        }
    }

    fn metrics_config(&self) -> MetricsConfig {
        MetricsConfig {
            path: Some("/metrics".into()),
            format: "prometheus".into(),
            slots_path: Some("/slots".into()),
            scrape_interval_seconds: 2.0,
            derive_from_proxy: false,
            metric_prefix: Some("llamacpp:".into()),
        }
    }

    fn allowed_path_prefixes(&self) -> Vec<&'static str> {
        vec![
            "/v1/",
            "/rerank",
            "/reranking",
            "/embedding",
            "/embeddings",
            "/infill",
            "/completion",
            "/tokenize",
            "/detokenize",
            "/apply-template",
            "/api/",
        ]
    }

    fn admin_paths(&self) -> Vec<&'static str> {
        vec![
            "/metrics",
            "/slots",
            "/props",
            "/models",
            "/models/load",
            "/models/unload",
            "/health",
            "/lora-adapters",
        ]
    }
}
