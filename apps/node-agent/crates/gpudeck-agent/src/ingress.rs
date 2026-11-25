//! Inbound defences shared by every listener the agent may open (direct-mode proxy, SSH-instance
//! forwarders): a per-source handshake rate limiter, and the monitor that counts rejected
//! attempts (by reason and source) and reports them as one `event(ingress_rejected)` per
//! interval — `critical` (alert) when an interval reaches the burst threshold.

use std::collections::{BTreeMap, HashMap};
use std::net::IpAddr;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use gpudeck_protocol::{hub_feature, Event, EventKind, JsonObject};

use crate::agent::Agent;

/// Most sources kept per interval (the rest are only counted).
const MAX_SOURCES: usize = 1024;
/// Sources listed in one report.
const REPORT_SOURCES: usize = 20;

#[derive(Default)]
struct Window {
    total: u64,
    reasons: BTreeMap<String, u64>,
    listeners: BTreeMap<String, u64>,
    sources: HashMap<IpAddr, u64>,
    other_sources: u64,
}

/// Counts rejected inbound attempts; [`IngressMonitor::take`] empties the current window.
#[derive(Default)]
pub struct IngressMonitor {
    window: Mutex<Window>,
    /// Lifetime totals (Prometheus).
    pub total: AtomicU64,
}

/// One interval's rejected attempts.
#[derive(Debug, Clone, PartialEq)]
pub struct Summary {
    pub total: u64,
    pub reasons: BTreeMap<String, u64>,
    pub listeners: BTreeMap<String, u64>,
    /// Top sources by count (at most 20).
    pub sources: Vec<(IpAddr, u64)>,
    pub distinct_sources: usize,
}

impl IngressMonitor {
    pub fn record(&self, listener: &str, reason: &str, ip: IpAddr) {
        self.total.fetch_add(1, Ordering::Relaxed);
        let mut w = self.window.lock().unwrap_or_else(|e| e.into_inner());
        w.total += 1;
        *w.reasons.entry(reason.to_string()).or_default() += 1;
        *w.listeners.entry(listener.to_string()).or_default() += 1;
        if w.sources.len() < MAX_SOURCES || w.sources.contains_key(&ip) {
            *w.sources.entry(ip).or_default() += 1;
        } else {
            w.other_sources += 1;
        }
        tracing::debug!(%ip, listener, reason, "inbound connection rejected");
    }

    pub fn take(&self) -> Option<Summary> {
        let w = std::mem::take(&mut *self.window.lock().unwrap_or_else(|e| e.into_inner()));
        if w.total == 0 {
            return None;
        }
        let distinct = w.sources.len();
        let mut sources: Vec<(IpAddr, u64)> = w.sources.into_iter().collect();
        sources.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(&b.0)));
        sources.truncate(REPORT_SOURCES);
        Some(Summary {
            total: w.total,
            reasons: w.reasons,
            listeners: w.listeners,
            sources,
            distinct_sources: distinct + usize::from(w.other_sources > 0),
        })
    }
}

impl Summary {
    pub fn event(&self, window: Duration, burst_threshold: u64) -> Event {
        let message = format!(
            "{} inbound connection attempt(s) rejected in {} s from {} source(s)",
            self.total,
            window.as_secs(),
            self.distinct_sources
        );
        let mut ev = if self.total >= burst_threshold {
            Event::critical(EventKind::IngressRejected, message)
        } else {
            Event::warning(EventKind::IngressRejected, message)
        };
        let mut d = JsonObject::new();
        d.insert("window_seconds".into(), window.as_secs().into());
        d.insert("total".into(), self.total.into());
        d.insert("burst_threshold".into(), burst_threshold.into());
        d.insert(
            "reasons".into(),
            serde_json::to_value(&self.reasons).unwrap_or_default(),
        );
        d.insert(
            "listeners".into(),
            serde_json::to_value(&self.listeners).unwrap_or_default(),
        );
        d.insert(
            "sources".into(),
            self.sources
                .iter()
                .map(|(ip, n)| serde_json::json!({"ip": ip.to_string(), "count": n}))
                .collect::<Vec<_>>()
                .into(),
        );
        d.insert("distinct_sources".into(), self.distinct_sources.into());
        ev.details = Some(d);
        ev
    }
}

