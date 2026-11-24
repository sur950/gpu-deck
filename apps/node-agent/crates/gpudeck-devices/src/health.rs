//! Device health derived from successive samples (`metrics.md` §5).
//!
//! Every plugin feeds its samples into a [`HealthTracker`] per device; `detect()` then stamps the
//! resulting `(health, health_reason)` on the inventory `Device`. Rules:
//!
//! * **unhealthy** — uncorrectable ECC count increased, a fatal error was reported by the plugin
//!   (NVIDIA Xid 48/63/79, `GPU_IS_LOST`, retired pages pending, AMD RAS uncorrectable), or the
//!   device stopped answering.
//! * **degraded** — thermally / hardware throttled (`hw_slowdown`, `sw_thermal`, `hw_thermal`) for
//!   longer than 60 s, or temperature above the vendor limit (NVIDIA 90 °C, AMD/Hygon 95 °C,
//!   others 95 °C), or non-fatal errors reported in the last interval.
//! * **healthy** — the device answers and none of the above holds.
//!
//! An unhealthy verdict is sticky for [`UNHEALTHY_HOLD`] so a single good sample does not flap
//! the inventory.

use std::time::{Duration, Instant};

use crate::types::{Device, DeviceHealth, DeviceSample, Vendor};

/// How long throttling must persist before the device is reported degraded.
pub const THROTTLE_DEGRADED_AFTER: Duration = Duration::from_secs(60);
/// How long an unhealthy verdict is kept after the condition clears.
pub const UNHEALTHY_HOLD: Duration = Duration::from_secs(300);

/// Throttle reasons that indicate a hardware / thermal problem (not power-cap or idle).
pub const SERIOUS_THROTTLE: &[&str] =
    &["hw_slowdown", "sw_thermal", "hw_thermal", "hw_power_brake"];

/// Temperature above which a device is reported degraded.
pub fn temperature_limit_c(vendor: Vendor) -> f64 {
    match vendor {
        Vendor::Nvidia => 90.0,
        _ => 95.0,
    }
}

#[derive(Debug, Clone)]
pub struct HealthTracker {
    vendor: Vendor,
    last_ecc: Option<u64>,
    throttled_since: Option<Instant>,
    unhealthy_until: Option<Instant>,
    unhealthy_reason: Option<String>,
    health: DeviceHealth,
    reason: Option<String>,
}

impl HealthTracker {
    pub fn new(vendor: Vendor) -> Self {
        HealthTracker {
            vendor,
            last_ecc: None,
            throttled_since: None,
            unhealthy_until: None,
            unhealthy_reason: None,
            health: DeviceHealth::Unknown,
            reason: None,
        }
    }

    pub fn health(&self) -> DeviceHealth {
        self.health
    }

    pub fn reason(&self) -> Option<&str> {
        self.reason.as_deref()
    }

    /// Record a fatal condition reported out of band (Xid 79, GPU lost, RAS uncorrectable...).
    pub fn mark_fatal(&mut self, reason: impl Into<String>) {
        self.mark_fatal_at(reason, Instant::now());
    }

    fn mark_fatal_at(&mut self, reason: impl Into<String>, now: Instant) {
        let reason = reason.into();
        self.unhealthy_until = Some(now + UNHEALTHY_HOLD);
        self.unhealthy_reason = Some(reason.clone());
        self.health = DeviceHealth::Unhealthy;
        self.reason = Some(reason);
    }

    /// The device did not answer (SMI failed / NVML lost it).
    pub fn mark_unreachable(&mut self, reason: impl Into<String>) {
        self.mark_fatal(reason);
    }

    /// Feed one sample; returns the new health.
    pub fn observe(&mut self, s: &DeviceSample) -> DeviceHealth {
        self.observe_at(s, Instant::now())
    }

    pub fn observe_at(&mut self, s: &DeviceSample, now: Instant) -> DeviceHealth {
        // Uncorrectable ECC growth is fatal.
        if let Some(ecc) = s.ecc_errors_total {
            if let Some(prev) = self.last_ecc {
                if ecc > prev {
                    self.mark_fatal_at(
                        format!("uncorrectable ECC errors increased by {}", ecc - prev),
                        now,
                    );
                }
            }
            self.last_ecc = Some(ecc);
        }

        let serious_throttle = s
            .throttle_reasons
            .as_ref()
            .map(|r| r.iter().any(|x| SERIOUS_THROTTLE.contains(&x.as_str())))
            .unwrap_or(false);
        if serious_throttle {
            self.throttled_since.get_or_insert(now);
        } else {
            self.throttled_since = None;
        }

        if let Some(until) = self.unhealthy_until {
            if now < until {
                self.health = DeviceHealth::Unhealthy;
                self.reason = self.unhealthy_reason.clone();
                return self.health;
            }
            self.unhealthy_until = None;
            self.unhealthy_reason = None;
        }

        let mut degraded: Vec<String> = Vec::new();
        if let Some(since) = self.throttled_since {
            if now.duration_since(since) >= THROTTLE_DEGRADED_AFTER {
                degraded.push(format!(
                    "throttled ({}) for {} s",
                    s.throttle_reasons.as_deref().unwrap_or_default().join(","),
                    now.duration_since(since).as_secs()
                ));
            }
        }
        let limit = temperature_limit_c(self.vendor);
        if let Some(t) = s.temperature_c {
            if t > limit {
                degraded.push(format!("temperature {t:.0} C above {limit:.0} C"));
            }
        }
        if let Some(n) = s.error_count {
            if n > 0 {
                degraded.push(format!("{n} device errors in the last interval"));
            }
        }
        if degraded.is_empty() {
            self.health = DeviceHealth::Healthy;
            self.reason = None;
        } else {
            self.health = DeviceHealth::Degraded;
            self.reason = Some(degraded.join("; "));
        }
        self.health
    }

