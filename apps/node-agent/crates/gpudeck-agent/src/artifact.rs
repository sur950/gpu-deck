//! Native engine artifacts: download (sha256-pinned), unpack and, when the artifact says so,
//! create a managed Python venv or build from source with CMake.
//!
//! Everything lands in `<state_dir>/engines/<engine>/<version>/` (the caller's `dir`); a
//! `.gpudeck-ready` marker holding the sha256 makes later starts skip the work. The control
//! plane only sends pinned artifacts; an unpinned (all-zero / malformed) sha256 is refused, so
//! an engine without a pinned build must be installed by the operator (`engine_paths`).

use std::path::{Path, PathBuf};
use std::time::Duration;

use gpudeck_protocol::{error_code, Artifact, ArtifactKind, ErrorInfo};
use sha2::{Digest, Sha256};
use tokio::io::AsyncWriteExt;
use tokio_util::sync::CancellationToken;
use tracing::info;

/// Largest single download accepted (the CUDA runtime bundles are ~600 MB).
const MAX_DOWNLOAD_BYTES: u64 = 4 << 30;

fn fail(msg: impl Into<String>) -> ErrorInfo {
    ErrorInfo::new(error_code::ARTIFACT_DOWNLOAD_FAILED, msg).retryable()
}

fn cancelled() -> ErrorInfo {
    ErrorInfo::new(error_code::CANCELLED, "cancelled")
}

/// A sha256 the agent accepts: 64 lowercase hex digits, not the all-zero placeholder.
pub fn is_pinned(sha256: &str) -> bool {
    sha256.len() == 64
        && sha256
            .chars()
            .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase())
        && !sha256.chars().all(|c| c == '0')
}

/// File name of a URL (last path segment, query stripped).
pub fn url_file_name(url: &str) -> String {
    url.split('?')
        .next()
        .unwrap_or(url)
        .rsplit('/')
        .next()
        .filter(|s| !s.is_empty())
        .unwrap_or("artifact")
        .to_string()
}

/// A relative path that stays inside the install folder.
fn safe_relative(p: &str) -> bool {
    let path = Path::new(p);
    !p.is_empty()
        && !path.is_absolute()
        && path
            .components()
            .all(|c| matches!(c, std::path::Component::Normal(_)))
}

/// Stream `url` to `dest` and check its sha256.
pub async fn download_verified(
    url: &str,
    sha256: &str,
    dest: &Path,
    cancel: &CancellationToken,
) -> Result<(), ErrorInfo> {
    if !is_pinned(sha256) {
        return Err(ErrorInfo::new(
            error_code::ARTIFACT_CHECKSUM_MISMATCH,
            format!("{url}: no pinned sha256; install the engine on the node and set engine_paths"),
        ));
    }
    if !url.starts_with("https://") && !url.starts_with("http://") {
        return Err(fail(format!("{url}: only http(s) downloads are supported")));
    }
    let client = reqwest::Client::builder()
        .connect_timeout(Duration::from_secs(30))
        .build()
        .map_err(|e| fail(e.to_string()))?;
    info!(%url, "downloading engine artifact");
    let mut resp = tokio::select! {
        r = client.get(url).send() => r.map_err(|e| fail(format!("GET {url}: {e}")))?,
        _ = cancel.cancelled() => return Err(cancelled()),
    };
    if !resp.status().is_success() {
        return Err(fail(format!("GET {url}: HTTP {}", resp.status())));
    }
    let mut file = tokio::fs::File::create(dest)
        .await
        .map_err(|e| fail(format!("create {}: {e}", dest.display())))?;
    let mut hasher = Sha256::new();
    let mut total: u64 = 0;
    loop {
        let chunk = tokio::select! {
            c = resp.chunk() => c.map_err(|e| fail(format!("read {url}: {e}")))?,
            _ = cancel.cancelled() => {
                let _ = tokio::fs::remove_file(dest).await;
                return Err(cancelled());
            }
        };
        let Some(chunk) = chunk else { break };
        total += chunk.len() as u64;
        if total > MAX_DOWNLOAD_BYTES {
            let _ = tokio::fs::remove_file(dest).await;
            return Err(fail(format!(
                "{url}: larger than {MAX_DOWNLOAD_BYTES} bytes"
            )));
        }
        hasher.update(&chunk);
        file.write_all(&chunk)
            .await
            .map_err(|e| fail(format!("write {}: {e}", dest.display())))?;
    }
    file.flush().await.map_err(|e| fail(e.to_string()))?;
    drop(file);
    let got = hex::encode(hasher.finalize());
    if got != sha256 {
        let _ = tokio::fs::remove_file(dest).await;
        return Err(ErrorInfo::new(
            error_code::ARTIFACT_CHECKSUM_MISMATCH,
            format!("{url}: sha256 {got} != {sha256}"),
        ));
    }
    Ok(())
}

