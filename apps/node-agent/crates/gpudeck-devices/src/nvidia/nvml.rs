//! NVML backend (`nvml-wrapper`, library dlopen'ed at runtime). The `Nvml` handle is leaked into a
//! `'static` so per-device handles and the Xid event set can live in the plugin for the life of
//! the agent process.

use nvml_wrapper::enum_wrappers::device::{
    Clock, EccCounter, MemoryError, PcieUtilCounter, TemperatureSensor,
};
use nvml_wrapper::error::NvmlError;
use nvml_wrapper::Nvml;

use super::smi::{normalize_bus_id, throttle_names};
use crate::types::{
    process_name, tidy_processes, Device, DeviceHealth, DeviceSample, GpuProcess, Vendor,
};

use nvml_wrapper::sys_exports::field_id::NVML_FI_DEV_MEMORY_TEMP;

/// Xid codes that make a device unhealthy (`metrics.md` §5): 48 double-bit ECC, 63 page
/// retirement / row remap event, 79 fallen off the bus.
pub const FATAL_XIDS: &[u64] = &[48, 63, 79];

/// `(device key, reason)` pairs for fatal conditions seen during a call.
pub type Fatal = Vec<(String, String)>;

pub struct NvmlBackend {
    nvml: &'static Nvml,
    #[cfg(target_os = "linux")]
    events: std::sync::Mutex<Option<xid::XidEvents>>,
}

impl NvmlBackend {
    /// Load `libnvidia-ml.so.1` (driver package) or the unversioned name; `None` when absent.
    pub fn init() -> Option<Self> {
        let nvml = Nvml::builder()
            .lib_path(std::ffi::OsStr::new("libnvidia-ml.so.1"))
            .init()
            .or_else(|_| Nvml::init())
            .map_err(|e| tracing::debug!(error = %e, "NVML not available"))
            .ok()?;
        let count = nvml.device_count().unwrap_or(0);
        if count == 0 {
            return None;
        }
        let nvml: &'static Nvml = Box::leak(Box::new(nvml));
        Some(NvmlBackend {
            nvml,
            #[cfg(target_os = "linux")]
            events: std::sync::Mutex::new(xid::XidEvents::register(nvml)),
        })
    }

    pub fn detect(&self) -> anyhow::Result<(Vec<Device>, Fatal)> {
        let nvml = self.nvml;
        let driver = nvml.sys_driver_version().ok();
        let cuda = nvml
            .sys_cuda_driver_version()
            .ok()
            .map(|v| format!("{}.{}", v / 1000, (v % 1000) / 10));
        let mut out = Vec::new();
        let mut fatal = Vec::new();
        for i in 0..nvml.device_count()? {
            let dev = match nvml.device_by_index(i) {
                Ok(d) => d,
                Err(e) => {
                    let mut d = Device::new(Vendor::Nvidia, i, "NVIDIA GPU (unreachable)", 0);
                    d.health = DeviceHealth::Unhealthy;
                    d.health_reason = Some(format!("NVML: {e}"));
                    out.push(d);
                    continue;
                }
            };
            let total = dev.memory_info().map(|m| m.total).unwrap_or(0);
            let mut d = Device::new(
                Vendor::Nvidia,
                i,
                dev.name().unwrap_or_else(|_| "NVIDIA GPU".into()),
                total,
            );
            d.uuid = dev.uuid().ok();
            d.driver_version = driver.clone();
            d.runtime_version = cuda.clone();
            d.compute_capability = dev
                .cuda_compute_capability()
                .ok()
                .map(|c| format!("{}.{}", c.major, c.minor));
            d.pci_bus_id = dev.pci_info().ok().map(|p| normalize_bus_id(&p.bus_id));
            d.max_power_watts = dev
                .power_management_limit_constraints()
                .ok()
                .map(|c| c.max_limit as f64 / 1000.0)
                .or_else(|| dev.enforced_power_limit().ok().map(|mw| mw as f64 / 1000.0));
            d.core_count = dev.num_cores().ok();
            d.health = DeviceHealth::Healthy;
            if let Ok(true) = dev.are_pages_pending_retired() {
                d.health = DeviceHealth::Unhealthy;
                d.health_reason =
                    Some("retired pages pending (reboot / GPU reset required)".into());
                fatal.push((d.key(), d.health_reason.clone().unwrap_or_default()));
            }
            if let Ok(mig) = dev.mig_mode() {
                d.extra
                    .insert("mig_mode_current".into(), serde_json::json!(mig.current));
                d.extra
                    .insert("mig_mode_pending".into(), serde_json::json!(mig.pending));
                if mig.current == 1 {
                    let mut migs = Vec::new();
                    for m in 0..dev.mig_device_count().unwrap_or(0) {
                        if let Ok(md) = dev.mig_device_by_index(m) {
                            migs.push(serde_json::json!({
                                "uuid": md.uuid().ok(),
                                "memory_total_bytes": md.memory_info().ok().map(|x| x.total),
                            }));
                        }
                    }
                    d.extra
                        .insert("mig_devices".into(), serde_json::Value::Array(migs));
                }
            }
            d.labels.insert("backend".into(), "nvml".into());
            out.push(d);
        }
        Ok((out, fatal))
    }

