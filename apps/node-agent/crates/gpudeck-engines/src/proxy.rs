//! Proxy-derived metrics (`metrics.md` §4): the universal fallback for engines that expose no
//! `/metrics` (MLX, mlx-audio, whisper.cpp, sd.cpp, custom) and the source of request/error/
//! latency metrics for all engines.
//!
//! The agent's mTLS proxy records one [`RequestRecord`] per completed request and feeds them into
//! a per-instance [`ProxyStats`]. On each scrape the adapter merges a `ProxyStats` snapshot into
//! the `EngineSample` via `EngineAdapter::apply_proxy_stats`, which (a) fills counters the engine
//! lacks and (b) computes the interval-window latency/throughput figures from the records seen
//! since the previous scrape.
//!
//! Everything here is plain data: no I/O, fully unit-testable.

use std::collections::VecDeque;

use serde::{Deserialize, Serialize};

use crate::types::{EngineSample, SessionState, SlotSample};

/// What the proxy knows about one finished (or failed) request.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct RequestRecord {
    /// `X-Request-ID` from the API layer, when present.
    pub request_id: Option<String>,
    /// Wall time from request start to last byte.
    pub e2e_ms: f64,
    /// Time to first body byte (for SSE: first `data:` frame with content); `None` for errors.
    pub ttft_ms: Option<f64>,
    /// Time the request waited in the proxy queue (`max_concurrent`) before being forwarded.
    pub queue_ms: f64,
    pub status: u16,
    /// Upstream connect/transport failure (counts as error regardless of status).
    pub transport_error: bool,
    pub prompt_tokens: Option<u64>,
    pub completion_tokens: Option<u64>,
    /// `usage.prompt_tokens_details.cached_tokens` / Anthropic `cache_read_input_tokens` /
    /// llama.cpp `timings.cache_n`.
    pub cached_tokens: Option<u64>,
    pub reasoning_tokens: Option<u64>,
    /// Speculative decoding acceptance from llama.cpp `timings.draft_n_accepted / draft_n`.
    pub draft_tokens: Option<u64>,
    pub draft_accepted: Option<u64>,
    /// Non-token work units (audio seconds, images, TTS kilo-chars).
    pub media_units: Option<f64>,
    pub streamed: bool,
}

impl RequestRecord {
    pub fn is_error(&self) -> bool {
        self.transport_error || self.status >= 500
    }

    /// Parse an OpenAI-style `usage` object (chat/completions/embeddings/responses) into this
    /// record's token fields. Accepts both `prompt_tokens/completion_tokens` and
    /// `input_tokens/output_tokens`, plus llama.cpp `timings`.
    pub fn absorb_usage(&mut self, body: &serde_json::Value) {
        if let Some(usage) = body.get("usage") {
            let g = |k: &str| usage.get(k).and_then(|v| v.as_u64());
            self.prompt_tokens = g("prompt_tokens")
                .or_else(|| g("input_tokens"))
                .or(self.prompt_tokens);
            self.completion_tokens = g("completion_tokens")
                .or_else(|| g("output_tokens"))
                .or(self.completion_tokens);
            let cached = usage
                .pointer("/prompt_tokens_details/cached_tokens")
                .or_else(|| usage.pointer("/input_tokens_details/cached_tokens"))
                .or_else(|| usage.get("cache_read_input_tokens"))
                .and_then(|v| v.as_u64());
            if cached.is_some() {
                self.cached_tokens = cached;
            }
            let reasoning = usage
                .pointer("/completion_tokens_details/reasoning_tokens")
                .or_else(|| usage.pointer("/output_tokens_details/reasoning_tokens"))
                .and_then(|v| v.as_u64());
            if reasoning.is_some() {
                self.reasoning_tokens = reasoning;
            }
        }
        if let Some(t) = body.get("timings") {
            let g = |k: &str| t.get(k).and_then(|v| v.as_u64());
            if let Some(c) = g("cache_n") {
                self.cached_tokens = Some(c);
            }
            if self.prompt_tokens.is_none() {
                self.prompt_tokens = g("prompt_n");
            }
            if self.completion_tokens.is_none() {
                self.completion_tokens = g("predicted_n");
            }
            if let (Some(d), Some(a)) = (g("draft_n"), g("draft_n_accepted")) {
                self.draft_tokens = Some(d);
                self.draft_accepted = Some(a);
            }
        }
    }
}

