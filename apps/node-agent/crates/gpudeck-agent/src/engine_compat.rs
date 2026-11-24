//! Which build of a native engine this host can run, and what to do when it cannot.
//!
//! Prebuilt Linux engines are linked against the glibc of the machine that built them: the
//! llama.cpp b12500 CUDA / ROCm / arm64 builds come from Ubuntu 24.04 and need glibc >= 2.38,
//! so they exit with `GLIBC_2.38 not found` on Ubuntu 22.04 / Debian 12. The control plane sends
//! each pinned build with `min_glibc`, same-accelerator `fallbacks` for older systems (an older
//! release built on Ubuntu 22.04) and the engine's official `container` image. The agent picks,
//! in order:
//!
//! 1. an operator-installed binary (`engine_paths`), always first (checked by the caller);
//! 2. the first native build whose `min_glibc` the host meets;
//! 3. the official image, when a container runtime is available;
//! 4. otherwise it refuses with a message that says what the host has, what the build needs
//!    and the three ways out.
//!
//! After a native install, `ldd` on the entrypoint catches what `min_glibc` cannot (a missing
//! `libgomp.so.1` / `libvulkan.so.1`, an old libstdc++): the agent then falls back to the image
//! or names the package to install.

use std::path::Path;
use std::sync::OnceLock;

use gpudeck_protocol::{Artifact, ArtifactContainer};

/// `(major, minor)` glibc version.
pub type Glibc = (u32, u32);

/// First `X.Y` version in `text` scanning tokens from the end (`"glibc 2.35"`,
/// `"ldd (Ubuntu GLIBC 2.35-0ubuntu3.8) 2.35"`).
pub fn parse_glibc(text: &str) -> Option<Glibc> {
    text.split(|c: char| c.is_whitespace() || c == '(' || c == ')')
        .rev()
        .find_map(|tok| {
            let tok = tok.split('-').next().unwrap_or(tok);
            let mut it = tok.split('.');
            let major = it.next()?.parse().ok()?;
            let minor = it.next()?.parse().ok()?;
            Some((major, minor))
        })
}

fn detect_glibc() -> Option<Glibc> {
    if !cfg!(target_os = "linux") {
        return None;
    }
    if let Ok(out) = std::process::Command::new("getconf")
        .arg("GNU_LIBC_VERSION")
        .output()
    {
        if out.status.success() {
            if let Some(v) = parse_glibc(&String::from_utf8_lossy(&out.stdout)) {
                return Some(v);
            }
        }
    }
    // musl's ldd prints "musl libc" on stderr and exits 1: no glibc then.
    let out = std::process::Command::new("ldd")
        .arg("--version")
        .output()
        .ok()?;
    let text = String::from_utf8_lossy(&out.stdout);
    let first = text.lines().next().unwrap_or_default();
    let lower = first.to_ascii_lowercase();
    if lower.contains("glibc") || lower.contains("gnu libc") {
        parse_glibc(first)
    } else {
        None
    }
}

/// The host's glibc (Linux; cached). None on macOS and on musl hosts.
pub fn host_glibc() -> Option<Glibc> {
    static GLIBC: OnceLock<Option<Glibc>> = OnceLock::new();
    *GLIBC.get_or_init(detect_glibc)
}

/// `"2.35"` for `hello.capabilities.glibc_version`.
pub fn host_glibc_string() -> Option<String> {
    host_glibc().map(|(a, b)| format!("{a}.{b}"))
}

/// Whether a host with `have` runs a build that needs `need` (no requirement = yes; a
/// requirement on a host without glibc = no).
pub fn meets(have: Option<Glibc>, need: Option<&str>) -> bool {
    let Some(need) = need.and_then(parse_glibc) else {
        return true;
    };
    matches!(have, Some(h) if h >= need)
}

