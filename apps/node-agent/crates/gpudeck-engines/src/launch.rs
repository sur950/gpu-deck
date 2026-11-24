//! Launch helpers shared by every adapter: environment (device pinning from the slice map,
//! shared-library path, user env), `{{placeholder}}` templates, and wrapping a native argv into
//! a `docker run` when `runtime == container`.

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

use anyhow::{anyhow, bail};
use gpudeck_devices::{slice_env, Vendor};

use crate::types::{ContainerSpec, LaunchSpec, ProcessSpec, Runtime};

/// Host the engine binds to inside its process: loopback natively and on Linux host networking,
/// `0.0.0.0` inside a bridged container (the port is published on 127.0.0.1 only).
pub fn bind_host(spec: &LaunchSpec) -> String {
    if spec.runtime == Runtime::Container && !cfg!(target_os = "linux") {
        "0.0.0.0".into()
    } else {
        spec.host.clone()
    }
}

/// Engine environment: device pinning + compute-share enforcement (`gpudeck_devices::slice_env`),
/// then `spec.env` (wins). `core_count` is taken from `spec.env["GPUDECK_DEVICE_CORES"]` when the
/// agent provides it (AMD CU masks).
pub fn base_env(spec: &LaunchSpec) -> BTreeMap<String, String> {
    let cores = spec
        .env
        .get("GPUDECK_DEVICE_CORES")
        .and_then(|v| v.parse().ok());
    let mut env = slice_env(&spec.devices, cores);
    for (k, v) in &spec.env {
        if k != "GPUDECK_DEVICE_CORES" {
            env.insert(k.clone(), v.clone());
        }
    }
    if let Some(cache) = &spec.cache_dir {
        // Python engines must never reach the network for weights: the agent pre-fetches into the
        // cache and the egress lock blocks the rest.
        env.entry("HF_HUB_OFFLINE".into())
            .or_insert_with(|| "1".into());
        env.entry("HF_HOME".into()).or_insert_with(|| cache.clone());
    }
    env
}

/// Add the shared-library search path for native engines shipped with their `.dylib`/`.so`
/// (llama.cpp / whisper.cpp / sd.cpp release archives): `spec.library_dir`, else the directory of
/// an absolute `executable` when it contains `lib*.dylib|so`.
pub fn add_library_path(env: &mut BTreeMap<String, String>, spec: &LaunchSpec, lib_prefix: &str) {
    let dir = spec.library_dir.clone().or_else(|| {
        let exe = spec.executable.as_deref()?;
        let parent = Path::new(exe).parent()?;
        let has_lib = std::fs::read_dir(parent).ok()?.flatten().any(|e| {
            let n = e.file_name().to_string_lossy().to_string();
            n.starts_with(lib_prefix) && (n.contains(".dylib") || n.contains(".so"))
        });
        if has_lib {
            Some(parent.to_string_lossy().into_owned())
        } else {
            None
        }
    });
    if let Some(dir) = dir {
        let var = if cfg!(target_os = "macos") {
            "DYLD_LIBRARY_PATH"
        } else {
            "LD_LIBRARY_PATH"
        };
        let val = match env.get(var).or(spec.env.get(var)) {
            Some(existing) if !existing.is_empty() => format!("{dir}:{existing}"),
            _ => dir,
        };
        env.insert(var.into(), val);
    }
}

/// Values for `{{placeholders}}` (`EngineSpec.launch_template.command` contract).
pub fn template_vars(spec: &LaunchSpec) -> BTreeMap<&'static str, String> {
    let model_dir = Path::new(&spec.model_path)
        .parent()
        .map(|p| p.to_string_lossy().into_owned())
        .unwrap_or_default();
    let model_dir = if Path::new(&spec.model_path).is_dir() {
        spec.model_path.clone()
    } else {
        model_dir
    };
    BTreeMap::from([
        ("port", spec.port.to_string()),
        ("host", bind_host(spec)),
        ("model_path", spec.model_path.clone()),
        ("model_dir", model_dir),
        (
            "context_size",
            spec.context_size.map(|c| c.to_string()).unwrap_or_default(),
        ),
        ("served_model_name", spec.served_model_name.clone()),
        ("tp_size", spec.tp_size.to_string()),
        ("pp_size", spec.pp_size.to_string()),
        ("device_indices", spec.device_indices()),
        ("cache_dir", spec.cache_dir.clone().unwrap_or_default()),
    ])
}

