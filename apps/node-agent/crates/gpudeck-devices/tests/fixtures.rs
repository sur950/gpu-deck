//! Parser tests against the recorded vendor-tool outputs in `apps/node-agent/fixtures/devices/`,
//! plus end-to-end plugin runs against fake SMI executables that replay those fixtures.

use std::path::{Path, PathBuf};

use gpudeck_devices::nvidia::smi;
use gpudeck_devices::{amd, apple, hygon, metax, moorethreads, DeviceHealth, DevicePlugin, Vendor};

fn fixture_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../fixtures/devices")
}

fn read(rel: &str) -> String {
    std::fs::read_to_string(fixture_dir().join(rel)).unwrap_or_else(|e| panic!("{rel}: {e}"))
}

fn json(rel: &str) -> serde_json::Value {
    serde_json::from_str(&read(rel)).unwrap()
}

/// Write an executable shell script `name` that runs `body` (POSIX sh, `$FX` = fixture dir).
fn fake_tool(dir: &Path, name: &str, body: &str) -> PathBuf {
    use std::os::unix::fs::PermissionsExt;
    let p = dir.join(name);
    let script = format!("#!/bin/sh\nFX='{}'\n{}\n", fixture_dir().display(), body);
    std::fs::write(&p, script).unwrap();
    std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o755)).unwrap();
    p
}

#[test]
fn nvidia_smi_full_query() {
    let rows = smi::parse_csv(&read("nvidia/nvidia-smi_query.csv"), smi::QUERY_FIELDS);
    assert_eq!(rows.len(), 2);
    let d = smi::row_to_device(Vendor::Nvidia, &rows[0], Some("12.4")).unwrap();
    assert_eq!(d.name, "NVIDIA H100 80GB HBM3");
    assert_eq!(
        d.uuid.as_deref(),
        Some("GPU-5f2c1c9e-8b4a-4b8e-9d1f-1a2b3c4d5e6f")
    );
    assert_eq!(d.vram_total_bytes, 81559 << 20);
    assert_eq!(d.compute_capability.as_deref(), Some("9.0"));
    assert_eq!(d.pci_bus_id.as_deref(), Some("0000:18:00.0"));
    assert_eq!(d.max_power_watts, Some(700.0));
    assert_eq!(d.runtime_version.as_deref(), Some("12.4"));
    let s = smi::row_to_sample(Vendor::Nvidia, &rows[1]).unwrap();
    assert_eq!(s.index, 1);
    assert_eq!(s.utilization_pct, Some(98.0));
    assert_eq!(s.memory_used_bytes, Some(65012 << 20));
    assert_eq!(s.power_watts, Some(690.12));
    assert_eq!(s.memory_temperature_c, Some(79.0));
    assert_eq!(s.fan_pct, None, "[N/A] must be null, never 0");
    assert_eq!(s.ecc_errors_total, Some(2));
    assert_eq!(
        s.throttle_reasons,
        Some(vec!["power_cap".to_string(), "sw_thermal".to_string()])
    );
    assert_eq!(
        smi::parse_cuda_version(&read("nvidia/nvidia-smi_banner.txt")).as_deref(),
        Some("12.4")
    );
}

#[test]
fn nvidia_smi_minimal_query() {
    let rows = smi::parse_csv(
        &read("nvidia/nvidia-smi_query_minimal.csv"),
        smi::MINIMAL_FIELDS,
    );
    assert_eq!(rows.len(), 1);
    let d = smi::row_to_device(Vendor::Nvidia, &rows[0], None).unwrap();
    assert_eq!(d.name, "Tesla T4");
    assert_eq!(d.compute_capability, None);
    let s = smi::row_to_sample(Vendor::Nvidia, &rows[0]).unwrap();
    assert_eq!(s.throttle_reasons, None);
    assert_eq!(s.power_watts, Some(9.39));
    // Wrong column count → skipped, not mis-parsed.
    assert!(smi::parse_csv(
        &read("nvidia/nvidia-smi_query_minimal.csv"),
        smi::QUERY_FIELDS
    )
    .is_empty());
}