    /// One sample per device plus `(device key, reason)` for fatal conditions seen this round.
    pub fn sample(&self) -> anyhow::Result<(Vec<DeviceSample>, Fatal)> {
        let nvml = self.nvml;
        let mut fatal: Fatal = Vec::new();
        #[cfg(target_os = "linux")]
        let xids = self
            .events
            .lock()
            .ok()
            .and_then(|mut g| g.as_mut().map(|e| e.drain()))
            .unwrap_or_default();
        #[cfg(not(target_os = "linux"))]
        let xids: std::collections::HashMap<String, Vec<u64>> = std::collections::HashMap::new();

        let mut out = Vec::new();
        for i in 0..nvml.device_count()? {
            let dev = match nvml.device_by_index(i) {
                Ok(d) => d,
                Err(e) => {
                    if matches!(e, NvmlError::GpuLost) {
                        fatal.push((
                            format!("nvidia:{i}"),
                            "GPU has fallen off the bus (NVML_ERROR_GPU_IS_LOST)".into(),
                        ));
                    }
                    continue;
                }
            };
            let uuid = dev.uuid().ok();
            let mut s = DeviceSample::new(Vendor::Nvidia, i, uuid.clone());
            match dev.utilization_rates() {
                Ok(u) => {
                    s.utilization_pct = Some(u.gpu as f64);
                    s.memory_util_pct = Some(u.memory as f64);
                }
                Err(NvmlError::GpuLost) => {
                    fatal.push((
                        s.key(),
                        "GPU has fallen off the bus (NVML_ERROR_GPU_IS_LOST)".into(),
                    ));
                    out.push(s);
                    continue;
                }
                Err(_) => {}
            }
            if let Ok(m) = dev.memory_info() {
                s.memory_used_bytes = Some(m.used);
                s.memory_total_bytes = Some(m.total);
            }
            s.power_watts = dev.power_usage().ok().map(|mw| mw as f64 / 1000.0);
            s.power_limit_watts = dev.enforced_power_limit().ok().map(|mw| mw as f64 / 1000.0);
            s.temperature_c = dev
                .temperature(TemperatureSensor::Gpu)
                .ok()
                .map(|t| t as f64);
            s.memory_temperature_c = memory_temperature(&dev);
            s.sm_clock_mhz = dev.clock_info(Clock::SM).ok();
            s.memory_clock_mhz = dev.clock_info(Clock::Memory).ok();
            s.fan_pct = dev.fan_speed(0).ok().map(|f| f as f64);
            s.encoder_pct = dev.encoder_utilization().ok().map(|u| u.utilization as f64);
            s.decoder_pct = dev.decoder_utilization().ok().map(|u| u.utilization as f64);
            s.pcie_tx_bytes_per_s = dev
                .pcie_throughput(PcieUtilCounter::Send)
                .ok()
                .map(|kb| kb as u64 * 1024);
            s.pcie_rx_bytes_per_s = dev
                .pcie_throughput(PcieUtilCounter::Receive)
                .ok()
                .map(|kb| kb as u64 * 1024);
            s.ecc_errors_total = dev
                .total_ecc_errors(MemoryError::Uncorrected, EccCounter::Aggregate)
                .ok();
            s.throttle_reasons = dev
                .current_throttle_reasons()
                .ok()
                .map(|r| throttle_names(r.bits()));
            s.process_count = dev.running_compute_processes_count().ok();
            s.processes = running_processes(&dev);
            let dev_xids = uuid
                .as_ref()
                .and_then(|u| xids.get(u))
                .cloned()
                .unwrap_or_default();
            s.error_count = Some(dev_xids.len() as u64);
            if let Some(x) = dev_xids.iter().find(|x| FATAL_XIDS.contains(x)) {
                fatal.push((s.key(), format!("Xid {x}")));
            }
            if !dev_xids.is_empty() {
                s.extra
                    .get_or_insert_with(serde_json::Map::new)
                    .insert("xids".into(), serde_json::json!(dev_xids));
            }
            out.push(s);
        }
        Ok((out, fatal))
    }
}

