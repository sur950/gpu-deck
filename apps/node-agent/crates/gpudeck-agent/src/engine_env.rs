//! Process environment of native engines and host prerequisites checked before a launch.
//!
//! * The engine executable's directory goes first on `PATH` (a Python venv's `bin/`: SGLang /
//!   flashinfer / deep_gemm JIT call `ninja` and friends by name), `VIRTUAL_ENV` is set for a venv,
//!   and `TMPDIR` points at `<state_dir>/tmp` (outlives the agent, writable under the systemd
//!   unit's `ProtectSystem=strict`).
//! * SGLang native on NVIDIA needs a CUDA toolkit (`nvcc` + headers, `CUDA_HOME`) and `ninja` for
//!   its JIT kernels; a pip venv alone fails late and obscurely (deep_gemm asserts `CUDA_HOME`,
//!   flashinfer "CUDA compiler and CUDA toolkit headers are incompatible"). The agent finds the
//!   toolkit (`CUDA_HOME` / `CUDA_PATH`, `/usr/local/cuda`, `nvcc` on `PATH`), exports
//!   `CUDA_HOME` + its `bin/`, or refuses up front with what is missing and the ways out.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use gpudeck_protocol::{DeviceAssignment, DeviceVendor, EngineName};

use crate::backend::ProcSpec;

/// What was found of a CUDA toolkit for an engine process.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct CudaToolkit {
    pub cuda_home: Option<PathBuf>,
    /// `CUDA_HOME` / `CUDA_PATH` was set explicitly (trusted even without `nvcc`).
    pub explicit: bool,
    pub nvcc: Option<PathBuf>,
    pub ninja: Option<PathBuf>,
}

fn find_in(dirs: &[PathBuf], name: &str) -> Option<PathBuf> {
    dirs.iter().map(|d| d.join(name)).find(|p| p.is_file())
}

/// Look for a CUDA toolkit and `ninja`. `env` reads the engine's environment (launch env, then
/// the agent's); `path` is the engine's `PATH` (venv bin first); `roots` the default install
/// locations (`/usr/local/cuda`, ...).
pub fn find_cuda_toolkit(
    env: &dyn Fn(&str) -> Option<String>,
    path: &[PathBuf],
    roots: &[PathBuf],
) -> CudaToolkit {
    let mut tk = CudaToolkit::default();
    if let Some(home) = ["CUDA_HOME", "CUDA_PATH"]
        .iter()
        .filter_map(|k| env(k))
        .map(|v| PathBuf::from(v.trim()))
        .find(|p| p.is_dir())
    {
        tk.nvcc = find_in(&[home.join("bin")], "nvcc");
        tk.cuda_home = Some(home);
        tk.explicit = true;
    }
    if tk.nvcc.is_none() {
        tk.nvcc = find_in(path, "nvcc").or_else(|| {
            find_in(
                &roots.iter().map(|r| r.join("bin")).collect::<Vec<_>>(),
                "nvcc",
            )
        });
    }
    if tk.cuda_home.is_none() {
        // nvcc → <home>/bin/nvcc (follow a /usr/bin/nvcc symlink to the real toolkit).
        tk.cuda_home = tk.nvcc.as_ref().and_then(|n| {
            let real = std::fs::canonicalize(n).unwrap_or_else(|_| n.clone());
            real.parent()?.parent().map(Path::to_path_buf)
        });
    }
    tk.ninja = find_in(path, "ninja");
    tk
}

/// `Err(message)` when SGLang cannot JIT its kernels on this host.
pub fn sglang_preflight(tk: &CudaToolkit) -> Result<(), String> {
    let mut missing = vec![];
    if tk.cuda_home.is_none() || (!tk.explicit && tk.nvcc.is_none()) {
        missing.push("a CUDA toolkit (nvcc + headers; CUDA_HOME not set, no /usr/local/cuda, no nvcc on PATH)");
    }
    if tk.ninja.is_none() {
        missing.push("ninja (not in the engine's venv bin/ or on PATH)");
    }
    if missing.is_empty() {
        return Ok(());
    }
    Err(format!(
        "SGLang (native) needs {} to build its JIT kernels (deep_gemm, flashinfer); a pip venv alone is not enough. \
         Install the CUDA toolkit matching the driver's CUDA version (e.g. `apt install cuda-toolkit-13-0`, or set \
         CUDA_HOME in the deployment's env / the agent's environment) and `pip install ninja` in the engine's venv, \
         or deploy SGLang with the container runtime (the official lmsysorg/sglang image ships the toolkit).",
        missing.join(" and ")
    ))
}

fn path_dirs(path: &str) -> Vec<PathBuf> {
    std::env::split_paths(path).collect()
}

