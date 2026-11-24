//! Per-instance request accounting done by the :9443 proxy (`metrics.md` §4): monotonic
//! counters since agent start, the window of requests completed since the last scrape, and the
//! in-flight set (for `active_sessions`, `waiting_sessions` and proxy-derived session samples).

use std::collections::{BTreeMap, VecDeque};
use std::time::Instant;

use serde::{Deserialize, Serialize};

/// What the proxy knows about one finished request.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct RequestRecord {
    pub request_id: Option<String>,
    pub e2e_ms: f64,
    pub ttft_ms: Option<f64>,
    pub queue_ms: f64,
    pub status: u16,
    pub transport_error: bool,
    pub prompt_tokens: Option<u64>,
    pub completion_tokens: Option<u64>,
    pub cached_tokens: Option<u64>,
    pub reasoning_tokens: Option<u64>,
    pub draft_tokens: Option<u64>,
    pub draft_accepted: Option<u64>,
    pub media_units: Option<f64>,
    pub streamed: bool,
    pub bytes_out: u64,
}

impl RequestRecord {
    pub fn is_error(&self) -> bool {
        self.transport_error || self.status >= 500
    }

    /// Absorb an OpenAI / Anthropic / llama.cpp response object (`usage`, `timings`).
    pub fn absorb(&mut self, body: &serde_json::Value) {
        // Responses API SSE: {"type":"response.completed","response":{"usage":...}}
        let body = body
            .get("response")
            .filter(|r| r.get("usage").is_some())
            .unwrap_or(body);
        // Anthropic SSE message_start: {"message":{"usage":...}}
        let body = body
            .get("message")
            .filter(|m| m.get("usage").is_some())
            .unwrap_or(body);
        if let Some(u) = body.get("usage").filter(|u| u.is_object()) {
            let g = |k: &str| u.get(k).and_then(|v| v.as_u64());
            if let Some(v) = g("prompt_tokens").or_else(|| g("input_tokens")) {
                self.prompt_tokens = Some(v);
            }
            if let Some(v) = g("completion_tokens").or_else(|| g("output_tokens")) {
                self.completion_tokens = Some(v);
            }
            let cached = u
                .pointer("/prompt_tokens_details/cached_tokens")
                .or_else(|| u.pointer("/input_tokens_details/cached_tokens"))
                .or_else(|| u.get("cache_read_input_tokens"))
                .and_then(|v| v.as_u64());
            if cached.is_some() {
                self.cached_tokens = cached;
            }
            let reasoning = u
                .pointer("/completion_tokens_details/reasoning_tokens")
                .or_else(|| u.pointer("/output_tokens_details/reasoning_tokens"))
                .and_then(|v| v.as_u64());
            if reasoning.is_some() {
                self.reasoning_tokens = reasoning;
            }
        }
        if let Some(t) = body.get("timings").filter(|t| t.is_object()) {
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

/// An in-flight request.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Inflight {
    pub connection_id: String,
    pub request_id: Option<String>,
    pub elapsed_ms: f64,
    pub queued: bool,
    pub first_byte_seen: bool,
    pub ttft_ms: Option<f64>,
    pub prompt_tokens: Option<u64>,
    pub chunks_seen: u64,
}

#[derive(Debug, Clone)]
struct InflightState {
    request_id: Option<String>,
    started: Instant,
    queued: bool,
    first_byte: Option<Instant>,
    prompt_tokens: Option<u64>,
    chunks: u64,
}

/// Counters + window + in-flight requests for one instance.
#[derive(Debug, Default)]
pub struct InstanceStats {
    pub requests_total: u64,
    pub requests_success_total: u64,
    pub errors_total: u64,
    pub prompt_tokens_total: u64,
    pub generated_tokens_total: u64,
    pub cached_tokens_total: Option<u64>,
    pub draft_tokens_total: u64,
    pub draft_accepted_total: u64,
    window: VecDeque<RequestRecord>,
    window_started: Option<Instant>,
    inflight: BTreeMap<u64, InflightState>,
    next_id: u64,
}

impl InstanceStats {
    /// Register a new request; returns its connection id.
    pub fn begin(&mut self, request_id: Option<String>, queued: bool) -> u64 {
        self.next_id += 1;
        let id = self.next_id;
        self.inflight.insert(
            id,
            InflightState {
                request_id,
                started: Instant::now(),
                queued,
                first_byte: None,
                prompt_tokens: None,
                chunks: 0,
            },
        );
        id
    }

    pub fn dequeued(&mut self, id: u64) {
        if let Some(s) = self.inflight.get_mut(&id) {
            s.queued = false;
        }
    }

    pub fn first_byte(&mut self, id: u64) {
        if let Some(s) = self.inflight.get_mut(&id) {
            s.first_byte.get_or_insert_with(Instant::now);
        }
    }

    pub fn chunk(&mut self, id: u64) {
        if let Some(s) = self.inflight.get_mut(&id) {
            s.chunks += 1;
        }
    }

    pub fn set_prompt_tokens(&mut self, id: u64, n: u64) {
        if let Some(s) = self.inflight.get_mut(&id) {
            s.prompt_tokens = Some(n);
        }
    }

    /// Finish a request and record it.
    pub fn finish(&mut self, id: u64, rec: RequestRecord) {
        self.inflight.remove(&id);
        self.record(rec);
    }

    /// Drop an in-flight entry without recording (client went away before forwarding).
    pub fn abandon(&mut self, id: u64) {
        self.inflight.remove(&id);
    }

    /// The handler was dropped (client closed the connection) while the request was still in
    /// flight and no response body observer owns it: record it as `499` so the entry does not
    /// leak into `active_sessions`. No-op when the request already finished.
    pub fn cancel_if_inflight(&mut self, id: u64) -> bool {
        let Some(st) = self.inflight.remove(&id) else {
            return false;
        };
        let rec = RequestRecord {
            request_id: st.request_id,
            e2e_ms: st.started.elapsed().as_secs_f64() * 1000.0,
            status: 499,
            ..Default::default()
        };
        self.record(rec);
        true
    }

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
        while self.window.len() > 10_000 {
            self.window.pop_front();
        }
    }

    pub fn active(&self) -> u64 {
        self.inflight.values().filter(|s| !s.queued).count() as u64
    }

    pub fn waiting(&self) -> u64 {
        self.inflight.values().filter(|s| s.queued).count() as u64
    }

    /// Copy counters and in-flight state; move the window out (it restarts now).
    pub fn take_snapshot(&mut self, max_concurrent: Option<u64>) -> crate::backend::ProxySnapshot {
        let now = Instant::now();
        let window_seconds = self
            .window_started
            .map(|t| now.duration_since(t).as_secs_f64())
            .unwrap_or(0.0);
        self.window_started = Some(now);
        let window: Vec<RequestRecord> = self.window.drain(..).collect();
        crate::backend::ProxySnapshot {
            requests_total: self.requests_total,
            requests_success_total: self.requests_success_total,
            errors_total: self.errors_total,
            prompt_tokens_total: self.prompt_tokens_total,
            generated_tokens_total: self.generated_tokens_total,
            cached_tokens_total: self.cached_tokens_total,
            draft_tokens_total: self.draft_tokens_total,
            draft_accepted_total: self.draft_accepted_total,
            active: self.active(),
            waiting: self.waiting(),
            max_concurrent,
            window,
            window_seconds,
            inflight: self.inflight_view(),
            kv_bytes_per_token: None,
            context_size: None,
        }
    }

    pub fn inflight_view(&self) -> Vec<Inflight> {
        let now = Instant::now();
        self.inflight
            .iter()
            .map(|(id, s)| Inflight {
                connection_id: format!("proxy-{id}"),
                request_id: s.request_id.clone(),
                elapsed_ms: now.duration_since(s.started).as_secs_f64() * 1000.0,
                queued: s.queued,
                first_byte_seen: s.first_byte.is_some(),
                ttft_ms: s
                    .first_byte
                    .map(|f| f.duration_since(s.started).as_secs_f64() * 1000.0),
                prompt_tokens: s.prompt_tokens,
                chunks_seen: s.chunks,
            })
            .collect()
    }
}

/// Window aggregates used by the generic merge.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct WindowStats {
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

pub fn window_stats(window: &[RequestRecord], window_seconds: f64) -> WindowStats {
    let ok: Vec<&RequestRecord> = window.iter().filter(|r| !r.is_error()).collect();
    let mut ttft: Vec<f64> = ok.iter().filter_map(|r| r.ttft_ms).collect();
    let mut e2e: Vec<f64> = ok.iter().map(|r| r.e2e_ms).collect();
    let queue: Vec<f64> = window.iter().map(|r| r.queue_ms).collect();
    let mut tpot = vec![];
    let (mut gen, mut prompt) = (0u64, 0u64);
    for r in &ok {
        if let Some(ct) = r.completion_tokens {
            gen += ct;
            let decode = r.e2e_ms - r.ttft_ms.unwrap_or(0.0);
            if ct > 1 && decode > 0.0 {
                tpot.push(decode / (ct - 1) as f64);
            }
        }
        prompt += r.prompt_tokens.unwrap_or(0);
    }
    sort(&mut ttft);
    sort(&mut e2e);
    let secs = if window_seconds > 0.0 {
        window_seconds
    } else {
        2.0
    };
    WindowStats {
        ttft_ms_avg: mean(&ttft),
        ttft_ms_p50: percentile(&ttft, 0.5),
        ttft_ms_p95: percentile(&ttft, 0.95),
        e2e_ms_avg: mean(&e2e),
        e2e_ms_p50: percentile(&e2e, 0.5),
        e2e_ms_p95: percentile(&e2e, 0.95),
        tpot_ms_avg: mean(&tpot),
        queue_ms_avg: mean(&queue),
        prompt_tps: (!ok.is_empty()).then(|| prompt as f64 / secs),
        generation_tps: (!ok.is_empty()).then(|| gen as f64 / secs),
    }
}

pub fn sort(v: &mut [f64]) {
    v.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
}

pub fn mean(v: &[f64]) -> Option<f64> {
    (!v.is_empty()).then(|| v.iter().sum::<f64>() / v.len() as f64)
}

/// Nearest-rank percentile of a sorted slice.
pub fn percentile(sorted: &[f64], q: f64) -> Option<f64> {
    if sorted.is_empty() {
        return None;
    }
    let rank = ((q * sorted.len() as f64).ceil() as usize).clamp(1, sorted.len());
    Some(sorted[rank - 1])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cancelled_inflight_does_not_leak() {
        let mut s = InstanceStats::default();
        let a = s.begin(None, false);
        let b = s.begin(None, false);
        s.abandon(a);
        assert!(
            !s.cancel_if_inflight(a),
            "already closed entries are left alone"
        );
        assert!(s.cancel_if_inflight(b));
        assert!(s.inflight.is_empty());
        assert_eq!((s.requests_total, s.errors_total), (1, 0));
    }

    #[test]
    fn absorbs_openai_llama_and_anthropic_usage() {
        let mut r = RequestRecord::default();
        r.absorb(&serde_json::json!({"usage":{"prompt_tokens":19,"completion_tokens":200,"prompt_tokens_details":{"cached_tokens":3}},"timings":{"cache_n":5,"prompt_n":14,"predicted_n":200,"draft_n":10,"draft_n_accepted":7}}));
        assert_eq!(
            (r.prompt_tokens, r.completion_tokens, r.cached_tokens),
            (Some(19), Some(200), Some(5))
        );
        assert_eq!((r.draft_tokens, r.draft_accepted), (Some(10), Some(7)));
        let mut a = RequestRecord::default();
        a.absorb(&serde_json::json!({"type":"message_start","message":{"usage":{"input_tokens":12,"cache_read_input_tokens":4,"output_tokens":1}}}));
        a.absorb(&serde_json::json!({"type":"message_delta","usage":{"output_tokens":30}}));
        assert_eq!(
            (a.prompt_tokens, a.completion_tokens, a.cached_tokens),
            (Some(12), Some(30), Some(4))
        );
        let mut resp = RequestRecord::default();
        resp.absorb(&serde_json::json!({"type":"response.completed","response":{"usage":{"input_tokens":8,"output_tokens":9,"output_tokens_details":{"reasoning_tokens":2}}}}));
        assert_eq!(
            (
                resp.prompt_tokens,
                resp.completion_tokens,
                resp.reasoning_tokens
            ),
            (Some(8), Some(9), Some(2))
        );
    }

    #[test]
    fn counters_window_and_inflight() {
        let mut s = InstanceStats::default();
        let a = s.begin(Some("r1".into()), true);
        let b = s.begin(None, false);
        assert_eq!((s.active(), s.waiting()), (1, 1));
        s.dequeued(a);
        s.first_byte(a);
        assert_eq!((s.active(), s.waiting()), (2, 0));
        s.finish(
            a,
            RequestRecord {
                status: 200,
                e2e_ms: 100.0,
                ttft_ms: Some(20.0),
                prompt_tokens: Some(10),
                completion_tokens: Some(9),
                ..Default::default()
            },
        );
        s.finish(
            b,
            RequestRecord {
                status: 502,
                transport_error: true,
                ..Default::default()
            },
        );
        let snap = s.take_snapshot(Some(4));
        assert_eq!(
            (
                snap.requests_total,
                snap.requests_success_total,
                snap.errors_total
            ),
            (2, 1, 1)
        );
        assert_eq!(snap.window.len(), 2);
        assert_eq!(snap.prompt_tokens_total, 10);
        let w = window_stats(&snap.window, 2.0);
        assert_eq!(w.ttft_ms_p95, Some(20.0));
        assert_eq!(w.tpot_ms_avg, Some(10.0));
        assert_eq!(w.generation_tps, Some(4.5));
        assert!(s.take_snapshot(None).window.is_empty());
    }

    #[test]
    fn percentile_nearest_rank() {
        let v: Vec<f64> = (1..=100).map(f64::from).collect();
        assert_eq!(percentile(&v, 0.95), Some(95.0));
        assert_eq!(percentile(&v, 0.5), Some(50.0));
        assert_eq!(percentile(&[], 0.5), None);
    }
}
