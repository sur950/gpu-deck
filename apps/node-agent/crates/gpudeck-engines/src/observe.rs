//! Metric parsing for **observed** engines: model servers GPU Deck did not start and only watches
//! (`agent.toml` `[[observe]]`, `protocol.md` §12).
//!
//! The agent fetches `/metrics` itself (GET only, short timeout) and hands the text here; this
//! module never talks to the network. The parsers are the managed adapters' own (`vllm`, `sglang`,
//! `llama_cpp`) plus a TGI mapping, with scrape history kept per observed name so rates and
//! interval percentiles work the same way as for managed instances.

use std::time::Instant;

use serde_json::json;

use crate::prometheus::{self, Snapshot};
use crate::state::{self, Prev};
use crate::types::EngineSample;
use crate::window::{hist_ms, PrevBuilder};

/// Observed engine kinds whose `/metrics` this module understands (`observed_engine_kind`).
pub const METRIC_KINDS: &[&str] = &["vllm", "sglang", "llamacpp", "tgi"];

fn key(name: &str) -> String {
    format!("observe://{name}")
}

/// Map one `/metrics` body of observed engine `name` (`kind` = `observed_engine_kind`). `None`
/// for kinds without a Prometheus mapping (`openai`, `generic`) or a body with no known metric.
pub fn sample_from_metrics(kind: &str, name: &str, text: &str) -> Option<EngineSample> {
    if !METRIC_KINDS.contains(&kind) {
        return None;
    }
    let snap = prometheus::parse(text);
    if snap.samples.is_empty() {
        return None;
    }
    let k = key(name);
    let now = Instant::now();
    let prev = state::peek_prev(&k);
    let (mut s, next) = match kind {
        "vllm" => crate::vllm::sample_from(&snap, None, prev.as_ref(), now),
        "sglang" => crate::sglang::sample_from(&snap, None, prev.as_ref(), now),
        "llamacpp" => {
            // `/slots` is never read for an observed server: Prometheus only.
            let s = crate::llama_cpp::sample_from(&snap, None, None, prev.as_ref(), now);
            (s, crate::llama_cpp::prev_from(&snap, now))
        }
        _ => tgi_sample_from(&snap, prev.as_ref(), now),
    };
    state::swap_prev(&k, next);
    s.extra_insert("metrics_source", json!("engine"));
    Some(s)
}

/// Drop the scrape history of an observed engine (removed from the config).
pub fn forget(name: &str) {
    state::forget_instance(&key(name));
}

/// Map a text-generation-inference (`tgi_*`) scrape into an `EngineSample`.
pub fn tgi_sample_from(snap: &Snapshot, prev: Option<&Prev>, now: Instant) -> (EngineSample, Prev) {
    let mut s = EngineSample::now();
    let mut next = PrevBuilder::default();
    s.active_sessions = snap.get("tgi_batch_current_size").map(|v| v as u64);
    s.waiting_sessions = snap.get("tgi_queue_size").map(|v| v as u64);
    s.requests_total = snap.sum("tgi_request_count").map(|v| v as u64);
    s.requests_success_total = snap.sum("tgi_request_success").map(|v| v as u64);
    s.errors_total = snap.sum("tgi_request_failure").map(|v| v as u64);
    let prompt = next.counter("prompt", snap.sum("tgi_request_input_length_sum"));
    let gen = next.counter("gen", snap.sum("tgi_request_generated_tokens_sum"));
    s.prompt_tokens_total = prompt.map(|v| v as u64);
    s.generated_tokens_total = gen.map(|v| v as u64);
    s.prompt_tps = state::rate(prev, "prompt", prompt, now);
    s.generation_tps = state::rate(prev, "gen", gen, now);
    (s.e2e_ms_avg, s.e2e_ms_p50, s.e2e_ms_p95) =
        hist_ms(snap, prev, &mut next, &["tgi_request_duration"]);
    s.queue_ms_avg = hist_ms(snap, prev, &mut next, &["tgi_request_queue_duration"]).0;
    s.tpot_ms_avg = hist_ms(
        snap,
        prev,
        &mut next,
        &["tgi_request_mean_time_per_token_duration"],
    )
    .0;
    if let Some(max) = snap.get("tgi_batch_current_max_tokens") {
        s.extra_insert("batch_current_max_tokens", json!(max));
    }
    (s, next.build(now))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture(rel: &str) -> String {
        std::fs::read_to_string(
            std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("../../fixtures/engines")
                .join(rel),
        )
        .unwrap()
    }

    #[test]
    fn vllm_fixture_maps_and_rates_after_second_scrape() {
        let name = "test-vllm-observed";
        forget(name);
        let a = sample_from_metrics("vllm", name, &fixture("vllm/metrics_1.txt")).unwrap();
        assert_eq!(a.active_sessions, Some(4));
        assert_eq!(a.prompt_tokens_total, Some(52000));
        assert!(a.kv_cache_usage_pct.is_some());
        assert_eq!(a.ttft_ms_p95, None, "no window on the first scrape");
        std::thread::sleep(std::time::Duration::from_millis(60));
        let b = sample_from_metrics("vllm", name, &fixture("vllm/metrics_2.txt")).unwrap();
        assert!(b.generation_tps.unwrap_or(0.0) > 0.0);
        assert!(b.ttft_ms_avg.is_some() || b.e2e_ms_avg.is_some());
        forget(name);
    }

    #[test]
    fn tgi_fixture_maps() {
        let name = "test-tgi-observed";
        forget(name);
        let a = sample_from_metrics("tgi", name, &fixture("tgi/metrics_1.txt")).unwrap();
        assert_eq!(a.active_sessions, Some(3));
        assert_eq!(a.waiting_sessions, Some(1));
        assert_eq!(a.requests_total, Some(100));
        assert_eq!(a.errors_total, Some(2));
        assert_eq!(a.prompt_tokens_total, Some(52000));
        std::thread::sleep(std::time::Duration::from_millis(60));
        let b = sample_from_metrics("tgi", name, &fixture("tgi/metrics_2.txt")).unwrap();
        assert!(b.generation_tps.unwrap() > 0.0);
        assert!(b.e2e_ms_avg.is_some());
        assert!(b.tpot_ms_avg.is_some());
        forget(name);
    }

    #[test]
    fn other_kinds_and_empty_bodies_give_nothing() {
        assert!(sample_from_metrics("openai", "x", "a 1\n").is_none());
        assert!(sample_from_metrics("generic", "x", "a 1\n").is_none());
        assert!(sample_from_metrics("vllm", "x", "").is_none());
        let s = sample_from_metrics("sglang", "test-sg", &fixture("sglang/metrics_1.txt"));
        assert!(s.is_some_and(|s| s.active_sessions.is_some()));
        let l = sample_from_metrics(
            "llamacpp",
            "test-llama",
            &fixture("llama_cpp/metrics_busy.txt"),
        );
        assert!(l.is_some());
    }
}
