//! Interval statistics from Prometheus histograms and counters between two scrapes (vLLM, SGLang,
//! custom Prometheus engines). Histograms are differenced bucket-wise so avg/p50/p95 describe only
//! the requests finished since the previous scrape; the first scrape after start yields `None`.

use std::collections::HashMap;
use std::time::Instant;

use crate::prometheus::{Histogram, Snapshot};
use crate::state::Prev;

/// First histogram present among `names` (metric renames across engine versions).
pub fn first_hist(snap: &Snapshot, names: &[&str]) -> Option<(String, Histogram)> {
    names
        .iter()
        .find_map(|n| snap.histogram(n).map(|h| (n.to_string(), h)))
}

/// The part of `cur` observed since the previous scrape.
pub fn hist_window(prev: Option<&Prev>, name: &str, cur: &Histogram) -> Option<Histogram> {
    let p = prev?.histograms.get(name)?;
    let d = cur.delta(p);
    if d.count > 0.0 {
        Some(d)
    } else {
        None
    }
}

/// `(avg, p50, p95)` in milliseconds for a seconds histogram window.
pub fn ms_stats(h: Option<&Histogram>) -> (Option<f64>, Option<f64>, Option<f64>) {
    match h {
        Some(h) => (
            h.mean().map(|v| v * 1000.0),
            h.quantile(0.5).map(|v| v * 1000.0),
            h.quantile(0.95).map(|v| v * 1000.0),
        ),
        None => (None, None, None),
    }
}

/// Collects the counters/histograms a scrape needs remembered for the next one.
#[derive(Debug, Default)]
pub struct PrevBuilder {
    counters: HashMap<String, f64>,
    histograms: HashMap<String, Histogram>,
}

impl PrevBuilder {
    pub fn counter(&mut self, name: &str, v: Option<f64>) -> Option<f64> {
        if let Some(x) = v {
            self.counters.insert(name.to_string(), x);
        }
        v
    }

    pub fn histogram(&mut self, name: &str, h: &Histogram) {
        self.histograms.insert(name.to_string(), h.clone());
    }

    pub fn build(self, at: Instant) -> Prev {
        Prev {
            at,
            counters: self.counters,
            histograms: self.histograms,
        }
    }
}

/// Window stats for a histogram family: looks up the first name present, records it in `next`,
/// and returns `(avg, p50, p95)` ms over the window.
pub fn hist_ms(
    snap: &Snapshot,
    prev: Option<&Prev>,
    next: &mut PrevBuilder,
    names: &[&str],
) -> (Option<f64>, Option<f64>, Option<f64>) {
    match first_hist(snap, names) {
        Some((name, h)) => {
            let w = hist_window(prev, &name, &h);
            next.histogram(&name, &h);
            ms_stats(w.as_ref())
        }
        None => (None, None, None),
    }
}

/// Ratio of two counter deltas (`num/den`), e.g. prefix-cache hits / queries.
pub fn delta_ratio(
    prev: Option<&Prev>,
    num_name: &str,
    num: Option<f64>,
    den_name: &str,
    den: Option<f64>,
) -> Option<f64> {
    let dn = crate::state::delta(prev, num_name, num)?;
    let dd = crate::state::delta(prev, den_name, den)?;
    if dd > 0.0 {
        Some(dn / dd)
    } else {
        None
    }
}