/// Run a program to completion (cancellable); the error carries the tail of its output.
pub async fn run(
    program: &str,
    args: &[String],
    cwd: Option<&Path>,
    cancel: &CancellationToken,
) -> Result<(), ErrorInfo> {
    let mut cmd = tokio::process::Command::new(program);
    cmd.args(args)
        .kill_on_drop(true)
        .stdin(std::process::Stdio::null());
    if let Some(dir) = cwd {
        cmd.current_dir(dir);
    }
    let child = cmd.output();
    let out = tokio::select! {
        o = child => o.map_err(|e| fail(format!("{program}: {e}")))?,
        _ = cancel.cancelled() => return Err(cancelled()),
    };
    if out.status.success() {
        return Ok(());
    }
    let text = format!(
        "{}{}",
        String::from_utf8_lossy(&out.stdout),
        String::from_utf8_lossy(&out.stderr)
    );
    let tail: String = text
        .chars()
        .rev()
        .take(600)
        .collect::<Vec<_>>()
        .into_iter()
        .rev()
        .collect();
    Err(fail(format!("{program} {} failed: {tail}", args.join(" "))))
}

/// Unpack a tarball or zip into `into` (created), dropping `strip` leading components.
async fn unpack(
    kind: ArtifactKind,
    archive: &Path,
    into: &Path,
    strip: u32,
    cancel: &CancellationToken,
) -> Result<(), ErrorInfo> {
    std::fs::create_dir_all(into).map_err(|e| fail(format!("create {}: {e}", into.display())))?;
    let (a, d) = (archive.display().to_string(), into.display().to_string());
    match kind {
        ArtifactKind::Tarball | ArtifactKind::Source => {
            let mut args = vec!["-xf".to_string(), a, "-C".into(), d];
            if strip > 0 {
                args.push(format!("--strip-components={strip}"));
            }
            run("tar", &args, None, cancel).await
        }
        ArtifactKind::Zip => {
            if strip > 0 {
                return Err(fail("strip_components is not supported for zip artifacts"));
            }
            run(
                "unzip",
                &["-q".into(), "-o".into(), a, "-d".into(), d],
                None,
                cancel,
            )
            .await
        }
        other => Err(fail(format!("{other} cannot be unpacked"))),
    }
}

/// `"==3.12"` → (3, 12, exact); `">=3.11"` / `"3.11"` → (3, 11, minimum).
fn python_spec(spec: Option<&str>) -> Option<(u32, u32, bool)> {
    let s = spec?.trim();
    let (exact, rest) = if let Some(r) = s.strip_prefix("==") {
        (true, r)
    } else {
        (false, s.strip_prefix(">=").unwrap_or(s))
    };
    let mut it = rest.split('.');
    let major = it.next()?.trim().parse().ok()?;
    let minor = it.next()?.trim().parse().ok()?;
    Some((major, minor, exact))
}

fn python_version(exe: &str) -> Option<(u32, u32)> {
    let out = std::process::Command::new(exe)
        .args(["-c", "import sys; print('%d.%d' % sys.version_info[:2])"])
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&out.stdout);
    let mut it = text.trim().split('.');
    Some((it.next()?.parse().ok()?, it.next()?.parse().ok()?))
}

/// Interpreter for a managed venv: `GPUDECK_AGENT_PYTHON`, else the newest `python3.x` on PATH
/// that satisfies `spec`.
pub fn find_python(spec: Option<&str>) -> Result<String, ErrorInfo> {
    let want = python_spec(spec);
    let ok = |v: (u32, u32)| match want {
        None => v.0 == 3,
        Some((maj, min, true)) => v == (maj, min),
        Some((maj, min, false)) => v.0 == maj && v.1 >= min,
    };
    let mut candidates: Vec<String> = Vec::new();
    if let Ok(p) = std::env::var("GPUDECK_AGENT_PYTHON") {
        if !p.trim().is_empty() {
            candidates.push(p);
        }
    }
    for minor in (9..=15).rev() {
        candidates.push(format!("python3.{minor}"));
    }
    candidates.push("python3".into());
    for c in candidates {
        if let Some(v) = python_version(&c) {
            if ok(v) {
                return Ok(c);
            }
        }
    }
    Err(ErrorInfo::new(
        error_code::ARTIFACT_DOWNLOAD_FAILED,
        format!(
            "no Python {} found for the engine's venv: install one (python.org, Homebrew, \
             apt) or set GPUDECK_AGENT_PYTHON",
            spec.unwrap_or("3")
        ),
    ))
}

