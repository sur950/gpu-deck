//! How a `Slice { vram_bytes, compute_pct }` maps onto each vendor's hardware, and which
//! environment the engine process needs to be pinned to a set of devices.
//!
//! The control plane schedules slices abstractly; the agent (via `slice_env`) turns a device
//! assignment into vendor environment variables, and the engine adapters add memory-cap flags
//! (`--gpu-memory-utilization`, `-ngl`, ...) from the same `Slice`.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

use crate::types::{Slice, Vendor};

/// Isolation mechanism behind a slice.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SliceMode {
    /// Hardware partitions (NVIDIA MIG). Memory and compute are isolated; a slice is a MIG profile.
    HardwarePartition,
    /// Shared device with a compute share enforced by a daemon (NVIDIA MPS active-thread percentage).
    ComputeShare,
    /// Compute-unit masking (AMD `HSA_CU_MASK`); memory is not isolated.
    ComputeMask,
    /// No hardware isolation: the engine is launched with a memory cap and the agent accounts the
    /// share (Apple unified memory, engines without partitioning).
    MemoryCap,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SliceSupport {
    pub vendor: Vendor,
    pub mode: SliceMode,
    /// True if two instances with different slices on the same device are memory-isolated.
    pub memory_isolated: bool,
    /// True if `compute_pct` is enforced by the driver (not only accounted).
    pub compute_enforced: bool,
    /// Smallest VRAM step the scheduler should use (bytes); `None` = any.
    pub vram_granularity_bytes: Option<u64>,
    /// Smallest compute step (%); `None` = any.
    pub compute_granularity_pct: Option<u8>,
    /// Environment variables used to select devices (documentation for operators/UI).
    pub visible_devices_env: Vec<&'static str>,
    pub notes: &'static str,
}

/// Slice semantics per vendor.
pub fn slice_support(vendor: Vendor) -> SliceSupport {
    match vendor {
        Vendor::Nvidia => SliceSupport {
            vendor,
            mode: SliceMode::HardwarePartition,
            memory_isolated: true,
            compute_enforced: true,
            vram_granularity_bytes: None,
            compute_granularity_pct: Some(1),
            visible_devices_env: vec!["CUDA_VISIBLE_DEVICES", "NVIDIA_VISIBLE_DEVICES"],
            notes: "MIG (A100/H100/B200 class): a slice is a MIG instance; the agent passes its \
                    MIG UUID (MIG-GPU-...) in CUDA_VISIBLE_DEVICES; vram_bytes/compute_pct are the \
                    profile's values (1g.10gb = 1/7 compute). Without MIG: MPS compute share via \
                    CUDA_MPS_ACTIVE_THREAD_PERCENTAGE=<compute_pct> and memory cap via \
                    CUDA_MPS_PINNED_DEVICE_MEM_LIMIT=0=<vram_bytes> (requires nvidia-cuda-mps-control \
                    -d on the node); otherwise memory_cap mode (engine flags) with time-slicing.",
        },
        Vendor::Amd | Vendor::Hygon => SliceSupport {
            vendor,
            mode: SliceMode::ComputeMask,
            memory_isolated: false,
            compute_enforced: true,
            vram_granularity_bytes: None,
            compute_granularity_pct: Some(1),
            visible_devices_env: vec!["HIP_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES"],
            notes: "CU masking: HSA_CU_MASK=<gpu>:<mask> limits the compute units an engine can \
                    schedule on (compute_pct → fraction of CUs, rounded to whole CUs; the device \
                    core_count is the CU count). Memory is a soft cap (engine flags such as \
                    --gpu-memory-utilization). MI300X compute/memory partitions (CPX/NPS4) appear as \
                    separate devices and need no slice. Hygon DCU uses the same ROCm/DTK semantics.",
        },
        Vendor::Metax => SliceSupport {
            vendor,
            mode: SliceMode::MemoryCap,
            memory_isolated: false,
            compute_enforced: false,
            vram_granularity_bytes: None,
            compute_granularity_pct: None,
            visible_devices_env: vec!["MACA_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES"],
            notes: "MetaX MACA exposes no partitioning API to user space; slices are memory caps \
                    enforced by engine flags and accounted by the agent. (unverified: vendor SDK)",
        },
        Vendor::Moorethreads => SliceSupport {
            vendor,
            mode: SliceMode::MemoryCap,
            memory_isolated: false,
            compute_enforced: false,
            vram_granularity_bytes: None,
            compute_granularity_pct: None,
            visible_devices_env: vec!["MUSA_VISIBLE_DEVICES"],
            notes: "Moore Threads MUSA: no MIG-like partitioning; memory cap through engine flags. \
                    (unverified: vendor SDK)",
        },
        Vendor::Thead => SliceSupport {
            vendor,
            mode: SliceMode::MemoryCap,
            memory_isolated: false,
            compute_enforced: false,
            vram_granularity_bytes: None,
            compute_granularity_pct: None,
            visible_devices_env: vec!["PPU_VISIBLE_DEVICES"],
            notes: "T-Head PPU: memory cap through engine flags; device selection env name is \
                    unverified until hardware is available.",
        },
        Vendor::Apple => SliceSupport {
            vendor,
            mode: SliceMode::MemoryCap,
            memory_isolated: false,
            compute_enforced: false,
            vram_granularity_bytes: Some(256 * 1024 * 1024),
            compute_granularity_pct: None,
            visible_devices_env: vec![],
            notes: "Unified memory, one GPU per Mac. vram_total_bytes is Metal's \
                    recommendedMaxWorkingSetSize (the wired limit, ~75-80 % of RAM). A slice is a \
                    memory budget: llama.cpp honours it through -ngl / -c / --cache-type-k/v and the \
                    agent refuses loads whose weights + KV estimate exceed the free budget; mlx_lm has \
                    no cap flag, the agent sizes context_size and accounts RSS. compute_pct is \
                    informational (Metal time-slices).",
        },
    }
}