/// What [`choose`] decided.
#[derive(Debug, Clone, PartialEq)]
pub enum Choice {
    /// Install and run this native build (the primary or one of its fallbacks).
    Native(Box<Artifact>),
    /// Run the engine's official image instead.
    Container(ArtifactContainer),
    /// Nothing fits: the message tells the operator why and what to do.
    Incompatible(String),
}

fn without_alternatives(a: &Artifact) -> Artifact {
    Artifact {
        fallbacks: vec![],
        container: None,
        ..a.clone()
    }
}

/// Pick the build for this host (`linux` false = no glibc checks: macOS).
pub fn choose(
    engine: &str,
    version: &str,
    art: &Artifact,
    glibc: Option<Glibc>,
    linux: bool,
    container_runtime: bool,
) -> Choice {
    let candidates = std::iter::once(art).chain(art.fallbacks.iter());
    for c in candidates {
        if !linux || meets(glibc, c.min_glibc.as_deref()) {
            return Choice::Native(Box::new(without_alternatives(c)));
        }
    }
    if let (Some(c), true) = (&art.container, container_runtime) {
        return Choice::Container(c.clone());
    }
    Choice::Incompatible(incompatible_message(
        engine,
        version,
        art,
        glibc,
        container_runtime,
    ))
}

fn engine_path_hint(engine: &str) -> String {
    format!(
        "install the engine yourself and set engine_paths.{engine} in agent.toml (or \
         GPUDECK_AGENT_ENGINE_PATHS_{})",
        engine.to_ascii_uppercase()
    )
}

fn container_hint(art: &Artifact, container_runtime: bool) -> Option<String> {
    let c = art.container.as_ref()?;
    Some(if container_runtime {
        format!("the official image {} failed too", c.image)
    } else {
        format!(
            "install Docker or Podman so the agent runs the official image {} instead",
            c.image
        )
    })
}

/// Why no native build fits the host, and the ways out.
pub fn incompatible_message(
    engine: &str,
    version: &str,
    art: &Artifact,
    glibc: Option<Glibc>,
    container_runtime: bool,
) -> String {
    let need = std::iter::once(art)
        .chain(art.fallbacks.iter())
        .filter_map(|a| a.min_glibc.as_deref().and_then(parse_glibc))
        .min();
    let have = match glibc {
        Some((a, b)) => format!("this host has glibc {a}.{b}"),
        None => "this host has no glibc (musl / Alpine?)".into(),
    };
    let need = match need {
        Some((a, b)) => format!(
            "the pinned {engine} {version} builds for this platform need glibc >= {a}.{b} \
             (Ubuntu 24.04, Debian 13, RHEL 10 or newer)"
        ),
        None => format!("the pinned {engine} {version} build does not run on this host"),
    };
    let mut ways = vec![];
    if let Some(c) = container_hint(art, container_runtime) {
        ways.push(c);
    }
    ways.push(engine_path_hint(engine));
    ways.push("or upgrade the node's OS".into());
    format!("{need}; {have}. Options: {}", ways.join("; "))
}

/// Shared libraries / symbol versions `ldd` reports missing for `exe` (Linux only; empty when
/// `ldd` is unavailable). The executable's folder is on `LD_LIBRARY_PATH`, like at launch.
pub fn missing_libraries(exe: &Path) -> Vec<String> {
    if !cfg!(target_os = "linux") {
        return vec![];
    }
    let mut cmd = std::process::Command::new("ldd");
    cmd.arg(exe);
    if let Some(dir) = exe.parent() {
        cmd.env("LD_LIBRARY_PATH", dir);
    }
    match cmd.output() {
        Ok(out) => parse_ldd_missing(&format!(
            "{}\n{}",
            String::from_utf8_lossy(&out.stdout),
            String::from_utf8_lossy(&out.stderr)
        )),
        Err(_) => vec![],
    }
}