    /// Stamp the current verdict on an inventory device. A tracker that has seen no sample yet
    /// leaves the plugin's own verdict in place.
    pub fn apply(&self, d: &mut Device) {
        if self.health != DeviceHealth::Unknown {
            d.health = self.health;
            d.health_reason = self.reason.clone();
        }
    }
}

/// Per-device trackers keyed by `Device::key()` / `DeviceSample::key()`.
#[derive(Debug, Default)]
pub struct HealthBook {
    trackers: std::sync::Mutex<std::collections::HashMap<String, HealthTracker>>,
}

impl HealthBook {
    pub fn observe(&self, samples: &[DeviceSample]) {
        let mut map = self.trackers.lock().unwrap_or_else(|e| e.into_inner());
        for s in samples {
            map.entry(s.key())
                .or_insert_with(|| HealthTracker::new(s.vendor))
                .observe(s);
        }
    }

    pub fn mark_fatal(&self, key: &str, vendor: Vendor, reason: impl Into<String>) {
        let mut map = self.trackers.lock().unwrap_or_else(|e| e.into_inner());
        map.entry(key.to_string())
            .or_insert_with(|| HealthTracker::new(vendor))
            .mark_fatal(reason);
    }

    pub fn apply(&self, devices: &mut [Device]) {
        let map = self.trackers.lock().unwrap_or_else(|e| e.into_inner());
        for d in devices {
            if let Some(t) = map.get(&d.key()) {
                t.apply(d);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample() -> DeviceSample {
        DeviceSample::new(Vendor::Nvidia, 0, Some("GPU-1".into()))
    }

    #[test]
    fn healthy_then_ecc_fatal_is_sticky() {
        let mut t = HealthTracker::new(Vendor::Nvidia);
        let now = Instant::now();
        let mut s = sample();
        s.ecc_errors_total = Some(0);
        assert_eq!(t.observe_at(&s, now), DeviceHealth::Healthy);
        s.ecc_errors_total = Some(2);
        assert_eq!(
            t.observe_at(&s, now + Duration::from_secs(2)),
            DeviceHealth::Unhealthy
        );
        assert!(t.reason().unwrap().contains("ECC"));
        // Still unhealthy shortly after, even though ECC stopped growing.
        assert_eq!(
            t.observe_at(&s, now + Duration::from_secs(60)),
            DeviceHealth::Unhealthy
        );
        // Cleared after the hold period.
        assert_eq!(
            t.observe_at(&s, now + UNHEALTHY_HOLD + Duration::from_secs(5)),
            DeviceHealth::Healthy
        );
    }

    #[test]
    fn throttle_needs_sixty_seconds() {
        let mut t = HealthTracker::new(Vendor::Nvidia);
        let now = Instant::now();
        let mut s = sample();
        s.throttle_reasons = Some(vec!["sw_thermal".into()]);
        assert_eq!(t.observe_at(&s, now), DeviceHealth::Healthy);
        assert_eq!(
            t.observe_at(&s, now + Duration::from_secs(61)),
            DeviceHealth::Degraded
        );
        // Power cap is not a health problem.
        s.throttle_reasons = Some(vec!["power_cap".into()]);
        assert_eq!(
            t.observe_at(&s, now + Duration::from_secs(63)),
            DeviceHealth::Healthy
        );
    }

    #[test]
    fn hot_device_is_degraded_and_apply_stamps() {
        let mut t = HealthTracker::new(Vendor::Amd);
        let mut s = sample();
        s.vendor = Vendor::Amd;
        s.temperature_c = Some(96.0);
        assert_eq!(t.observe(&s), DeviceHealth::Degraded);
        let mut d = Device::new(Vendor::Amd, 0, "MI300X", 1);
        t.apply(&mut d);
        assert_eq!(d.health, DeviceHealth::Degraded);
        assert!(d.health_reason.unwrap().contains("96"));
    }

    #[test]
    fn book_tracks_by_key() {
        let book = HealthBook::default();
        let mut s = sample();
        s.ecc_errors_total = Some(1);
        book.observe(std::slice::from_ref(&s));
        book.mark_fatal("GPU-1", Vendor::Nvidia, "Xid 79");
        let mut devs = vec![Device {
            uuid: Some("GPU-1".into()),
            ..Device::new(Vendor::Nvidia, 0, "x", 1)
        }];
        book.apply(&mut devs);
        assert_eq!(devs[0].health, DeviceHealth::Unhealthy);
        assert_eq!(devs[0].health_reason.as_deref(), Some("Xid 79"));
    }
}