/// Download, verify and install `art` into `dir`; returns once `entry` exists.
pub async fn install(
    art: &Artifact,
    dir: &Path,
    entry: &Path,
    cancel: &CancellationToken,
) -> Result<(), ErrorInfo> {
    std::fs::create_dir_all(dir).map_err(|e| {
        ErrorInfo::new(
            error_code::INTERNAL,
            format!("create {}: {e}", dir.display()),
        )
    })?;
    let file_name = url_file_name(&art.url);
    let downloads = dir.join(".downloads");
    std::fs::create_dir_all(&downloads).map_err(|e| fail(e.to_string()))?;
    let main = downloads.join(&file_name);
    download_verified(&art.url, &art.sha256, &main, cancel).await?;
    let strip = art.strip_components.unwrap_or(0);
    match art.kind {
        ArtifactKind::Tarball | ArtifactKind::Zip | ArtifactKind::Source => {
            unpack(art.kind, &main, dir, strip, cancel).await?
        }
        ArtifactKind::Binary => {
            std::fs::rename(&main, entry).map_err(|e| fail(e.to_string()))?;
        }
        ArtifactKind::Wheel => {
            run(
                "python3",
                &[
                    "-m".into(),
                    "pip".into(),
                    "install".into(),
                    "--no-deps".into(),
                    "--target".into(),
                    dir.display().to_string(),
                    main.display().to_string(),
                ],
                None,
                cancel,
            )
            .await?
        }
        ArtifactKind::PythonVenv => {
            let python = find_python(art.python.as_deref())?;
            let venv = dir.join("venv");
            if !venv.join("bin").join("python3").exists() {
                run(
                    &python,
                    &["-m".into(), "venv".into(), venv.display().to_string()],
                    None,
                    cancel,
                )
                .await?;
            }
            let mut req = main.display().to_string();
            if !art.pip_extras.is_empty() {
                req = format!("{req}[{}]", art.pip_extras.join(","));
            }
            run(
                &venv.join("bin").join("python3").display().to_string(),
                &[
                    "-m".into(),
                    "pip".into(),
                    "install".into(),
                    "--disable-pip-version-check".into(),
                    "--no-input".into(),
                    req,
                ],
                None,
                cancel,
            )
            .await?
        }
    }
    for part in &art.parts {
        let into = match part.dest.as_deref() {
            Some(d) if !d.is_empty() => {
                if !safe_relative(d) {
                    return Err(fail(format!(
                        "artifact part dest {d:?} leaves the install folder"
                    )));
                }
                dir.join(d)
            }
            _ => dir.to_path_buf(),
        };
        let file = downloads.join(url_file_name(&part.url));
        download_verified(&part.url, &part.sha256, &file, cancel).await?;
        unpack(
            part.kind,
            &file,
            &into,
            part.strip_components.unwrap_or(0),
            cancel,
        )
        .await?;
        let _ = std::fs::remove_file(&file);
    }
    if art.kind == ArtifactKind::Source {
        build_source(art, dir, cancel).await?;
    }
    if art.kind != ArtifactKind::Wheel && art.kind != ArtifactKind::PythonVenv {
        let _ = std::fs::remove_file(&main);
    }
    let _ = std::fs::remove_dir_all(&downloads);
    #[cfg(unix)]
    if entry.is_file() {
        use std::os::unix::fs::PermissionsExt;
        if let Ok(meta) = std::fs::metadata(entry) {
            let mode = meta.permissions().mode() | 0o755;
            let _ = std::fs::set_permissions(entry, std::fs::Permissions::from_mode(mode));
        }
    }
    if !entry.exists() {
        return Err(fail(format!(
            "artifact entrypoint {} not found after install",
            entry.display()
        )));
    }
    Ok(())
}