/// Prepare the environment of a native engine process in place (see the module docs).
/// `Err(message)` = a host prerequisite is missing (the load fails with `unsupported_engine`).
pub fn prepare_native(
    spec: &mut ProcSpec,
    engine: EngineName,
    devices: &[DeviceAssignment],
    tmp_dir: &Path,
) -> Result<(), String> {
    if spec.container.is_some() {
        return Ok(());
    }
    let agent_env = |k: &str| std::env::var(k).ok().filter(|v| !v.is_empty());
    let base_path = spec
        .env
        .get("PATH")
        .cloned()
        .or_else(|| agent_env("PATH"))
        .unwrap_or_else(|| "/usr/local/bin:/usr/bin:/bin".into());
    let mut dirs = path_dirs(&base_path);
    let exe_dir = Path::new(&spec.program).parent().map(Path::to_path_buf);
    if let Some(d) = exe_dir.as_ref().filter(|d| d.is_absolute()) {
        dirs.retain(|x| x != d);
        dirs.insert(0, d.clone());
        // <venv>/bin/python → VIRTUAL_ENV=<venv> (what `activate` would set).
        if let Some(venv) = d.parent().filter(|v| v.join("pyvenv.cfg").is_file()) {
            spec.env
                .entry("VIRTUAL_ENV".into())
                .or_insert_with(|| venv.display().to_string());
        }
    }
    if !spec.env.contains_key("TMPDIR") && std::fs::create_dir_all(tmp_dir).is_ok() {
        spec.env
            .insert("TMPDIR".into(), tmp_dir.display().to_string());
    }
    let nvidia = devices.iter().any(|d| d.vendor == DeviceVendor::Nvidia);
    if engine == EngineName::Sglang && nvidia {
        let launch_env: BTreeMap<String, String> = spec.env.clone();
        let env = |k: &str| launch_env.get(k).cloned().or_else(|| agent_env(k));
        let roots = [PathBuf::from("/usr/local/cuda"), PathBuf::from("/opt/cuda")];
        let tk = find_cuda_toolkit(&env, &dirs, &roots);
        sglang_preflight(&tk)?;
        if let Some(home) = &tk.cuda_home {
            spec.env
                .entry("CUDA_HOME".into())
                .or_insert_with(|| home.display().to_string());
            let bin = home.join("bin");
            if bin.is_dir() && !dirs.contains(&bin) {
                dirs.push(bin);
            }
        }
    }
    if let Ok(joined) = std::env::join_paths(&dirs) {
        spec.env
            .insert("PATH".into(), joined.to_string_lossy().into_owned());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use uuid::Uuid;

    fn touch_exe(p: &Path) {
        std::fs::create_dir_all(p.parent().unwrap()).unwrap();
        std::fs::write(p, b"#!/bin/sh\n").unwrap();
    }

    fn spec(program: &Path) -> ProcSpec {
        ProcSpec {
            program: program.display().to_string(),
            args: vec![],
            env: BTreeMap::new(),
            cwd: None,
            container: None,
        }
    }

    fn nvidia() -> Vec<DeviceAssignment> {
        vec![DeviceAssignment {
            device_id: Uuid::nil(),
            index: 0,
            vendor: DeviceVendor::Nvidia,
            slice: None,
        }]
    }

    #[test]
    fn venv_bin_first_on_path_and_tmpdir() {
        let d = tempfile::tempdir().unwrap();
        let venv = d.path().join("venv");
        let py = venv.join("bin/python");
        touch_exe(&py);
        std::fs::write(venv.join("pyvenv.cfg"), "home = /usr/bin\n").unwrap();
        let mut s = spec(&py);
        s.env.insert("PATH".into(), "/usr/bin:/bin".into());
        prepare_native(&mut s, EngineName::Vllm, &[], &d.path().join("tmp")).unwrap();
        assert_eq!(
            s.env["PATH"],
            format!("{}:/usr/bin:/bin", venv.join("bin").display())
        );
        assert_eq!(s.env["VIRTUAL_ENV"], venv.display().to_string());
        assert_eq!(s.env["TMPDIR"], d.path().join("tmp").display().to_string());
        assert!(d.path().join("tmp").is_dir());
    }

    #[test]
    fn sglang_without_toolkit_is_refused_with_the_way_out() {
        let d = tempfile::tempdir().unwrap();
        let py = d.path().join("venv/bin/python");
        touch_exe(&py);
        let mut s = spec(&py);
        s.env
            .insert("PATH".into(), d.path().join("empty").display().to_string());
        let tk = find_cuda_toolkit(&|_| None, &[d.path().join("venv/bin")], &[]);
        assert!(tk.cuda_home.is_none() && tk.ninja.is_none());
        let msg = sglang_preflight(&tk).unwrap_err();
        assert!(msg.contains("CUDA toolkit") && msg.contains("ninja") && msg.contains("container"));
        // Apple / AMD / CPU launches are not checked.
        let mut s2 = s.clone();
        prepare_native(&mut s2, EngineName::Sglang, &[], &d.path().join("t")).unwrap();
    }

    #[test]
    fn sglang_toolkit_found_from_nvcc_and_venv_ninja() {
        let d = tempfile::tempdir().unwrap();
        let py = d.path().join("venv/bin/python");
        touch_exe(&py);
        touch_exe(&d.path().join("venv/bin/ninja"));
        let cuda = d.path().join("cuda-13.0");
        touch_exe(&cuda.join("bin/nvcc"));
        let tk = find_cuda_toolkit(
            &|_| None,
            &[d.path().join("venv/bin")],
            std::slice::from_ref(&cuda),
        );
        assert_eq!(tk.nvcc, Some(cuda.join("bin/nvcc")));
        assert!(tk.ninja.is_some());
        assert!(sglang_preflight(&tk).is_ok());
        // An explicit CUDA_HOME (pip nvidia/cu13 dir, no nvcc) is trusted.
        let pip = d.path().join("site-packages/nvidia/cu13");
        std::fs::create_dir_all(&pip).unwrap();
        let p2 = pip.display().to_string();
        let tk = find_cuda_toolkit(
            &|k| (k == "CUDA_HOME").then(|| p2.clone()),
            &[d.path().join("venv/bin")],
            &[],
        );
        assert!(tk.explicit && sglang_preflight(&tk).is_ok());
        let mut s = spec(&py);
        s.env.insert("CUDA_HOME".into(), p2.clone());
        s.env.insert("PATH".into(), "/usr/bin".into());
        prepare_native(&mut s, EngineName::Sglang, &nvidia(), &d.path().join("t")).unwrap();
        assert_eq!(s.env["CUDA_HOME"], p2);
        assert!(s.env["PATH"].starts_with(&d.path().join("venv/bin").display().to_string()));
    }
}
