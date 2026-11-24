//! Helpers shared by the CLI-backed plugins: locating vendor tools, running them with a timeout,
//! and lenient number parsing for SMI output (`"N/A"`, `"[Not Supported]"`, `"1234 MiB"`, ...).

use std::path::{Path, PathBuf};
use std::time::Duration;

use anyhow::{anyhow, Context};
use tokio::process::Command;

/// Default timeout for one SMI invocation.
pub const SMI_TIMEOUT: Duration = Duration::from_secs(8);

/// Extra directories searched after `PATH` (vendor SDKs install their SMI tools here).
pub const VENDOR_BIN_DIRS: &[&str] = &[
    "/usr/bin",
    "/usr/local/bin",
    "/opt/rocm/bin",
    "/opt/dtk/bin",
    "/opt/hyhal/bin",
    "/opt/maca/bin",
    "/opt/maca/tools/mx-smi",
    "/usr/local/musa/bin",
    "/opt/musa/bin",
    "/usr/local/ppu/bin",
    "/opt/ppu/bin",
    "/usr/sbin",
    "/sbin",
];

/// Find an executable on `PATH` or in the vendor directories. An env override
/// `GPUDECK_<TOOL>_PATH` (tool name upper-cased, `-` → `_`) wins when set.
pub fn which(tool: &str) -> Option<PathBuf> {
    let env_key = format!(
        "GPUDECK_{}_PATH",
        tool.to_ascii_uppercase().replace(['-', '.'], "_")
    );
    if let Ok(p) = std::env::var(&env_key) {
        let p = PathBuf::from(p);
        if is_executable(&p) {
            return Some(p);
        }
    }
    let mut dirs: Vec<PathBuf> = std::env::var_os("PATH")
        .map(|p| std::env::split_paths(&p).collect())
        .unwrap_or_default();
    dirs.extend(VENDOR_BIN_DIRS.iter().map(PathBuf::from));
    dirs.into_iter()
        .map(|d| d.join(tool))
        .find(|p| is_executable(p))
}

fn is_executable(p: &Path) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::metadata(p)
            .map(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
            .unwrap_or(false)
    }
    #[cfg(not(unix))]
    {
        p.is_file()
    }
}

/// Run `program args...` and return stdout as UTF-8 (lossy). Non-zero exit is an error that
/// includes stderr; the process is killed after `timeout`.
pub async fn run(program: &Path, args: &[&str], timeout: Duration) -> anyhow::Result<String> {
    let mut cmd = Command::new(program);
    cmd.args(args)
        .kill_on_drop(true)
        .stdin(std::process::Stdio::null());
    let fut = cmd.output();
    let out = tokio::time::timeout(timeout, fut)
        .await
        .map_err(|_| anyhow!("{} timed out after {:?}", program.display(), timeout))?
        .with_context(|| format!("failed to run {}", program.display()))?;
    if !out.status.success() {
        return Err(anyhow!(
            "{} {} exited with {}: {}",
            program.display(),
            args.join(" "),
            out.status,
            String::from_utf8_lossy(&out.stderr).trim()
        ));
    }
    Ok(String::from_utf8_lossy(&out.stdout).into_owned())
}

/// Parse a number out of SMI text: accepts `"42"`, `"42.5"`, `"42 %"`, `"1234 MiB"`, `"(800Mhz)"`,
/// `"35.0W"`; returns `None` for `""`, `"N/A"`, `"[N/A]"`, `"[Not Supported]"`, `"-"`, `"None"`.
pub fn parse_num(s: &str) -> Option<f64> {
    let t = s
        .trim()
        .trim_matches(|c| c == '(' || c == ')' || c == '[' || c == ']');
    if t.is_empty() {
        return None;
    }
    let lower = t.to_ascii_lowercase();
    if lower == "n/a"
        || lower == "none"
        || lower == "-"
        || lower == "null"
        || lower.contains("not supported")
        || lower.contains("not available")
        || lower.contains("insufficient")
        || lower.contains("unknown")
    {
        return None;
    }
    // Leading numeric token (sign, digits, dot, exponent).
    let mut end = 0;
    for (i, c) in t.char_indices() {
        if c.is_ascii_digit() || c == '.' || c == '-' || c == '+' || c == 'e' || c == 'E' {
            end = i + c.len_utf8();
        } else {
            break;
        }
    }
    // Avoid swallowing a trailing `e` from a unit like `Mhz`? Units start with letters we stop
    // at, but "e" is part of the numeric charset; trim a dangling exponent marker.
    let mut num = &t[..end];
    while num.ends_with(['e', 'E', '-', '+']) {
        num = &num[..num.len() - 1];
    }
    num.parse::<f64>().ok()
}