/// An in-flight request as seen by the proxy (for `SessionSample`s of proxy-derived engines).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct InflightRequest {
    pub connection_id: String,
    pub request_id: Option<String>,
    /// Milliseconds since the request arrived at the proxy.
    pub elapsed_ms: f64,
    /// Still queued behind `max_concurrent`.
    pub queued: bool,
    /// First byte seen (generating) or not (processing).
    pub first_byte_seen: bool,
    pub ttft_ms: Option<f64>,
    pub prompt_tokens: Option<u64>,
    /// SSE content chunks counted so far (≈ generated tokens).
    pub chunks_seen: u64,
}

/// Per-instance proxy counters (monotonic since agent start) plus the records of the current
/// scrape window.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct ProxyStats {
    pub requests_total: u64,
    pub requests_success_total: u64,
    pub errors_total: u64,
    pub prompt_tokens_total: u64,
    pub generated_tokens_total: u64,
    /// Sum of `cached_tokens` over requests that reported it (`None` until any did).
    pub cached_tokens_total: Option<u64>,
    pub draft_tokens_total: u64,
    pub draft_accepted_total: u64,
    /// Requests currently forwarded to the engine.
    pub active: u64,
    /// Requests queued at the proxy (`max_concurrent`).
    pub waiting: u64,
    /// `max_concurrent` configured for this instance (→ `slots_total` for proxy-derived engines).
    pub max_concurrent: Option<u64>,
    /// Records completed since the previous `take_window()`.
    pub window: VecDeque<RequestRecord>,
    /// Seconds covered by `window` (set by `take_window`); defaults to the scrape interval.
    pub window_seconds: f64,
    /// In-flight requests at snapshot time.
    pub inflight: Vec<InflightRequest>,
    /// Bytes per KV token (from `model_versions.config`) to estimate `kv_cache_*_bytes`.
    pub kv_bytes_per_token: Option<u64>,
    /// `context_size × max_concurrency` for proxy-derived `kv_cache_total_tokens`.
    pub context_size: Option<u64>,
}

impl ProxyStats {
    /// Record a finished request (called by the proxy on response end / failure).
    pub fn record(&mut self, r: RequestRecord) {
        self.requests_total += 1;
        if r.is_error() {
            self.errors_total += 1;
        } else if r.status < 400 {
            self.requests_success_total += 1;
        }
        self.prompt_tokens_total += r.prompt_tokens.unwrap_or(0);
        self.generated_tokens_total += r.completion_tokens.unwrap_or(0);
        if let Some(c) = r.cached_tokens {
            *self.cached_tokens_total.get_or_insert(0) += c;
        }
        self.draft_tokens_total += r.draft_tokens.unwrap_or(0);
        self.draft_accepted_total += r.draft_accepted.unwrap_or(0);
        self.window.push_back(r);
        // Bound memory under very high request rates between scrapes.
        while self.window.len() > 10_000 {
            self.window.pop_front();
        }
    }

    /// Snapshot for a scrape: copies the counters, moves the window out, and resets it.
    pub fn take_window(&mut self, window_seconds: f64) -> ProxyStats {
        let mut snap = self.clone();
        snap.window_seconds = window_seconds;
        self.window.clear();
        snap
    }