/// Expand a template argv. An element that is exactly `{{flags}}` expands to `flags`; other
/// placeholders are substituted inside strings. Unknown placeholders are an error.
pub fn expand(argv: &[String], spec: &LaunchSpec, flags: &[String]) -> anyhow::Result<Vec<String>> {
    let vars = template_vars(spec);
    let mut out = Vec::new();
    for a in argv {
        if a.trim() == "{{flags}}" {
            out.extend(flags.iter().cloned());
            continue;
        }
        out.push(expand_str(a, &vars)?);
    }
    Ok(out)
}

pub fn expand_str(s: &str, vars: &BTreeMap<&'static str, String>) -> anyhow::Result<String> {
    let mut out = String::with_capacity(s.len());
    let mut rest = s;
    while let Some(start) = rest.find("{{") {
        out.push_str(&rest[..start]);
        let after = &rest[start + 2..];
        let end = after
            .find("}}")
            .ok_or_else(|| anyhow!("unterminated placeholder in {s:?}"))?;
        let name = after[..end].trim();
        let val = vars
            .get(name)
            .ok_or_else(|| anyhow!("unknown placeholder {{{{{name}}}}} in {s:?}"))?;
        out.push_str(val);
        rest = &after[end + 2..];
    }
    out.push_str(rest);
    Ok(out)
}

/// `docker run` device arguments per vendor.
pub fn container_device_args(spec: &LaunchSpec) -> anyhow::Result<Vec<String>> {
    let Some(vendor) = spec.vendor() else {
        return Ok(Vec::new());
    };
    let ids: Vec<String> = spec
        .devices
        .iter()
        .map(|d| match (&d.uuid, &d.slice) {
            (Some(u), Some(_)) if u.starts_with("MIG-") => u.clone(),
            _ => d.index.to_string(),
        })
        .collect();
    Ok(match vendor {
        Vendor::Nvidia => {
            let list = ids.join(",");
            let v = if ids.len() > 1 { format!("\"device={list}\"") } else { format!("device={list}") };
            vec!["--gpus".into(), v]
        }
        Vendor::Amd | Vendor::Hygon => vec![
            "--device=/dev/kfd".into(),
            "--device=/dev/dri".into(),
            "--group-add=video".into(),
            "--security-opt=seccomp=unconfined".into(),
        ],
        Vendor::Metax => vec!["--device=/dev/mxcd".into(), "--device=/dev/dri".into(), "--group-add=video".into()],
        Vendor::Moorethreads => vec!["--runtime=mthreads".into(), format!("--env=MTHREADS_VISIBLE_DEVICES={}", ids.join(","))],
        Vendor::Thead => vec![format!("--env=PPU_VISIBLE_DEVICES={}", ids.join(","))],
        Vendor::Apple => bail!("containers cannot use the Apple GPU (Metal is not exposed to Linux VMs); use runtime=native"),
    })
}