/// `libgomp.so.1 => not found` → `libgomp.so.1`; ``version `GLIBC_2.38' not found`` →
/// `GLIBC_2.38`.
pub fn parse_ldd_missing(text: &str) -> Vec<String> {
    let mut out: Vec<String> = vec![];
    for line in text.lines() {
        let line = line.trim();
        let item = if let Some((lib, rest)) = line.split_once("=>") {
            rest.trim()
                .starts_with("not found")
                .then(|| lib.trim().to_string())
        } else if let Some(i) = line.find("version `") {
            let rest = &line[i + "version `".len()..];
            rest.split('\'').next().map(str::to_string)
        } else {
            None
        };
        if let Some(item) = item.filter(|s| !s.is_empty()) {
            if !out.contains(&item) {
                out.push(item);
            }
        }
    }
    out
}

/// Package hint for a missing library (Debian / Ubuntu names).
fn package_hint(item: &str) -> Option<&'static str> {
    let hints: [(&str, &str); 7] = [
        ("libgomp.so", "libgomp1"),
        ("libvulkan.so", "libvulkan1 (and the GPU's Vulkan driver)"),
        ("libssl.so.3", "libssl3"),
        ("libcrypto.so.3", "libssl3"),
        ("libstdc++", "a newer libstdc++6"),
        ("GLIBCXX_", "a newer libstdc++6 (or a newer OS)"),
        ("GLIBC_", "a newer glibc (a newer OS)"),
    ];
    hints
        .iter()
        .find(|(k, _)| item.starts_with(k) || item.contains(k))
        .map(|(_, v)| *v)
}