#[tokio::test]
async fn nvidia_plugin_via_fake_smi_with_field_fallback() {
    let dir = tempfile::tempdir().unwrap();
    let tool = fake_tool(
        dir.path(),
        "nvidia-smi",
        r#"case "$1" in
  "") cat "$FX/nvidia/nvidia-smi_banner.txt" ;;
  *compute_cap*) echo 'Field "compute_cap" is not a valid field to query.' >&2; exit 2 ;;
  --query-gpu=*) cat "$FX/nvidia/nvidia-smi_query_minimal.csv" ;;
esac"#,
    );
    let p = gpudeck_devices::nvidia::NvidiaPlugin::with_smi(tool);
    assert_eq!(p.backend(), "nvidia-smi");
    let devs = p.detect().await.unwrap();
    assert_eq!(devs.len(), 1);
    assert_eq!(devs[0].runtime_version.as_deref(), Some("12.4"));
    let s = p.sample().await.unwrap();
    assert_eq!(s[0].temperature_c, Some(33.0));
    // After one healthy sample the inventory reports healthy.
    assert_eq!(p.detect().await.unwrap()[0].health, DeviceHealth::Healthy);
}

#[tokio::test]
async fn nvidia_ecc_growth_marks_unhealthy() {
    let dir = tempfile::tempdir().unwrap();
    let state = dir.path().join("n");
    let tool = fake_tool(
        dir.path(),
        "nvidia-smi",
        &format!(
            r#"case "$1" in
  "") cat "$FX/nvidia/nvidia-smi_banner.txt" ;;
  *) if [ -f '{s}' ]; then sed 's/, 2, 0x0000000000000024/, 5, 0x0000000000000024/' "$FX/nvidia/nvidia-smi_query.csv"; else touch '{s}'; cat "$FX/nvidia/nvidia-smi_query.csv"; fi ;;
esac"#,
            s = state.display()
        ),
    );
    let p = gpudeck_devices::nvidia::NvidiaPlugin::with_smi(tool);
    p.sample().await.unwrap();
    p.sample().await.unwrap();
    let devs = p.detect().await.unwrap();
    assert_eq!(devs[0].health, DeviceHealth::Healthy);
    assert_eq!(devs[1].health, DeviceHealth::Unhealthy);
    assert!(devs[1].health_reason.as_deref().unwrap().contains("ECC"));
}

#[test]
fn amd_smi_static_and_metric() {
    let devs = amd::parse_amd_smi_static(
        &json("amd/amd-smi_static.json"),
        &json("amd/amd-smi_list.json"),
        &json("amd/amd-smi_version.json"),
    );
    assert_eq!(devs.len(), 2);
    let d = &devs[0];
    assert_eq!(d.name, "AMD Instinct MI300X");
    assert_eq!(
        d.uuid.as_deref(),
        Some("1fff74a1-0000-1000-80ea-8d1b4a8a2f3b")
    );
    assert_eq!(d.vram_total_bytes, 196592 << 20);
    assert_eq!(d.compute_capability.as_deref(), Some("gfx942"));
    assert_eq!(d.core_count, Some(304));
    assert_eq!(d.runtime_version.as_deref(), Some("6.2.0"));
    assert_eq!(d.driver_version.as_deref(), Some("6.7.0"));
    assert_eq!(d.pci_bus_id.as_deref(), Some("0000:0c:00.0"));
    assert_eq!(d.max_power_watts, Some(750.0));
    assert_eq!(d.numa_node, Some(0));
    assert_eq!(d.extra["compute_partition"], "SPX");

    let mut s = amd::parse_amd_smi_metric(
        &json("amd/amd-smi_metric.json"),
        &json("amd/amd-smi_list.json"),
    );
    amd::apply_process_counts(&mut s, &json("amd/amd-smi_process.json"));
    assert_eq!(s.len(), 2);
    assert_eq!(s[0].utilization_pct, Some(87.0));
    assert_eq!(s[0].memory_util_pct, Some(41.0));
    assert_eq!(s[0].memory_used_bytes, Some(152331 << 20));
    assert_eq!(s[0].power_watts, Some(612.0));
    assert_eq!(
        s[0].temperature_c,
        Some(71.0),
        "edge N/A on MI300X → hotspot"
    );
    assert_eq!(s[0].memory_temperature_c, Some(58.0));
    assert_eq!(s[0].sm_clock_mhz, Some(2100));
    assert_eq!(s[0].memory_clock_mhz, Some(1300));
    assert_eq!(s[0].fan_pct, None);
    assert_eq!(s[0].encoder_pct, None);
    assert_eq!(s[0].throttle_reasons, Some(vec![]));
    assert_eq!(s[0].process_count, Some(1));
    assert_eq!(s[1].process_count, Some(0));
    assert_eq!(s[1].ecc_errors_total, Some(1));
    assert_eq!(s[1].throttle_reasons, Some(vec!["thermal".to_string()]));
}