/// Finish a launch: native → `program args` with `env`; container → `docker run ...` running the
/// same argv as `--entrypoint program` (or `ContainerConfig.command` when set) with model/cache
/// paths bind-mounted read-only at identical paths.
pub fn finish(
    spec: &LaunchSpec,
    program: String,
    args: Vec<String>,
    env: BTreeMap<String, String>,
    default_image: Option<&str>,
) -> anyhow::Result<ProcessSpec> {
    if spec.runtime != Runtime::Container {
        return Ok(ProcessSpec {
            program,
            args,
            env,
            cwd: spec.working_dir.clone(),
            container: None,
        });
    }
    let cfg = spec.container.clone().unwrap_or_default();
    let image = if cfg.image.is_empty() {
        default_image
            .ok_or_else(|| anyhow!("{}: runtime=container needs container.image", spec.engine))?
            .to_string()
    } else {
        cfg.image.clone()
    };
    let runtime = cfg.runtime.clone().unwrap_or_else(|| "docker".into());
    let name = format!(
        "gpudeck-{}-{}",
        spec.engine.as_str().replace('_', "-"),
        spec.port
    );
    let host_network = cfg!(target_os = "linux");

    let mut mounts: BTreeSet<String> = BTreeSet::new();
    for p in spec
        .model_paths
        .iter()
        .chain(std::iter::once(&spec.model_path))
    {
        let path = Path::new(p);
        let dir = if path.is_dir() {
            path
        } else {
            path.parent().unwrap_or(path)
        };
        if !dir.as_os_str().is_empty() {
            mounts.insert(format!("{0}:{0}:ro", dir.display()));
        }
    }
    for p in [&spec.cache_dir, &spec.models_dir, &spec.mmproj_path]
        .into_iter()
        .flatten()
    {
        let path = Path::new(p);
        let dir = if path.extension().is_some() {
            path.parent().unwrap_or(path)
        } else {
            path
        };
        mounts.insert(format!("{0}:{0}:ro", dir.display()));
    }
    for cv in &spec.control_vectors {
        if let Some(parent) = Path::new(&cv.path).parent() {
            mounts.insert(format!("{0}:{0}:ro", parent.display()));
        }
    }
    let mut volumes: Vec<String> = mounts.into_iter().collect();
    volumes.extend(cfg.volumes.iter().cloned());

    let device_args = container_device_args(spec)?;
    let ports = if host_network {
        Vec::new()
    } else {
        vec![format!("127.0.0.1:{0}:{0}", spec.port)]
    };
    let (entrypoint, command) = if cfg.command.is_empty() {
        (Some(program), args)
    } else {
        (None, expand(&cfg.command, spec, &[])?)
    };

    let mut argv: Vec<String> = vec!["run".into(), "--rm".into(), "--name".into(), name.clone()];
    if host_network {
        argv.push("--network=host".into());
    }
    for p in &ports {
        argv.push("-p".into());
        argv.push(p.clone());
    }
    argv.extend(device_args.iter().cloned());
    if let Some(shm) = &cfg.shm_size {
        argv.push(format!("--shm-size={shm}"));
    } else if host_network
        && matches!(
            spec.engine.as_str(),
            "vllm" | "vllm_omni" | "sglang" | "sglang_diffusion" | "tensorrt_llm"
        )
    {
        argv.push("--ipc=host".into());
    }
    for v in &volumes {
        argv.push("-v".into());
        argv.push(v.clone());
    }
    for (k, v) in &env {
        argv.push("-e".into());
        argv.push(format!("{k}={v}"));
    }
    if let Some(wd) = &spec.working_dir {
        argv.push("-w".into());
        argv.push(wd.clone());
    }
    argv.extend(cfg.run_args.iter().cloned());
    if let Some(ep) = &entrypoint {
        argv.push("--entrypoint".into());
        argv.push(ep.clone());
    }
    argv.push(image.clone());
    argv.extend(command.iter().cloned());

    Ok(ProcessSpec {
        program: runtime.clone(),
        args: argv,
        env: BTreeMap::new(),
        cwd: None,
        container: Some(ContainerSpec {
            runtime,
            image,
            name,
            volumes,
            ports,
            host_network,
            device_args,
            env,
            command: entrypoint.into_iter().chain(command).collect(),
            extra_args: cfg.run_args,
        }),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::EngineKind;

    #[test]
    fn templates() {
        let mut spec = LaunchSpec::new(EngineKind::Custom, "m", "/models/x/model.gguf", 10240);
        spec.context_size = Some(4096);
        let argv: Vec<String> = [
            "srv",
            "--port={{port}}",
            "-m",
            "{{ model_path }}",
            "{{flags}}",
            "--dir",
            "{{model_dir}}",
        ]
        .iter()
        .map(|s| s.to_string())
        .collect();
        let out = expand(&argv, &spec, &["--a".into(), "1".into()]).unwrap();
        assert_eq!(
            out,
            vec![
                "srv",
                "--port=10240",
                "-m",
                "/models/x/model.gguf",
                "--a",
                "1",
                "--dir",
                "/models/x"
            ]
        );
        assert!(expand(&["{{nope}}".to_string()], &spec, &[]).is_err());
    }
}