/// `nvmlDeviceGetComputeRunningProcesses`: pid + used memory, named from `/proc` (comm or the
/// basename of argv[0]; never the arguments). NVML only, no CUDA context is created.
fn running_processes(dev: &nvml_wrapper::Device<'_>) -> Option<Vec<GpuProcess>> {
    use nvml_wrapper::enums::device::UsedGpuMemory;
    let procs = dev.running_compute_processes().ok()?;
    Some(tidy_processes(
        procs
            .into_iter()
            .map(|p| GpuProcess {
                pid: p.pid,
                used_memory_bytes: match p.used_gpu_memory {
                    UsedGpuMemory::Used(b) => Some(b),
                    UsedGpuMemory::Unavailable => None,
                },
                name: process_name(p.pid),
            })
            .collect(),
    ))
}

fn memory_temperature(dev: &nvml_wrapper::Device<'_>) -> Option<f64> {
    use nvml_wrapper::enums::device::SampleValue;
    use nvml_wrapper::structs::device::FieldId;
    let vals = dev
        .field_values_for(&[FieldId(NVML_FI_DEV_MEMORY_TEMP)])
        .ok()?;
    let sample = vals.into_iter().next()?.ok()?;
    let v = match sample.value.ok()? {
        SampleValue::F64(v) => v,
        SampleValue::U32(v) => v as f64,
        SampleValue::U64(v) => v as f64,
        SampleValue::I64(v) => v as f64,
    };
    if v > 0.0 {
        Some(v)
    } else {
        None
    }
}

#[cfg(target_os = "linux")]
mod xid {
    //! Xid critical-error events (`nvmlDeviceRegisterEvents`), polled without blocking each sample.

    use std::collections::HashMap;

    use nvml_wrapper::bitmasks::event::EventTypes;
    use nvml_wrapper::enums::event::XidError;
    use nvml_wrapper::{EventSet, Nvml};

    pub struct XidEvents {
        set: EventSet<'static>,
    }

    // SAFETY: NVML handles are thread-safe (NVML API reference, "Thread safety"); the set is only
    // used behind the plugin's Mutex.
    unsafe impl Send for XidEvents {}

    impl XidEvents {
        pub fn register(nvml: &'static Nvml) -> Option<Self> {
            let mut set = nvml.create_event_set().ok()?;
            for i in 0..nvml.device_count().ok()? {
                let dev = nvml.device_by_index(i).ok()?;
                set = match dev.register_events(EventTypes::CRITICAL_XID_ERROR, set) {
                    Ok(s) => s,
                    Err(e) => {
                        tracing::debug!(error = %e.error, "Xid event registration not supported");
                        return None;
                    }
                };
            }
            Some(XidEvents { set })
        }

        /// Pending Xids per device UUID since the previous call.
        pub fn drain(&mut self) -> HashMap<String, Vec<u64>> {
            let mut out: HashMap<String, Vec<u64>> = HashMap::new();
            for _ in 0..1024 {
                match self.set.wait(0) {
                    Ok(ev) => {
                        let uuid = ev.device.uuid().unwrap_or_default();
                        let code = match ev.event_data {
                            Some(XidError::Value(v)) => v,
                            _ => 0,
                        };
                        out.entry(uuid).or_default().push(code);
                    }
                    // `Timeout` = no pending event; anything else ends this poll too.
                    Err(_) => break,
                }
            }
            out
        }
    }
}