    /// Window aggregates (avg / p50 / p95 of ttft and e2e, tpot, queue, throughput).
    pub fn window_stats(&self) -> WindowStats {
        let mut ttft: Vec<f64> = self.window.iter().filter_map(|r| r.ttft_ms).collect();
        let mut e2e: Vec<f64> = self
            .window
            .iter()
            .filter(|r| !r.is_error())
            .map(|r| r.e2e_ms)
            .collect();
        let queue: Vec<f64> = self.window.iter().map(|r| r.queue_ms).collect();
        let mut tpot: Vec<f64> = Vec::new();
        let mut gen_tokens = 0u64;
        let mut timed_tokens = 0u64;
        let mut prompt_tokens = 0u64;
        let mut gen_time_s = 0.0;
        for r in self.window.iter().filter(|r| !r.is_error()) {
            if let Some(ct) = r.completion_tokens {
                gen_tokens += ct;
                // Streams: decode time = e2e - first content frame. A non-streamed body arrives
                // in one piece (its "first byte" is the end), so only e2e bounds its decode time.
                let decode_ms = if r.streamed {
                    r.e2e_ms - r.ttft_ms.unwrap_or(0.0)
                } else {
                    r.e2e_ms
                };
                if ct > 1 && decode_ms >= 1.0 {
                    if r.streamed {
                        tpot.push(decode_ms / (ct - 1) as f64);
                    }
                    timed_tokens += ct;
                    gen_time_s += decode_ms / 1000.0;
                }
            }
            prompt_tokens += r.prompt_tokens.unwrap_or(0);
        }
        ttft.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
        e2e.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
        let secs = if self.window_seconds > 0.0 {
            self.window_seconds
        } else {
            2.0
        };
        WindowStats {
            requests: self.window.len() as u64,
            ttft_ms_avg: mean(&ttft),
            ttft_ms_p50: percentile(&ttft, 0.50),
            ttft_ms_p95: percentile(&ttft, 0.95),
            e2e_ms_avg: mean(&e2e),
            e2e_ms_p50: percentile(&e2e, 0.50),
            e2e_ms_p95: percentile(&e2e, 0.95),
            tpot_ms_avg: mean(&tpot),
            queue_ms_avg: mean(&queue),
            prompt_tps: if prompt_tokens > 0 {
                Some(prompt_tokens as f64 / secs)
            } else {
                None
            },
            generation_tps: if gen_tokens > 0 {
                Some(if gen_time_s > 0.0 {
                    timed_tokens as f64 / gen_time_s
                } else {
                    gen_tokens as f64 / secs
                })
            } else {
                None
            },
        }
    }

    /// Per-request session samples for proxy-derived engines (`metrics.md` §3, last column).
    pub fn session_samples(&self) -> Vec<SlotSample> {
        self.inflight
            .iter()
            .map(|r| {
                let state = if r.queued {
                    SessionState::Waiting
                } else if r.first_byte_seen {
                    SessionState::Generating
                } else {
                    SessionState::Processing
                };
                let generated = if r.first_byte_seen {
                    Some(r.chunks_seen)
                } else {
                    None
                };
                let kv_tokens = match (r.prompt_tokens, generated) {
                    (Some(p), Some(g)) => Some(p + g),
                    (Some(p), None) => Some(p),
                    _ => None,
                };
                let mut s = SlotSample::new(r.connection_id.clone(), state);
                s.request_id = r.request_id.clone();
                s.prompt_tokens = r.prompt_tokens;
                s.generated_tokens = generated;
                s.kv_cache_tokens = kv_tokens;
                s.kv_cache_bytes = kv_tokens.and_then(|t| self.kv_bytes_per_token.map(|b| t * b));
                s.context_size = self
                    .context_size
                    .map(|c| c / self.max_concurrent.unwrap_or(1).max(1));
                s.ttft_ms = r.ttft_ms;
                s.elapsed_ms = Some(r.elapsed_ms);
                s.tokens_per_second = match (generated, r.ttft_ms) {
                    (Some(g), Some(t)) if g > 0 && r.elapsed_ms > t => {
                        Some(g as f64 / ((r.elapsed_ms - t) / 1000.0))
                    }
                    _ => None,
                };
                s
            })
            .collect()
    }
}

