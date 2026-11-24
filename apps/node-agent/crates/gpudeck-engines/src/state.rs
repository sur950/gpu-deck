//! Process-wide adapter state keyed by engine base URL.
//!
//! * [`Hints`] are registered by `EngineAdapter::command()` (context size, slots, KV bytes/token,
//!   served name...) so a later `scrape(base_url)` — possibly on a different adapter instance —
//!   can compute totals the engine does not expose.
//! * [`Prev`] keeps the previous scrape's counters and histograms per instance URL so rates and
//!   interval percentiles can be derived from deltas.
//!
//! Call [`forget`] when an engine process is stopped.

use std::collections::HashMap;
use std::sync::{Mutex, OnceLock};
use std::time::Instant;

use crate::http::split_model;
use crate::prometheus::Histogram;

#[derive(Debug, Clone, Default, PartialEq)]
pub struct Hints {
    pub served_model_name: Option<String>,
    /// Per-request context (tokens).
    pub context_size: Option<u64>,
    /// Parallel slots / max running requests.
    pub max_concurrency: Option<u64>,
    /// llama.cpp: slots share one KV buffer (`--kv-unified`), so Σ n_ctx over-counts.
    pub kv_unified: bool,
    pub kv_bytes_per_token: Option<u64>,
    /// vLLM `--gpu-memory-utilization` etc. (informational).
    pub memory_fraction: Option<f64>,
    pub router_mode: bool,
    pub models_dir: Option<String>,
}

fn hints_map() -> &'static Mutex<HashMap<String, Hints>> {
    static M: OnceLock<Mutex<HashMap<String, Hints>>> = OnceLock::new();
    M.get_or_init(|| Mutex::new(HashMap::new()))
}

fn key(base_url: &str) -> String {
    split_model(base_url).0
}

pub fn register(base_url: &str, hints: Hints) {
    hints_map()
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .insert(key(base_url), hints);
}

pub fn hints(base_url: &str) -> Option<Hints> {
    hints_map()
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .get(&key(base_url))
        .cloned()
}

/// Drop hints and scrape history for a stopped engine (all router models included).
pub fn forget(base_url: &str) {
    let k = key(base_url);
    hints_map()
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .remove(&k);
    prev_map()
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .retain(|pk, _| split_model(pk).0 != k);
}

/// Drop the scrape history of one instance URL (a router model that was unloaded).
pub fn forget_instance(instance_url: &str) {
    prev_map()
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .remove(instance_url.trim_end_matches('/'));
}

/// Previous scrape of one instance URL (router models are separate entries).
#[derive(Debug, Clone)]
pub struct Prev {
    pub at: Instant,
    pub counters: HashMap<String, f64>,
    pub histograms: HashMap<String, Histogram>,
}

fn prev_map() -> &'static Mutex<HashMap<String, Prev>> {
    static M: OnceLock<Mutex<HashMap<String, Prev>>> = OnceLock::new();
    M.get_or_init(|| Mutex::new(HashMap::new()))
}

/// Store `cur` for `instance_url` and return the previous scrape, if any.
pub fn swap_prev(instance_url: &str, cur: Prev) -> Option<Prev> {
    prev_map()
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .insert(instance_url.trim_end_matches('/').to_string(), cur)
}

/// The previous scrape without replacing it.
pub fn peek_prev(instance_url: &str) -> Option<Prev> {
    prev_map()
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .get(instance_url.trim_end_matches('/'))
        .cloned()
}

/// Helper for counter rates: `(cur − prev) / dt`, `None` without history, on reset or dt≈0.
pub fn rate(prev: Option<&Prev>, name: &str, cur: Option<f64>, now: Instant) -> Option<f64> {
    let p = prev?;
    let c = cur?;
    let pv = *p.counters.get(name)?;
    let dt = now.duration_since(p.at).as_secs_f64();
    if dt < 0.05 || c < pv {
        return None;
    }
    Some((c - pv) / dt)
}

/// Counter delta `cur − prev` (`None` without history or on reset).
pub fn delta(prev: Option<&Prev>, name: &str, cur: Option<f64>) -> Option<f64> {
    let pv = *prev?.counters.get(name)?;
    let c = cur?;
    if c < pv {
        None
    } else {
        Some(c - pv)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn register_and_forget() {
        register(
            "http://127.0.0.1:1?model=a",
            Hints {
                context_size: Some(10),
                ..Default::default()
            },
        );
        assert_eq!(hints("http://127.0.0.1:1").unwrap().context_size, Some(10));
        let p = Prev {
            at: Instant::now(),
            counters: HashMap::new(),
            histograms: HashMap::new(),
        };
        swap_prev("http://127.0.0.1:1?model=a", p.clone());
        assert!(swap_prev("http://127.0.0.1:1?model=a", p.clone()).is_some());
        forget("http://127.0.0.1:1");
        assert!(hints("http://127.0.0.1:1").is_none());
        assert!(swap_prev("http://127.0.0.1:1?model=a", p).is_none());
    }

    #[test]
    fn rates() {
        let t0 = Instant::now();
        let p = Prev {
            at: t0,
            counters: HashMap::from([("x".to_string(), 10.0)]),
            histograms: HashMap::new(),
        };
        let r = rate(
            Some(&p),
            "x",
            Some(30.0),
            t0 + std::time::Duration::from_secs(2),
        )
        .unwrap();
        assert!((r - 10.0).abs() < 1e-9);
        assert_eq!(
            rate(
                Some(&p),
                "x",
                Some(5.0),
                t0 + std::time::Duration::from_secs(2)
            ),
            None
        );
        assert_eq!(delta(Some(&p), "x", Some(12.0)), Some(2.0));
        assert_eq!(rate(None, "x", Some(1.0), t0), None);
    }
}