/// `source` artifacts: `cmake -S dir -B dir/build -DCMAKE_BUILD_TYPE=Release <args>` then
/// `cmake --build dir/build --config Release -j N [--target ...]`. Needs cmake and a C/C++
/// toolchain on the node.
async fn build_source(
    art: &Artifact,
    dir: &Path,
    cancel: &CancellationToken,
) -> Result<(), ErrorInfo> {
    let build = art
        .build
        .as_ref()
        .ok_or_else(|| fail("source artifact without a build recipe"))?;
    if build.system != "cmake" {
        return Err(fail(format!("unsupported build system {}", build.system)));
    }
    if crate::template::resolve_executable("cmake", None).is_none() {
        return Err(ErrorInfo::new(
            error_code::ARTIFACT_DOWNLOAD_FAILED,
            "building this engine needs cmake and a C/C++ compiler on the node (macOS: \
             `xcode-select --install` and `brew install cmake`); or install the engine yourself \
             and set engine_paths",
        ));
    }
    let build_dir: PathBuf = dir.join("build");
    let mut configure = vec![
        "-S".to_string(),
        dir.display().to_string(),
        "-B".into(),
        build_dir.display().to_string(),
        "-DCMAKE_BUILD_TYPE=Release".into(),
    ];
    for a in &build.args {
        if !a.starts_with("-D") || a.contains(char::is_whitespace) {
            return Err(fail(format!("refusing build argument {a:?}")));
        }
        configure.push(a.clone());
    }
    info!(dir = %dir.display(), "building engine from source (cmake)");
    run("cmake", &configure, Some(dir), cancel).await?;
    let jobs = std::thread::available_parallelism()
        .map(|n| n.get())
        .unwrap_or(4);
    let mut compile = vec![
        "--build".to_string(),
        build_dir.display().to_string(),
        "--config".into(),
        "Release".into(),
        "-j".into(),
        jobs.to_string(),
    ];
    if !build.targets.is_empty() {
        compile.push("--target".into());
        compile.extend(build.targets.iter().cloned());
    }
    run("cmake", &compile, Some(dir), cancel).await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pinned_sha_rules() {
        assert!(is_pinned(&"ab".repeat(32)));
        assert!(!is_pinned(&"0".repeat(64)));
        assert!(!is_pinned(&"AB".repeat(32)));
        assert!(!is_pinned("abc"));
    }

    #[test]
    fn url_names_and_paths() {
        assert_eq!(
            url_file_name("https://x/y/llama-b1-bin.tar.gz?raw=1"),
            "llama-b1-bin.tar.gz"
        );
        assert!(safe_relative("llama-b12500"));
        assert!(!safe_relative("../etc"));
        assert!(!safe_relative("/abs"));
    }

    #[test]
    fn python_specs() {
        assert_eq!(python_spec(Some("==3.12")), Some((3, 12, true)));
        assert_eq!(python_spec(Some(">=3.11")), Some((3, 11, false)));
        assert_eq!(python_spec(Some("3.10")), Some((3, 10, false)));
        assert_eq!(python_spec(None), None);
    }

    #[tokio::test]
    async fn unpinned_downloads_are_refused() {
        let dir = tempfile::tempdir().unwrap();
        let err = download_verified(
            "https://example.invalid/x.tar.gz",
            &"0".repeat(64),
            &dir.path().join("x"),
            &CancellationToken::new(),
        )
        .await
        .unwrap_err();
        assert_eq!(err.code, error_code::ARTIFACT_CHECKSUM_MISMATCH);
    }

    #[tokio::test]
    async fn tarball_with_parts_strip_and_dest() {
        let src = tempfile::tempdir().unwrap();
        let stage = src.path().join("stage");
        std::fs::create_dir_all(stage.join("pkg-1")).unwrap();
        std::fs::write(stage.join("pkg-1").join("server"), b"#!/bin/sh\n").unwrap();
        std::fs::create_dir_all(stage.join("rt-1")).unwrap();
        std::fs::write(stage.join("rt-1").join("libcudart.so.12"), b"lib").unwrap();
        let cancel = CancellationToken::new();
        let tar = |name: &str, inner: &str| {
            let out = src.path().join(name);
            let st = std::process::Command::new("tar")
                .args([
                    "-czf",
                    out.to_str().unwrap(),
                    "-C",
                    stage.to_str().unwrap(),
                    inner,
                ])
                .status()
                .unwrap();
            assert!(st.success());
            out
        };
        let main = tar("main.tar.gz", "pkg-1");
        let part = tar("part.tar.gz", "rt-1");
        let dest = tempfile::tempdir().unwrap();
        let file = dest.path().join("x.tar.gz");
        std::fs::copy(&main, &file).unwrap();
        unpack(ArtifactKind::Tarball, &file, dest.path(), 0, &cancel)
            .await
            .unwrap();
        unpack(
            ArtifactKind::Tarball,
            &part,
            &dest.path().join("pkg-1"),
            1,
            &cancel,
        )
        .await
        .unwrap();
        assert!(dest.path().join("pkg-1/server").is_file());
        assert!(dest.path().join("pkg-1/libcudart.so.12").is_file());
    }
}