/// Window aggregates computed by `ProxyStats::window_stats`.
#[derive(Debug, Clone, PartialEq, Default, Serialize, Deserialize)]
pub struct WindowStats {
    pub requests: u64,
    pub ttft_ms_avg: Option<f64>,
    pub ttft_ms_p50: Option<f64>,
    pub ttft_ms_p95: Option<f64>,
    pub e2e_ms_avg: Option<f64>,
    pub e2e_ms_p50: Option<f64>,
    pub e2e_ms_p95: Option<f64>,
    pub tpot_ms_avg: Option<f64>,
    pub queue_ms_avg: Option<f64>,
    pub prompt_tps: Option<f64>,
    pub generation_tps: Option<f64>,
}

fn mean(v: &[f64]) -> Option<f64> {
    if v.is_empty() {
        None
    } else {
        Some(v.iter().sum::<f64>() / v.len() as f64)
    }
}

/// Nearest-rank percentile on a sorted slice.
pub fn percentile(sorted: &[f64], q: f64) -> Option<f64> {
    if sorted.is_empty() {
        return None;
    }
    let rank = ((q * sorted.len() as f64).ceil() as usize).clamp(1, sorted.len());
    Some(sorted[rank - 1])
}

/// `EngineSample.extra` flag: the engine's `prompt_tokens_total` excludes cache hits.
pub const PROMPT_EXCLUDES_CACHED: &str = "prompt_tokens_exclude_cached";