/// Message for an installed build that cannot start because of `missing` libraries.
pub fn missing_libraries_message(
    engine: &str,
    version: &str,
    exe: &Path,
    missing: &[String],
    art: &Artifact,
    container_runtime: bool,
) -> String {
    let mut pkgs: Vec<&str> = missing.iter().filter_map(|m| package_hint(m)).collect();
    pkgs.dedup();
    let mut ways = vec![];
    if !pkgs.is_empty() {
        ways.push(format!("install {}", pkgs.join(", ")));
    }
    if let Some(c) = container_hint(art, container_runtime) {
        ways.push(c);
    }
    ways.push(engine_path_hint(engine));
    format!(
        "{engine} {version}: {} cannot start on this host, missing {}. Options: {}",
        exe.display(),
        missing.join(", "),
        ways.join("; ")
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use gpudeck_protocol::ArtifactKind;

    fn build(name: &str, glibc: Option<&str>) -> Artifact {
        Artifact {
            url: format!("https://example.invalid/{name}.tar.gz"),
            sha256: "ab".repeat(32),
            kind: ArtifactKind::Tarball,
            entrypoint: Some(format!("{name}/llama-server")),
            size_bytes: None,
            strip_components: None,
            parts: vec![],
            pip_extras: vec![],
            python: None,
            build: None,
            min_glibc: glibc.map(str::to_string),
            fallbacks: vec![],
            container: None,
        }
    }

    fn image() -> ArtifactContainer {
        ArtifactContainer {
            image: "ghcr.io/ggml-org/llama.cpp:server-cuda-b12464@sha256:aa".into(),
            entrypoint: Some("/app/llama-server".into()),
            version: Some("b12464".into()),
        }
    }

    #[test]
    fn glibc_versions() {
        assert_eq!(parse_glibc("glibc 2.35\n"), Some((2, 35)));
        assert_eq!(
            parse_glibc("ldd (Ubuntu GLIBC 2.35-0ubuntu3.8) 2.35"),
            Some((2, 35))
        );
        assert_eq!(parse_glibc("ldd (GNU libc) 2.36"), Some((2, 36)));
        assert_eq!(parse_glibc("musl libc (x86_64)"), None);
        assert!(meets(Some((2, 39)), Some("2.38")));
        assert!(meets(Some((2, 38)), Some("2.38")));
        assert!(!meets(Some((2, 35)), Some("2.38")));
        assert!(
            !meets(None, Some("2.35")),
            "musl host cannot run glibc builds"
        );
        assert!(meets(None, None));
        if cfg!(target_os = "macos") {
            assert_eq!(host_glibc(), None);
        }
    }

    #[test]
    fn picks_the_first_build_the_host_runs() {
        let mut art = build("llama-b12500", Some("2.38"));
        art.fallbacks = vec![build("llama-b10375", Some("2.35"))];
        art.container = Some(image());
        // Ubuntu 24.04: the pinned build.
        match choose("llama_cpp", "b12500", &art, Some((2, 39)), true, true) {
            Choice::Native(a) => {
                assert!(a.url.contains("b12500"));
                assert!(a.fallbacks.is_empty() && a.container.is_none());
            }
            other => panic!("{other:?}"),
        }
        // Ubuntu 22.04: the older release.
        match choose("llama_cpp", "b12500", &art, Some((2, 35)), true, false) {
            Choice::Native(a) => assert!(a.url.contains("b10375")),
            other => panic!("{other:?}"),
        }
        // macOS: no glibc checks at all.
        assert!(matches!(
            choose("llama_cpp", "b12500", &art, None, false, false),
            Choice::Native(a) if a.url.contains("b12500")
        ));
    }

    #[test]
    fn container_then_a_clear_refusal() {
        let mut art = build("llama-b12500-cuda", Some("2.38"));
        art.container = Some(image());
        // Debian 12 with Docker: the official image.
        assert_eq!(
            choose("llama_cpp", "b12500", &art, Some((2, 36)), true, true),
            Choice::Container(image())
        );
        // Debian 12 without a container runtime: refuse and say why.
        let Choice::Incompatible(msg) =
            choose("llama_cpp", "b12500", &art, Some((2, 36)), true, false)
        else {
            panic!("expected a refusal")
        };
        assert!(msg.contains("glibc >= 2.38"), "{msg}");
        assert!(msg.contains("this host has glibc 2.36"), "{msg}");
        assert!(msg.contains("install Docker or Podman"), "{msg}");
        assert!(msg.contains("engine_paths.llama_cpp"), "{msg}");
        assert!(
            msg.contains("GPUDECK_AGENT_ENGINE_PATHS_LLAMA_CPP"),
            "{msg}"
        );
        // No image to fall back to.
        art.container = None;
        let Choice::Incompatible(msg) = choose("llama_cpp", "b12500", &art, None, true, true)
        else {
            panic!("expected a refusal")
        };
        assert!(msg.contains("no glibc") && !msg.contains("Docker"), "{msg}");
    }

    #[test]
    fn ldd_output() {
        let text = "\tlinux-vdso.so.1 (0x00007ffd)\n\
            \tlibgomp.so.1 => not found\n\
            \tlibllama.so.1 => /opt/x/libllama.so.1 (0x00007f)\n\
            ./llama-server: /lib/x86_64-linux-gnu/libc.so.6: version `GLIBC_2.38' not found \
            (required by ./llama-server)\n\
            ./llama-server: /lib/x86_64-linux-gnu/libc.so.6: version `GLIBC_2.38' not found \
            (required by /opt/x/libllama.so.1)\n";
        let missing = parse_ldd_missing(text);
        assert_eq!(missing, vec!["libgomp.so.1", "GLIBC_2.38"]);
        let mut art = build("llama-b12500", Some("2.34"));
        art.container = Some(image());
        let msg = missing_libraries_message(
            "llama_cpp",
            "b12500",
            Path::new("/s/llama-server"),
            &missing,
            &art,
            false,
        );
        assert!(msg.contains("missing libgomp.so.1, GLIBC_2.38"), "{msg}");
        assert!(msg.contains("install libgomp1, a newer glibc"), "{msg}");
        assert!(msg.contains("install Docker or Podman"), "{msg}");
        assert!(missing_libraries(Path::new("/definitely/missing")).is_empty());
    }
}