#[test]
fn amd_smi_gpu_data_shape() {
    let s = amd::parse_amd_smi_metric(
        &json("amd/amd-smi_metric_gpu_data.json"),
        &serde_json::Value::Null,
    );
    assert_eq!(s.len(), 1);
    assert_eq!(s[0].temperature_c, Some(44.0));
    assert!((s[0].fan_pct.unwrap() - 25.098).abs() < 0.01);
    assert_eq!(s[0].encoder_pct, Some(0.0));
    assert_eq!(s[0].throttle_reasons, None);
}

#[test]
fn rocm_smi_fallback() {
    let (d, s) = amd::parse_rocm_smi(&json("amd/rocm-smi.json"), Vendor::Amd);
    assert_eq!(d.len(), 1);
    assert_eq!(d[0].name, "AMD Instinct MI210");
    assert_eq!(d[0].vram_total_bytes, 68702699520);
    assert_eq!(d[0].compute_capability.as_deref(), Some("gfx90a"));
    assert_eq!(d[0].driver_version.as_deref(), Some("6.2.4"));
    assert_eq!(d[0].max_power_watts, Some(300.0));
    assert_eq!(s[0].utilization_pct, Some(63.0));
    assert_eq!(s[0].memory_used_bytes, Some(33285996544));
    assert_eq!(s[0].power_watts, Some(211.0));
    assert_eq!(s[0].temperature_c, Some(52.0));
    assert_eq!(s[0].sm_clock_mhz, Some(1700));
    assert_eq!(s[0].memory_clock_mhz, Some(1600));
}

#[tokio::test]
async fn amd_plugin_via_fake_amd_smi() {
    let dir = tempfile::tempdir().unwrap();
    let tool = fake_tool(
        dir.path(),
        "amd-smi",
        r#"echo "WARNING: User is missing the following required groups: render" 
case "$1" in
  static) cat "$FX/amd/amd-smi_static.json" ;;
  metric) cat "$FX/amd/amd-smi_metric.json" ;;
  list) cat "$FX/amd/amd-smi_list.json" ;;
  version) cat "$FX/amd/amd-smi_version.json" ;;
  process) cat "$FX/amd/amd-smi_process.json" ;;
esac"#,
    );
    let p = amd::AmdPlugin::with_amd_smi(tool);
    let devs = p.detect().await.unwrap();
    assert_eq!(devs.len(), 2);
    let s = p.sample().await.unwrap();
    assert_eq!(s[0].uuid, devs[0].uuid);
    let devs = p.detect().await.unwrap();
    assert_eq!(devs[0].health, DeviceHealth::Healthy);
    // GPU 1 at 97 C hotspot is above the AMD limit.
    assert_eq!(devs[1].health, DeviceHealth::Degraded);
}

