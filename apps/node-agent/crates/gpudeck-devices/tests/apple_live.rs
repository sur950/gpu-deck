//! Real Apple Silicon detection and sampling (runs on macOS/aarch64 only).
#![cfg(all(target_os = "macos", target_arch = "aarch64"))]

use gpudeck_devices::apple::ApplePlugin;
use gpudeck_devices::{DeviceHealth, DevicePlugin, Vendor};

#[tokio::test]
async fn apple_detect_and_sample() {
    let plugin =
        ApplePlugin::detect_backend().expect("Apple GPU should be detected on Apple Silicon");
    assert_eq!(plugin.vendor(), Vendor::Apple);
    let devices = plugin.detect().await.unwrap();
    assert_eq!(devices.len(), 1);
    let d = &devices[0];
    println!("{}", serde_json::to_string_pretty(d).unwrap());
    assert!(d.is_unified_memory);
    assert!(d.vram_total_bytes > 1 << 30, "recommendedMaxWorkingSetSize");
    assert!(d.core_count.unwrap_or(0) > 0, "gpu-core-count");
    assert!(d.uuid.as_deref().unwrap_or("").starts_with("apple-"));
    assert!(d.name.starts_with("Apple"));
    assert_eq!(d.health, DeviceHealth::Healthy);
    let usable = d.extra["usable_memory_fraction"].as_f64().unwrap();
    assert!(usable > 0.3 && usable <= 1.0);

    let s1 = plugin.sample().await.unwrap();
    tokio::time::sleep(std::time::Duration::from_millis(600)).await;
    let s2 = plugin.sample().await.unwrap();
    println!("{}", serde_json::to_string_pretty(&s2).unwrap());
    assert_eq!(s1.len(), 1);
    let s = &s2[0];
    assert_eq!(s.uuid, d.uuid);
    assert!(
        s.utilization_pct.is_some(),
        "IOAccelerator Device Utilization %"
    );
    assert!(s.memory_used_bytes.is_some(), "In use system memory");
    assert_eq!(s.memory_total_bytes, Some(d.vram_total_bytes));
    // Power (IOReport) and temperature (SMC) are best effort: when present they must be sane.
    if let Some(p) = s.power_watts {
        assert!((0.0..500.0).contains(&p), "power {p}");
    }
    if let Some(t) = s.temperature_c {
        assert!((1.0..150.0).contains(&t), "temp {t}");
    }
    let wire = s.to_gpu_sample("6f1c2a3b-0000-4000-8000-000000000001");
    assert!(wire.get("vendor").is_none() && wire.get("device_id").is_some());
}