/// Token bucket per source address (handshakes per minute + burst).
pub struct HandshakeLimiter {
    per_minute: f64,
    burst: f64,
    buckets: HashMap<IpAddr, (f64, Instant)>,
}

impl HandshakeLimiter {
    pub fn new(per_minute: u32, burst: u32) -> Self {
        Self {
            per_minute: f64::from(per_minute.max(1)),
            burst: f64::from(burst.max(1)),
            buckets: HashMap::new(),
        }
    }

    pub fn configure(&mut self, per_minute: u32, burst: u32) {
        self.per_minute = f64::from(per_minute.max(1));
        self.burst = f64::from(burst.max(1));
    }

    /// Take one token for `ip`; false = over the limit.
    pub fn allow(&mut self, ip: IpAddr, now: Instant) -> bool {
        if self.buckets.len() > 50_000 {
            // Forget sources whose bucket refilled completely (bounded memory under a scan).
            let full = self.burst / (self.per_minute / 60.0);
            self.buckets
                .retain(|_, (_, t)| now.duration_since(*t).as_secs_f64() < full);
        }
        let rate = self.per_minute / 60.0;
        let (tokens, last) = self.buckets.entry(ip).or_insert((self.burst, now));
        let refill = now.duration_since(*last).as_secs_f64() * rate;
        *tokens = (*tokens + refill).min(self.burst);
        *last = now;
        if *tokens >= 1.0 {
            *tokens -= 1.0;
            true
        } else {
            false
        }
    }
}

/// Report loop: one `event(ingress_rejected)` per interval with rejections (hubs that list
/// `secure_transport`; otherwise logged only).
pub async fn report_loop(agent: Arc<Agent>) {
    loop {
        let (interval, threshold) = agent.ingress_report_params();
        tokio::select! {
            _ = tokio::time::sleep(interval) => {}
            _ = agent.shutdown.cancelled() => return,
        }
        let Some(summary) = agent.ingress.take() else {
            continue;
        };
        tracing::warn!(
            total = summary.total,
            sources = summary.distinct_sources,
            reasons = ?summary.reasons,
            "rejected inbound connection attempts"
        );
        if agent.rt().hub_may_have(hub_feature::SECURE_TRANSPORT) {
            agent.event(summary.event(interval, threshold));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn monitor_summarises_and_resets() {
        let m = IngressMonitor::default();
        assert!(m.take().is_none());
        let a: IpAddr = "203.0.113.9".parse().unwrap();
        let b: IpAddr = "198.51.100.1".parse().unwrap();
        for _ in 0..25 {
            m.record("proxy", "source_not_allowed", a);
        }
        m.record("ssh", "source_not_allowed", b);
        let s = m.take().unwrap();
        assert_eq!(s.total, 26);
        assert_eq!(s.sources[0], (a, 25));
        assert_eq!(s.listeners["proxy"], 25);
        assert_eq!(s.distinct_sources, 2);
        let ev = s.event(Duration::from_secs(60), 20);
        assert_eq!(ev.severity, gpudeck_protocol::Severity::Critical);
        assert_eq!(ev.kind, EventKind::IngressRejected);
        assert!(m.take().is_none(), "window resets");
        m.record("proxy", "rate_limited", a);
        let quiet = m.take().unwrap().event(Duration::from_secs(60), 20);
        assert_eq!(quiet.severity, gpudeck_protocol::Severity::Warning);
        assert_eq!(m.total.load(Ordering::Relaxed), 27);
    }

    #[test]
    fn limiter_bursts_then_refills() {
        let mut l = HandshakeLimiter::new(60, 3);
        let ip: IpAddr = "203.0.113.9".parse().unwrap();
        let t0 = Instant::now();
        assert!(l.allow(ip, t0) && l.allow(ip, t0) && l.allow(ip, t0));
        assert!(!l.allow(ip, t0), "burst exhausted");
        assert!(l.allow("10.0.0.1".parse().unwrap(), t0), "per source");
        assert!(l.allow(ip, t0 + Duration::from_millis(1100)), "1/s refill");
    }
}
