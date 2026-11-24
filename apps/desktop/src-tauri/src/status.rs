//! Payloads of the two remote-callable commands (`notify`, `set_tray_status`):
//! parsing, sanitising and the text shown in the tray menu.
//!
//! Everything a server page sends is untrusted: strings are trimmed, stripped of
//! control characters and capped; lists are capped; paths are validated with
//! `deeplink::validate_app_path` so a click can only navigate inside the
//! connected server's origin. Pure code, unit-tested.

use std::collections::VecDeque;
use std::time::{Duration, Instant};

use serde::Deserialize;

use crate::deeplink::validate_app_path;

pub const MAX_TITLE: usize = 120;
pub const MAX_BODY: usize = 400;
pub const MAX_ITEMS: usize = 5;
pub const MAX_LABEL: usize = 80;

pub fn clean(s: &str, max: usize) -> String {
    let collapsed: String = s
        .chars()
        .map(|c| if c.is_control() { ' ' } else { c })
        .collect::<String>()
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ");
    if collapsed.chars().count() <= max {
        collapsed
    } else {
        let mut out: String = collapsed.chars().take(max.saturating_sub(1)).collect();
        out.push('…');
        out
    }
}

fn clean_path(path: Option<&str>) -> Option<String> {
    path.and_then(|p| validate_app_path(p).ok())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum Severity {
    #[default]
    Info,
    Warning,
    Critical,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct NotifyPayload {
    pub title: String,
    #[serde(default)]
    pub body: Option<String>,
    #[serde(default)]
    pub path: Option<String>,
    #[serde(default)]
    pub severity: Severity,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Notification {
    pub title: String,
    pub body: String,
    pub path: Option<String>,
    pub severity: Severity,
}

impl NotifyPayload {
    pub fn sanitize(&self) -> Result<Notification, &'static str> {
        let title = clean(&self.title, MAX_TITLE);
        if title.is_empty() {
            return Err("title is required");
        }
        Ok(Notification {
            title,
            body: clean(self.body.as_deref().unwrap_or(""), MAX_BODY),
            path: clean_path(self.path.as_deref()),
            severity: self.severity,
        })
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum Health {
    Ok,
    Degraded,
    Down,
    #[default]
    Unknown,
}

#[derive(Debug, Clone, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct TrayItemPayload {
    pub label: String,
    #[serde(default)]
    pub path: Option<String>,
}

#[derive(Debug, Clone, Deserialize, Default)]
#[serde(rename_all = "camelCase", default)]
pub struct TrayStatusPayload {
    pub health: Health,
    pub nodes_online: Option<u32>,
    pub nodes_total: Option<u32>,
    pub devices_unhealthy: Option<u32>,
    pub deployments_running: Option<u32>,
    pub swaps_in_progress: Option<u32>,
    pub alerts_open: Option<u32>,
    pub active_swaps: Vec<TrayItemPayload>,
    pub open_alerts: Vec<TrayItemPayload>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TrayItem {
    pub label: String,
    pub path: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct TrayStatus {
    pub health: Health,
    pub nodes_online: Option<u32>,
    pub nodes_total: Option<u32>,
    pub devices_unhealthy: Option<u32>,
    pub deployments_running: Option<u32>,
    pub swaps_in_progress: u32,
    pub alerts_open: u32,
    pub active_swaps: Vec<TrayItem>,
    pub open_alerts: Vec<TrayItem>,
}

fn items(list: &[TrayItemPayload]) -> Vec<TrayItem> {
    list.iter()
        .filter_map(|i| {
            let label = clean(&i.label, MAX_LABEL);
            (!label.is_empty()).then(|| TrayItem {
                label,
                path: clean_path(i.path.as_deref()),
            })
        })
        .take(MAX_ITEMS)
        .collect()
}

impl TrayStatusPayload {
    pub fn sanitize(&self) -> TrayStatus {
        let cap = |v: Option<u32>| v.map(|n| n.min(1_000_000));
        let active_swaps = items(&self.active_swaps);
        let open_alerts = items(&self.open_alerts);
        TrayStatus {
            health: self.health,
            nodes_online: cap(self.nodes_online),
            nodes_total: cap(self.nodes_total),
            devices_unhealthy: cap(self.devices_unhealthy),
            deployments_running: cap(self.deployments_running),
            swaps_in_progress: cap(self.swaps_in_progress)
                .unwrap_or(0)
                .max(active_swaps.len() as u32),
            alerts_open: cap(self.alerts_open)
                .unwrap_or(0)
                .max(open_alerts.len() as u32),
            active_swaps,
            open_alerts,
        }
    }
}

impl TrayStatus {
    pub fn health_label(&self) -> &'static str {
        match self.health {
            Health::Ok => "Healthy",
            Health::Degraded => "Degraded",
            Health::Down => "Unreachable",
            Health::Unknown => "Status unknown",
        }
    }

    /// Disabled, informational lines at the top of the tray menu.
    pub fn summary_lines(&self) -> Vec<String> {
        let mut out = vec![format!("Fleet: {}", self.health_label())];
        let mut parts = Vec::new();
        if let (Some(on), Some(total)) = (self.nodes_online, self.nodes_total) {
            parts.push(format!("{on}/{total} nodes online"));
        }
        if let Some(bad) = self.devices_unhealthy.filter(|n| *n > 0) {
            parts.push(format!(
                "{bad} GPU{} unhealthy",
                if bad == 1 { "" } else { "s" }
            ));
        }
        if let Some(dep) = self.deployments_running {
            parts.push(format!(
                "{dep} deployment{} running",
                if dep == 1 { "" } else { "s" }
            ));
        }
        if !parts.is_empty() {
            out.push(parts.join(" · "));
        }
        out
    }

    pub fn tooltip(&self, server: &str) -> String {
        let mut t = format!("GPU Deck · {server} · {}", self.health_label());
        if self.alerts_open > 0 {
            t.push_str(&format!(
                " · {} open alert{}",
                self.alerts_open,
                if self.alerts_open == 1 { "" } else { "s" }
            ));
        }
        if self.swaps_in_progress > 0 {
            t.push_str(&format!(
                " · {} swap{} running",
                self.swaps_in_progress,
                if self.swaps_in_progress == 1 { "" } else { "s" }
            ));
        }
        t
    }

    pub fn swaps_title(&self) -> String {
        format!("Active swaps ({})", self.swaps_in_progress)
    }

    pub fn alerts_title(&self) -> String {
        format!("Open alerts ({})", self.alerts_open)
    }
}

/// Sliding-window limiter so a misbehaving page cannot flood the OS notification centre.
#[derive(Debug)]
pub struct RateLimiter {
    window: Duration,
    max: usize,
    hits: VecDeque<Instant>,
}

impl RateLimiter {
    pub fn new(max: usize, window: Duration) -> Self {
        Self {
            window,
            max,
            hits: VecDeque::new(),
        }
    }

    pub fn allow(&mut self, now: Instant) -> bool {
        while let Some(front) = self.hits.front() {
            if now.duration_since(*front) >= self.window {
                self.hits.pop_front();
            } else {
                break;
            }
        }
        if self.hits.len() >= self.max {
            return false;
        }
        self.hits.push_back(now);
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn clean_strips_controls_and_caps() {
        assert_eq!(clean("  a\n\tb\u{0}c  ", 50), "a b c");
        assert_eq!(clean("abcdef", 4), "abc…");
        assert_eq!(clean("ééé", 3), "ééé");
    }

    #[test]
    fn notify_payload_is_sanitised() {
        let p: NotifyPayload = serde_json::from_value(serde_json::json!({
            "title": "  GPU 0 overheating\n",
            "body": "x".repeat(1000),
            "path": "//evil.example.com",
            "severity": "critical"
        }))
        .unwrap();
        let n = p.sanitize().unwrap();
        assert_eq!(n.title, "GPU 0 overheating");
        assert_eq!(n.body.chars().count(), MAX_BODY);
        assert_eq!(n.path, None);
        assert_eq!(n.severity, Severity::Critical);
        let ok: NotifyPayload =
            serde_json::from_value(serde_json::json!({"title": "t", "path": "/alerts"})).unwrap();
        assert_eq!(ok.sanitize().unwrap().path.as_deref(), Some("/alerts"));
        assert_eq!(ok.severity, Severity::Info);
        let empty: NotifyPayload =
            serde_json::from_value(serde_json::json!({"title": " \n "})).unwrap();
        assert!(empty.sanitize().is_err());
        assert!(serde_json::from_value::<NotifyPayload>(
            serde_json::json!({"title": "t", "severity": "boom"})
        )
        .is_err());
    }

    #[test]
    fn tray_status_is_capped_and_summarised() {
        let p: TrayStatusPayload = serde_json::from_value(serde_json::json!({
            "health": "degraded",
            "nodesOnline": 3, "nodesTotal": 4, "devicesUnhealthy": 1, "deploymentsRunning": 2,
            "swapsInProgress": 0, "alertsOpen": 9,
            "activeSwaps": [{"label": "qwen3 → node-2", "path": "/swaps/1"}],
            "openAlerts": (0..12).map(|i| serde_json::json!({"label": format!("alert {i}"), "path": "/alerts"})).collect::<Vec<_>>()
        }))
        .unwrap();
        let s = p.sanitize();
        assert_eq!(s.open_alerts.len(), MAX_ITEMS);
        assert_eq!(s.swaps_in_progress, 1, "at least the listed swaps");
        assert_eq!(s.alerts_open, 9);
        assert_eq!(
            s.summary_lines(),
            vec![
                "Fleet: Degraded".to_string(),
                "3/4 nodes online · 1 GPU unhealthy · 2 deployments running".to_string()
            ]
        );
        assert_eq!(
            s.tooltip("Lab"),
            "GPU Deck · Lab · Degraded · 9 open alerts · 1 swap running"
        );
        assert_eq!(s.alerts_title(), "Open alerts (9)");
        let unknown = TrayStatusPayload::default().sanitize();
        assert_eq!(
            unknown.summary_lines(),
            vec!["Fleet: Status unknown".to_string()]
        );
        // empty labels dropped, bad paths dropped
        let p: TrayStatusPayload = serde_json::from_value(serde_json::json!({
            "openAlerts": [{"label": "  "}, {"label": "ok", "path": "https://evil.example.com"}]
        }))
        .unwrap();
        let s = p.sanitize();
        assert_eq!(
            s.open_alerts,
            vec![TrayItem {
                label: "ok".into(),
                path: None
            }]
        );
    }

    #[test]
    fn rate_limiter_window() {
        let mut rl = RateLimiter::new(2, Duration::from_secs(10));
        let t0 = Instant::now();
        assert!(rl.allow(t0));
        assert!(rl.allow(t0 + Duration::from_secs(1)));
        assert!(!rl.allow(t0 + Duration::from_secs(2)));
        assert!(rl.allow(t0 + Duration::from_secs(10)));
    }
}