/// Parse a byte quantity with an optional unit (`B`, `KiB`, `MiB`, `GiB`, `KB`, `MB`, `GB`).
/// A bare number is interpreted with `default_unit_bytes` (e.g. `1 << 20` for SMIs printing MiB).
pub fn parse_bytes(s: &str, default_unit_bytes: u64) -> Option<u64> {
    let v = parse_num(s)?;
    let lower = s.to_ascii_lowercase();
    let mult = if lower.contains("tib")
        || lower.contains(" tb")
        || lower.ends_with("tb")
        || lower.ends_with('t')
    {
        1u64 << 40
    } else if lower.contains("gib")
        || lower.contains(" gb")
        || lower.ends_with("gb")
        || lower.ends_with('g')
    {
        1u64 << 30
    } else if lower.contains("mib")
        || lower.contains(" mb")
        || lower.ends_with("mb")
        || lower.ends_with('m')
    {
        1u64 << 20
    } else if lower.contains("kib")
        || lower.contains(" kb")
        || lower.ends_with("kb")
        || lower.ends_with('k')
    {
        1u64 << 10
    } else if lower.contains("bytes") || lower.ends_with(" b") || lower.ends_with("(b)") {
        1
    } else {
        default_unit_bytes
    };
    Some((v * mult as f64).round() as u64)
}

/// Read a JSON value that may be a plain number, a numeric string, or the amd-smi
/// `{"value": 12, "unit": "W"}` object. `"N/A"` → `None`.
pub fn json_num(v: &serde_json::Value) -> Option<f64> {
    match v {
        serde_json::Value::Number(n) => n.as_f64(),
        serde_json::Value::String(s) => parse_num(s),
        serde_json::Value::Object(m) => m.get("value").and_then(json_num),
        _ => None,
    }
}

/// Like `json_num` but also returns the unit (`amd-smi` objects) so callers can convert MB → bytes.
pub fn json_num_unit(v: &serde_json::Value) -> Option<(f64, Option<String>)> {
    match v {
        serde_json::Value::Object(m) => {
            let n = m.get("value").and_then(json_num)?;
            let unit = m
                .get("unit")
                .and_then(|u| u.as_str())
                .map(|s| s.to_string());
            Some((n, unit))
        }
        other => json_num(other).map(|n| (n, None)),
    }
}

/// Convert `(value, unit)` to bytes; the unit may be absent (then `default_unit_bytes`).
pub fn to_bytes(value: f64, unit: Option<&str>, default_unit_bytes: u64) -> u64 {
    let mult = match unit.map(|u| u.trim().to_ascii_lowercase()).as_deref() {
        Some("b") | Some("bytes") => 1,
        Some("kb") | Some("kib") => 1 << 10,
        Some("mb") | Some("mib") => 1 << 20,
        Some("gb") | Some("gib") => 1 << 30,
        Some("tb") | Some("tib") => 1 << 40,
        _ => default_unit_bytes,
    };
    (value * mult as f64).round() as u64
}

/// Look up a nested JSON path like `["mem_usage", "used_vram"]`.
pub fn json_path<'a>(v: &'a serde_json::Value, path: &[&str]) -> Option<&'a serde_json::Value> {
    let mut cur = v;
    for key in path {
        cur = cur.get(*key)?;
    }
    Some(cur)
}

/// Parse `key : value` lines (nvidia-smi -q / mthreads-gmi -q style) into a flat list in order,
/// keeping the indentation depth so callers can scope keys to sections.
pub fn parse_kv_lines(text: &str) -> Vec<(usize, String, String)> {
    text.lines()
        .filter_map(|line| {
            let depth = line.len() - line.trim_start().len();
            let (k, v) = line.split_once(':')?;
            let k = k.trim();
            if k.is_empty() {
                return None;
            }
            Some((depth, k.to_string(), v.trim().to_string()))
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn numbers() {
        assert_eq!(parse_num("42"), Some(42.0));
        assert_eq!(parse_num(" 42.5 %"), Some(42.5));
        assert_eq!(parse_num("1234 MiB"), Some(1234.0));
        assert_eq!(parse_num("(800Mhz)"), Some(800.0));
        assert_eq!(parse_num("35.0W"), Some(35.0));
        assert_eq!(parse_num("[N/A]"), None);
        assert_eq!(parse_num("[Not Supported]"), None);
        assert_eq!(parse_num(""), None);
        assert_eq!(parse_num("N/A"), None);
    }

    #[test]
    fn bytes() {
        assert_eq!(parse_bytes("1024 MiB", 1), Some(1 << 30));
        assert_eq!(parse_bytes("2 GB", 1), Some(2 << 30));
        assert_eq!(parse_bytes("16368", 1 << 20), Some(16368 << 20));
        assert_eq!(parse_bytes("68702699520", 1), Some(68702699520));
        assert_eq!(to_bytes(196592.0, Some("MB"), 1), 196592 << 20);
    }

    #[test]
    fn json_values() {
        let v: serde_json::Value = serde_json::json!({"value": 23, "unit": "W"});
        assert_eq!(json_num(&v), Some(23.0));
        assert_eq!(json_num(&serde_json::json!("N/A")), None);
        assert_eq!(json_num(&serde_json::json!("12.5")), Some(12.5));
        assert_eq!(json_num_unit(&v), Some((23.0, Some("W".into()))));
    }

    #[test]
    fn kv_lines() {
        let kv = parse_kv_lines("GPU 0\n    Product Name : X\n        Total : 1 MiB\n");
        assert_eq!(kv.len(), 2);
        assert_eq!(kv[0], (4, "Product Name".into(), "X".into()));
        assert_eq!(kv[1].0, 8);
    }
}