#[test]
fn hygon_json_and_text() {
    let (d, s) = amd::parse_rocm_smi(&json("hygon/hy-smi.json"), Vendor::Hygon);
    assert_eq!(d[0].vendor, Vendor::Hygon);
    assert_eq!(d[0].name, "Hygon DCU K100_AI");
    assert_eq!(d[0].labels["backend"], "hy-smi");
    assert_eq!(s[0].utilization_pct, Some(35.0));
    assert_eq!(s[0].memory_util_pct, Some(62.0));
    assert_eq!(s[0].power_watts, Some(156.0));

    let (d, s) = hygon::parse_text(&read("hygon/hy-smi.txt"), &read("hygon/hy-smi_meminfo.txt"));
    assert_eq!(d.len(), 2);
    assert_eq!(d[0].vram_total_bytes, 68702699520);
    assert_eq!(d[1].max_power_watts, Some(400.0));
    assert_eq!(s[0].utilization_pct, Some(35.0));
    assert_eq!(s[0].temperature_c, Some(49.0));
    assert_eq!(s[1].memory_used_bytes, Some(8388608));
}

#[tokio::test]
async fn hygon_plugin_falls_back_to_text() {
    let dir = tempfile::tempdir().unwrap();
    let tool = fake_tool(
        dir.path(),
        "hy-smi",
        r#"case "$*" in
  *--json*) echo "unrecognized arguments: --json" >&2; exit 2 ;;
  "--showmeminfo vram") cat "$FX/hygon/hy-smi_meminfo.txt" ;;
  "") cat "$FX/hygon/hy-smi.txt" ;;
esac"#,
    );
    let p = hygon::HygonPlugin::with_tool(tool);
    assert_eq!(p.detect().await.unwrap().len(), 2);
    assert_eq!(p.sample().await.unwrap()[0].power_watts, Some(156.0));
}

#[test]
fn metax_table() {
    let (d, s) = metax::parse(&read("metax/mx-smi.txt"));
    assert_eq!(d.len(), 2);
    assert_eq!(d[0].name, "MetaX C500");
    assert_eq!(d[0].vram_total_bytes, 65536 << 20);
    assert_eq!(d[0].driver_version.as_deref(), Some("2.9.8"));
    assert_eq!(d[0].runtime_version.as_deref(), Some("2.29.0.18"));
    assert_eq!(d[1].pci_bus_id.as_deref(), Some("0000:0f:00.0"));
    assert_eq!(d[1].max_power_watts, Some(350.0));
    assert_eq!(s[1].utilization_pct, Some(76.0));
    assert_eq!(s[1].temperature_c, Some(61.0));
    assert_eq!(s[1].power_watts, Some(287.0));
    assert_eq!(s[1].memory_used_bytes, Some(50122 << 20));
}

#[test]
fn moorethreads_query() {
    let (d, s) = moorethreads::parse_query(&read("moorethreads/mthreads-gmi-q.txt"));
    assert_eq!(d.len(), 2);
    assert_eq!(d[0].name, "MTT S4000");
    assert_eq!(
        d[0].uuid.as_deref(),
        Some("7a1b2c3d-4e5f-6071-8293-a4b5c6d7e8f9")
    );
    assert_eq!(d[0].vram_total_bytes, 49152 << 20);
    assert_eq!(d[0].driver_version.as_deref(), Some("2.7.0"));
    assert_eq!(d[0].runtime_version.as_deref(), Some("3.1.0"));
    assert_eq!(d[0].pci_bus_id.as_deref(), Some("0000:3b:00.0"));
    assert_eq!(d[0].core_count, Some(8192));
    assert_eq!(d[0].max_power_watts, Some(450.0));
    assert_eq!(d[1].index, 1);
    assert_eq!(s[0].utilization_pct, Some(3.0));
    assert_eq!(s[0].memory_util_pct, Some(2.0));
    assert_eq!(s[0].power_watts, Some(47.66));
    assert_eq!(s[0].sm_clock_mhz, Some(1750));
    assert_eq!(s[0].fan_pct, Some(0.0));
    assert_eq!(s[1].fan_pct, None);
    assert_eq!(s[1].temperature_c, Some(71.0));
    assert_eq!(s[1].memory_used_bytes, Some(30020 << 20));
}