/// One device assignment as the engine launcher sees it (index + optional slice + optional UUID).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AssignedDevice {
    pub vendor: Vendor,
    pub index: u32,
    /// Vendor UUID; for NVIDIA MIG slices this is the `MIG-...` UUID that must be used instead of
    /// the parent index.
    #[serde(default)]
    pub uuid: Option<String>,
    #[serde(default)]
    pub slice: Option<Slice>,
}

/// Environment the engine process needs to see exactly `devices` (all of one vendor), plus the
/// MPS/CU-mask variables that enforce a compute slice where the vendor supports it.
///
/// `core_count` is the device's CU/SM count when known (AMD CU masks need it).
pub fn slice_env(devices: &[AssignedDevice], core_count: Option<u32>) -> BTreeMap<String, String> {
    let mut env = BTreeMap::new();
    let Some(first) = devices.first() else {
        return env;
    };
    let vendor = first.vendor;
    let ids: Vec<String> = devices
        .iter()
        .map(|d| match (&d.uuid, &d.slice) {
            // A MIG slice must be addressed by its MIG UUID, never by the parent index.
            (Some(u), Some(_)) if vendor == Vendor::Nvidia && u.starts_with("MIG-") => u.clone(),
            _ => d.index.to_string(),
        })
        .collect();
    let joined = ids.join(",");
    match vendor {
        Vendor::Nvidia => {
            env.insert("CUDA_VISIBLE_DEVICES".into(), joined.clone());
            env.insert("NVIDIA_VISIBLE_DEVICES".into(), joined);
            // MPS enforcement for non-MIG slices (only meaningful with a single device).
            if devices.len() == 1 {
                if let Some(slice) = first.slice {
                    let is_mig = first.uuid.as_deref().is_some_and(|u| u.starts_with("MIG-"));
                    if !is_mig {
                        env.insert(
                            "CUDA_MPS_ACTIVE_THREAD_PERCENTAGE".into(),
                            slice.compute_pct.to_string(),
                        );
                        // Device ordinals here are as seen by the client (after
                        // CUDA_VISIBLE_DEVICES remapping), so the single device is always 0.
                        env.insert(
                            "CUDA_MPS_PINNED_DEVICE_MEM_LIMIT".into(),
                            format!("0={}", slice.vram_bytes),
                        );
                    }
                }
            }
        }
        Vendor::Amd | Vendor::Hygon => {
            env.insert("HIP_VISIBLE_DEVICES".into(), joined.clone());
            env.insert("ROCR_VISIBLE_DEVICES".into(), joined);
            if devices.len() == 1 {
                if let (Some(slice), Some(cus)) = (first.slice, core_count) {
                    if slice.compute_pct < 100 && cus > 0 {
                        let n =
                            ((cus as u64 * slice.compute_pct as u64).div_ceil(100)).max(1) as u32;
                        env.insert("HSA_CU_MASK".into(), format!("0:{}", cu_mask_hex(n)));
                    }
                }
            }
        }
        Vendor::Metax => {
            env.insert("MACA_VISIBLE_DEVICES".into(), joined.clone());
            env.insert("CUDA_VISIBLE_DEVICES".into(), joined);
        }
        Vendor::Moorethreads => {
            env.insert("MUSA_VISIBLE_DEVICES".into(), joined);
        }
        Vendor::Thead => {
            env.insert("PPU_VISIBLE_DEVICES".into(), joined);
        }
        Vendor::Apple => {}
    }
    env
}

