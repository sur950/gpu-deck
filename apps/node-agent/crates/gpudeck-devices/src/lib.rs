//! GPU Deck node agent — device plugins.
//!
//! One plugin per accelerator family, each producing the common [`Device`] (inventory) and
//! [`DeviceSample`] (2 s metrics) types from `libs/contracts/agent-protocol/common.json` and
//! `libs/contracts/metrics.md` §1.
//!
//! | Vendor | Backend | Fallback | Status on this Mac |
//! | --- | --- | --- | --- |
//! | NVIDIA | NVML via `nvml-wrapper` (dlopen, binary runs without the library) | `nvidia-smi --query-gpu` CSV | fixture-only |
//! | AMD | `amd-smi static/metric/list --json` | `rocm-smi -a --json` | fixture-only |
//! | Hygon DCU | `hy-smi --json` (rocm-smi fork) | `hy-smi` text table | fixture-only, unverified format |
//! | MetaX | `mx-smi` summary table | `mx-smi -q` | fixture-only, unverified format |
//! | Moore Threads | `mthreads-gmi -q` | `mthreads-gmi` table | fixture-only, unverified format |
//! | T-Head PPU | `ppu-smi -q` (name overridable) | table | fixture-only, unverified format |
//! | Apple Silicon | IOKit (`IOAccelerator` PerformanceStatistics, `AGXAccelerator`), Metal, IOReport, SMC, sysctl | `system_profiler` | real |
//!
//! `detect_plugins()` returns the plugins whose tooling is present on this node, in a fixed order.

pub mod amd;
pub mod apple;
pub mod health;
pub mod hygon;
pub mod metax;
pub mod moorethreads;
pub mod nvidia;
pub mod slicing;
pub mod thead;
pub mod types;
pub mod util;

pub use health::{HealthBook, HealthTracker};
pub use slicing::{slice_env, slice_support, AssignedDevice, SliceMode, SliceSupport};
pub use types::{Device, DeviceHealth, DevicePlugin, DeviceSample, Slice, Vendor};

/// Instantiate every plugin whose backend is usable on this node (library loadable or SMI tool on
/// `PATH`/vendor dirs). Order is stable: NVIDIA, AMD, Hygon, MetaX, Moore Threads, T-Head, Apple.
/// Set `GPUDECK_DEVICE_PLUGINS=nvidia,apple` to force a subset (useful for tests).
pub fn detect_plugins() -> Vec<Box<dyn DevicePlugin>> {
    let forced: Option<Vec<Vendor>> = std::env::var("GPUDECK_DEVICE_PLUGINS").ok().map(|s| {
        s.split(',')
            .filter_map(|v| v.trim().parse::<Vendor>().ok())
            .collect()
    });
    let wanted = |v: Vendor| forced.as_ref().is_none_or(|f| f.contains(&v));

    let mut plugins: Vec<Box<dyn DevicePlugin>> = Vec::new();
    if wanted(Vendor::Nvidia) {
        if let Some(p) = nvidia::NvidiaPlugin::detect_backend() {
            plugins.push(Box::new(p));
        }
    }
    if wanted(Vendor::Amd) {
        if let Some(p) = amd::AmdPlugin::detect_backend() {
            plugins.push(Box::new(p));
        }
    }
    if wanted(Vendor::Hygon) {
        if let Some(p) = hygon::HygonPlugin::detect_backend() {
            plugins.push(Box::new(p));
        }
    }
    if wanted(Vendor::Metax) {
        if let Some(p) = metax::MetaxPlugin::detect_backend() {
            plugins.push(Box::new(p));
        }
    }
    if wanted(Vendor::Moorethreads) {
        if let Some(p) = moorethreads::MooreThreadsPlugin::detect_backend() {
            plugins.push(Box::new(p));
        }
    }
    if wanted(Vendor::Thead) {
        if let Some(p) = thead::TheadPlugin::detect_backend() {
            plugins.push(Box::new(p));
        }
    }
    if wanted(Vendor::Apple) {
        if let Some(p) = apple::ApplePlugin::detect_backend() {
            plugins.push(Box::new(p));
        }
    }
    tracing::debug!(
        plugins = ?plugins.iter().map(|p| p.backend()).collect::<Vec<_>>(),
        "device plugins detected"
    );
    plugins
}

/// Run `detect` on every plugin and flatten the result; a failing plugin is logged and skipped.
pub async fn detect_all(plugins: &[Box<dyn DevicePlugin>]) -> Vec<Device> {
    let mut out = Vec::new();
    for p in plugins {
        match p.detect().await {
            Ok(devs) => out.extend(devs),
            Err(e) => tracing::warn!(vendor = %p.vendor(), error = %e, "device detect failed"),
        }
    }
    out
}

/// Run `sample` on every plugin and flatten the result; a failing plugin is logged and skipped.
pub async fn sample_all(plugins: &[Box<dyn DevicePlugin>]) -> Vec<DeviceSample> {
    let mut out = Vec::new();
    for p in plugins {
        match p.sample().await {
            Ok(s) => out.extend(s),
            Err(e) => tracing::warn!(vendor = %p.vendor(), error = %e, "device sample failed"),
        }
    }
    out
}