#[tokio::test]
async fn thead_plugin_via_fake_ppu_smi() {
    let dir = tempfile::tempdir().unwrap();
    let tool = fake_tool(
        dir.path(),
        "ppu-smi",
        r#"case "$1" in
  "") cat "$FX/thead/ppu-smi_banner.txt" ;;
  *) cat "$FX/thead/ppu-smi_query.csv" ;;
esac"#,
    );
    let p = gpudeck_devices::thead::TheadPlugin::with_tool(tool);
    let d = p.detect().await.unwrap();
    assert_eq!(d.len(), 1);
    assert_eq!(d[0].vendor, Vendor::Thead);
    assert_eq!(d[0].vram_total_bytes, 98304 << 20);
    assert_eq!(d[0].runtime_version.as_deref(), Some("1.4.0"));
    assert_eq!(d[0].compute_capability, None);
    let s = p.sample().await.unwrap();
    assert_eq!(s[0].utilization_pct, Some(15.0));
    assert_eq!(s[0].memory_temperature_c, None);
    assert_eq!(s[0].throttle_reasons, None);
}

#[tokio::test]
async fn metax_and_moorethreads_plugins_via_fake_tools() {
    let dir = tempfile::tempdir().unwrap();
    let mx = fake_tool(dir.path(), "mx-smi", r#"cat "$FX/metax/mx-smi.txt""#);
    let mt = fake_tool(
        dir.path(),
        "mthreads-gmi",
        r#"[ "$1" = "-q" ] && cat "$FX/moorethreads/mthreads-gmi-q.txt""#,
    );
    let p = metax::MetaxPlugin::with_tool(mx);
    assert_eq!(p.sample().await.unwrap().len(), 2);
    let p = moorethreads::MooreThreadsPlugin::with_tool(mt);
    assert_eq!(p.detect().await.unwrap().len(), 2);
}

#[test]
fn apple_text_fallbacks() {
    let (stats, cores, model) =
        apple::parse_ioreg_accelerator(&read("apple/ioreg_ioaccelerator.txt"));
    assert_eq!(cores, Some(10));
    assert_eq!(model.as_deref(), Some("Apple M5"));
    assert!(stats.device_utilization_pct.is_some());
    assert!(stats.in_use_system_memory.is_some());
    let (model, cores, fam) =
        apple::parse_system_profiler(&json("apple/system_profiler_displays.json")).unwrap();
    assert_eq!(
        (model.as_str(), cores, fam),
        ("Apple M5", Some(10), Some(4))
    );
}

#[tokio::test]
async fn failing_tool_is_an_error_not_a_panic() {
    let dir = tempfile::tempdir().unwrap();
    let tool = fake_tool(dir.path(), "amd-smi", "echo boom >&2; exit 3");
    let p = amd::AmdPlugin::with_amd_smi(tool);
    let err = p.sample().await.unwrap_err().to_string();
    assert!(err.contains("boom"), "{err}");
    let plugins: Vec<Box<dyn DevicePlugin>> = vec![Box::new(p)];
    assert!(gpudeck_devices::sample_all(&plugins).await.is_empty());
}

#[test]
fn every_device_fixture_is_listed_in_manifest() {
    let manifest = json("../manifest.json");
    let listed: Vec<String> = manifest["files"]
        .as_object()
        .unwrap()
        .keys()
        .cloned()
        .collect();
    for vendor in std::fs::read_dir(fixture_dir()).unwrap() {
        let vendor = vendor.unwrap();
        for f in std::fs::read_dir(vendor.path()).unwrap() {
            let name = format!(
                "devices/{}/{}",
                vendor.file_name().to_string_lossy(),
                f.unwrap().file_name().to_string_lossy()
            );
            assert!(
                listed.contains(&name),
                "{name} missing from fixtures/manifest.json"
            );
        }
    }
}