/// Hex CU mask with the lowest `n` bits set (`HSA_CU_MASK` format, `0x` prefixed).
pub fn cu_mask_hex(n: u32) -> String {
    if n == 0 {
        return "0x0".to_string();
    }
    let full_words = (n / 64) as usize;
    let rem = n % 64;
    let mut s = String::from("0x");
    if rem > 0 {
        s.push_str(&format!("{:x}", (1u128 << rem) - 1));
    }
    for _ in 0..full_words {
        s.push_str("ffffffffffffffff");
    }
    s
}

/// Total VRAM an assignment claims on each device (whole device when `slice` is `None`).
pub fn claimed_vram(devices: &[AssignedDevice], device_total: impl Fn(u32) -> Option<u64>) -> u64 {
    devices
        .iter()
        .map(|d| {
            d.slice
                .map(|s| s.vram_bytes)
                .or_else(|| device_total(d.index))
                .unwrap_or(0)
        })
        .sum()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn nvidia_mig_uses_uuid() {
        let env = slice_env(
            &[AssignedDevice {
                vendor: Vendor::Nvidia,
                index: 0,
                uuid: Some("MIG-abc".into()),
                slice: Some(Slice {
                    vram_bytes: 10 << 30,
                    compute_pct: 14,
                }),
            }],
            None,
        );
        assert_eq!(env["CUDA_VISIBLE_DEVICES"], "MIG-abc");
        assert!(!env.contains_key("CUDA_MPS_ACTIVE_THREAD_PERCENTAGE"));
    }

    #[test]
    fn nvidia_mps_share() {
        let env = slice_env(
            &[AssignedDevice {
                vendor: Vendor::Nvidia,
                index: 1,
                uuid: Some("GPU-1".into()),
                slice: Some(Slice {
                    vram_bytes: 1 << 30,
                    compute_pct: 50,
                }),
            }],
            None,
        );
        assert_eq!(env["CUDA_VISIBLE_DEVICES"], "1");
        assert_eq!(env["CUDA_MPS_ACTIVE_THREAD_PERCENTAGE"], "50");
        assert_eq!(env["CUDA_MPS_PINNED_DEVICE_MEM_LIMIT"], "0=1073741824");
    }

    #[test]
    fn amd_cu_mask() {
        let env = slice_env(
            &[AssignedDevice {
                vendor: Vendor::Amd,
                index: 0,
                uuid: None,
                slice: Some(Slice {
                    vram_bytes: 1,
                    compute_pct: 50,
                }),
            }],
            Some(304),
        );
        assert_eq!(env["HIP_VISIBLE_DEVICES"], "0");
        assert_eq!(env["HSA_CU_MASK"], format!("0:{}", cu_mask_hex(152)));
        assert_eq!(cu_mask_hex(4), "0xf");
        assert_eq!(cu_mask_hex(64), "0xffffffffffffffff");
        assert_eq!(cu_mask_hex(65), "0x1ffffffffffffffff");
    }

    #[test]
    fn apple_has_no_env() {
        let env = slice_env(
            &[AssignedDevice {
                vendor: Vendor::Apple,
                index: 0,
                uuid: None,
                slice: None,
            }],
            None,
        );
        assert!(env.is_empty());
        assert_eq!(slice_support(Vendor::Apple).mode, SliceMode::MemoryCap);
    }
}