/// Merge proxy stats into a sample. Engine-provided values win; when `derive_all` is set
/// (`derive_from_proxy=true` engines) sessions/slots/KV come from the proxy as well.
pub fn merge_proxy_stats(sample: &mut EngineSample, stats: &ProxyStats, derive_all: bool) {
    let w = stats.window_stats();
    // metrics.md: prompt_tokens_total includes cached tokens (computed = prompt - cached).
    // Engines whose counter excludes cache hits get the proxy's usage-based total, which comes
    // from the same responses (and resets together with) cached_tokens_total.
    let excludes_cached = sample
        .extra
        .as_ref()
        .and_then(|e| e.get(PROMPT_EXCLUDES_CACHED))
        .and_then(|v| v.as_bool())
        == Some(true);
    if excludes_cached
        && sample.cached_tokens_total.is_none()
        && stats.cached_tokens_total.is_some()
    {
        if let Some(evaluated) = sample.prompt_tokens_total {
            sample.extra_insert(
                "evaluated_prompt_tokens_total",
                serde_json::json!(evaluated),
            );
        }
        sample.prompt_tokens_total = Some(stats.prompt_tokens_total);
    }
    // Counters the engines never expose (llama.cpp has no request counter, etc.).
    sample.requests_total.get_or_insert(stats.requests_total);
    sample
        .requests_success_total
        .get_or_insert(stats.requests_success_total);
    sample.errors_total.get_or_insert(stats.errors_total);
    if sample.cached_tokens_total.is_none() {
        sample.cached_tokens_total = stats.cached_tokens_total;
    }
    if sample.spec_accept_rate.is_none() && stats.draft_tokens_total > 0 {
        sample.spec_accept_rate =
            Some(stats.draft_accepted_total as f64 / stats.draft_tokens_total as f64);
    }
    // Window latencies: engines with histograms (vLLM/SGLang) already filled these.
    macro_rules! fill {
        ($($f:ident),*) => { $( if sample.$f.is_none() { sample.$f = w.$f; } )* };
    }
    fill!(
        ttft_ms_avg,
        ttft_ms_p50,
        ttft_ms_p95,
        e2e_ms_avg,
        e2e_ms_p50,
        e2e_ms_p95,
        tpot_ms_avg,
        queue_ms_avg
    );
    if sample.waiting_sessions.is_none() || derive_all {
        // Engine queue + proxy queue (engine value already includes its own queue).
        let engine_waiting = sample.waiting_sessions.unwrap_or(0);
        sample.waiting_sessions = Some(engine_waiting + stats.waiting);
    }
    if derive_all {
        sample.active_sessions.get_or_insert(stats.active);
        sample
            .prompt_tokens_total
            .get_or_insert(stats.prompt_tokens_total);
        sample
            .generated_tokens_total
            .get_or_insert(stats.generated_tokens_total);
        if sample.prompt_tps.is_none() {
            sample.prompt_tps = w.prompt_tps;
        }
        if sample.generation_tps.is_none() {
            sample.generation_tps = w.generation_tps;
        }
        if let Some(max) = stats.max_concurrent {
            sample.slots_total.get_or_insert(max);
            sample
                .slots_idle
                .get_or_insert(max.saturating_sub(stats.active));
        }
        if sample.sessions.is_empty() {
            sample.sessions = stats.session_samples();
        }
        if sample.kv_cache_used_tokens.is_none() {
            let used: u64 = sample
                .sessions
                .iter()
                .filter_map(|s| s.kv_cache_tokens)
                .sum();
            if !sample.sessions.is_empty() {
                sample.kv_cache_used_tokens = Some(used);
            } else if stats.context_size.is_some() {
                sample.kv_cache_used_tokens = Some(0);
            }
        }
        if sample.kv_cache_total_tokens.is_none() {
            sample.kv_cache_total_tokens = stats
                .context_size
                .map(|c| c * stats.max_concurrent.unwrap_or(1).max(1));
        }
        if let (Some(u), Some(t)) = (sample.kv_cache_used_tokens, sample.kv_cache_total_tokens) {
            if t > 0 && sample.kv_cache_usage_pct.is_none() {
                sample.kv_cache_usage_pct = Some(u as f64 * 100.0 / t as f64);
            }
        }
        if sample.kv_cache_bytes.is_none() {
            sample.kv_cache_bytes = sample
                .kv_cache_used_tokens
                .and_then(|t| stats.kv_bytes_per_token.map(|b| t * b));
        }
    } else if sample.active_sessions.is_none() {
        sample.active_sessions = Some(stats.active);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rec(e2e: f64, ttft: Option<f64>, status: u16, pt: u64, ct: u64) -> RequestRecord {
        RequestRecord {
            e2e_ms: e2e,
            ttft_ms: ttft,
            status,
            prompt_tokens: Some(pt),
            completion_tokens: Some(ct),
            streamed: true,
            ..Default::default()
        }
    }

    #[test]
    fn llama_prompt_total_includes_cached_from_proxy() {
        let mut s = ProxyStats::default();
        let mut r = rec(100.0, Some(10.0), 200, 14, 5);
        r.cached_tokens = Some(13);
        s.record(r);
        let mut sample = EngineSample::now();
        sample.prompt_tokens_total = Some(1); // evaluated only
        sample.extra_insert(PROMPT_EXCLUDES_CACHED, serde_json::json!(true));
        merge_proxy_stats(&mut sample, &s, false);
        assert_eq!(
            (sample.prompt_tokens_total, sample.cached_tokens_total),
            (Some(14), Some(13))
        );
        assert_eq!(sample.extra.unwrap()["evaluated_prompt_tokens_total"], 1);
    }

    #[test]
    fn non_streamed_generation_tps_uses_e2e() {
        // A non-streamed body's first byte is its last: ttft ≈ e2e must not give ~infinite tps.
        let mut s = ProxyStats::default();
        let mut r = rec(500.0, Some(499.99), 200, 10, 50);
        r.streamed = false;
        s.record(r);
        let w = s.take_window(2.0).window_stats();
        assert!((w.generation_tps.unwrap() - 100.0).abs() < 1e-9);
        assert_eq!(w.tpot_ms_avg, None);
    }

    #[test]
    fn counters_and_window() {
        let mut s = ProxyStats::default();
        s.record(rec(1000.0, Some(100.0), 200, 20, 10));
        s.record(rec(2000.0, Some(200.0), 200, 30, 19));
        s.record(rec(50.0, None, 502, 0, 0));
        assert_eq!(s.requests_total, 3);
        assert_eq!(s.requests_success_total, 2);
        assert_eq!(s.errors_total, 1);
        assert_eq!(s.prompt_tokens_total, 50);
        assert_eq!(s.generated_tokens_total, 29);
        let snap = s.take_window(2.0);
        assert!(s.window.is_empty());
        let w = snap.window_stats();
        assert_eq!(w.requests, 3);
        assert_eq!(w.ttft_ms_avg, Some(150.0));
        assert_eq!(w.ttft_ms_p95, Some(200.0));
        assert_eq!(w.e2e_ms_avg, Some(1500.0));
        // tpot: (1000-100)/9 = 100, (2000-200)/18 = 100
        assert_eq!(w.tpot_ms_avg, Some(100.0));
        assert_eq!(w.prompt_tps, Some(25.0));
        // generation tps = 29 tokens / 2.7 s decode time
        assert!((w.generation_tps.unwrap() - 29.0 / 2.7).abs() < 1e-9);
    }

    #[test]
    fn absorb_usage_variants() {
        let mut r = RequestRecord::default();
        r.absorb_usage(&serde_json::json!({"usage": {"prompt_tokens": 18, "completion_tokens": 3,
            "prompt_tokens_details": {"cached_tokens": 5}}, "timings": {"cache_n": 7, "draft_n": 10, "draft_n_accepted": 8}}));
        assert_eq!(r.prompt_tokens, Some(18));
        assert_eq!(r.cached_tokens, Some(7));
        assert_eq!(r.draft_accepted, Some(8));
        let mut a = RequestRecord::default();
        a.absorb_usage(&serde_json::json!({"usage": {"input_tokens": 4, "output_tokens": 6, "cache_read_input_tokens": 2}}));
        assert_eq!(
            (a.prompt_tokens, a.completion_tokens, a.cached_tokens),
            (Some(4), Some(6), Some(2))
        );
    }

    #[test]
    fn merge_for_proxy_derived_engine() {
        let mut stats = ProxyStats {
            active: 1,
            waiting: 2,
            max_concurrent: Some(1),
            context_size: Some(4096),
            kv_bytes_per_token: Some(1024),
            inflight: vec![InflightRequest {
                connection_id: "c1".into(),
                request_id: Some("r1".into()),
                elapsed_ms: 500.0,
                queued: false,
                first_byte_seen: true,
                ttft_ms: Some(100.0),
                prompt_tokens: Some(100),
                chunks_seen: 40,
            }],
            ..Default::default()
        };
        stats.record(rec(1000.0, Some(100.0), 200, 20, 10));
        let mut sample = EngineSample::now();
        merge_proxy_stats(&mut sample, &stats, true);
        assert_eq!(sample.active_sessions, Some(1));
        assert_eq!(sample.waiting_sessions, Some(2));
        assert_eq!(sample.slots_total, Some(1));
        assert_eq!(sample.slots_idle, Some(0));
        assert_eq!(sample.requests_total, Some(1));
        assert_eq!(sample.sessions.len(), 1);
        assert_eq!(sample.sessions[0].state, SessionState::Generating);
        assert_eq!(sample.sessions[0].kv_cache_tokens, Some(140));
        assert_eq!(sample.kv_cache_used_tokens, Some(140));
        assert_eq!(sample.kv_cache_total_tokens, Some(4096));
        assert_eq!(sample.kv_cache_bytes, Some(140 * 1024));
        assert_eq!(sample.sessions[0].tokens_per_second, Some(100.0));
    }

    #[test]
    fn merge_does_not_overwrite_engine_values() {
        let stats = ProxyStats {
            active: 5,
            waiting: 1,
            ..Default::default()
        };
        let mut sample = EngineSample {
            active_sessions: Some(2),
            waiting_sessions: Some(0),
            ..EngineSample::now()
        };
        merge_proxy_stats(&mut sample, &stats, false);
        assert_eq!(sample.active_sessions, Some(2));
        assert_eq!(sample.waiting_sessions, Some(0));
        assert_eq!(sample.requests_total, Some(0));
    }
}
