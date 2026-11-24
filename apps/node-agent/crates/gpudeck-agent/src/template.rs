//! Turn an [`EngineLaunch`] into a [`ProcSpec`]: render `engine.launch_template` placeholders
//! (`common.json#/$defs/EngineSpec`), expand `{{flags}}`, add engine-specific flags derived from
//! the `LaunchSpec`, set device visibility / library env, force engines onto loopback, and wrap
//! container runtimes in `docker run`.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use anyhow::{bail, Result};
use gpudeck_protocol::{DeviceAssignment, DeviceVendor, EngineName, EngineRuntime, JsonObject};
use serde_json::Value;

use crate::backend::{ContainerRun, EngineLaunch, ProcSpec};

/// Template argv that delegates the whole command line to the engine adapter.
pub const ADAPTER_PLACEHOLDER: &str = "{{adapter}}";

const HOST_FLAGS: &[&str] = &[
    "--host",
    "--bind",
    "--listen",
    "--hostname",
    "--address",
    "--http-host",
    "--server-host",
];

/// Expand `{"flag": value}` into argv (`true` = bare flag, `false`/`null` omitted, arrays repeat).
pub fn expand_flags(flags: &JsonObject) -> Vec<String> {
    let mut out = vec![];
    for (k, v) in flags {
        let key = flag_name(k);
        match v {
            Value::Bool(true) => out.push(key),
            Value::Bool(false) | Value::Null => {}
            Value::Array(items) => {
                for it in items {
                    match it {
                        Value::Bool(true) => out.push(key.clone()),
                        Value::Bool(false) | Value::Null => {}
                        other => {
                            out.push(key.clone());
                            out.push(scalar(other));
                        }
                    }
                }
            }
            other => {
                out.push(key);
                out.push(scalar(other));
            }
        }
    }
    out
}

fn flag_name(k: &str) -> String {
    if k.starts_with('-') {
        k.to_string()
    } else if k.chars().count() == 1 {
        format!("-{k}")
    } else {
        format!("--{k}")
    }
}

fn scalar(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

fn has_flag(args: &[String], names: &[&str]) -> bool {
    args.iter().any(|a| {
        names
            .iter()
            .any(|n| a == n || a.starts_with(&format!("{n}=")))
    })
}

/// Engine-specific flags derived from structured `LaunchSpec` fields when the operator did not
/// set them explicitly (`flags` always win).
pub fn derived_flags(l: &EngineLaunch, already: &[String]) -> Vec<String> {
    let mut out: Vec<String> = vec![];
    let launch = &l.launch;
    let mut push = |names: &[&str], vals: Vec<String>| {
        if !has_flag(already, names) {
            out.extend(vals);
        }
    };
    match l.engine.name {
        EngineName::LlamaCpp if l.router.is_none() => {
            if let Some(c) = launch.context_size {
                push(
                    &["-c", "--ctx-size"],
                    vec!["--ctx-size".into(), c.to_string()],
                );
            }
            if let Some(n) = launch.max_concurrency {
                push(
                    &["-np", "--parallel"],
                    vec!["--parallel".into(), n.to_string()],
                );
            }
            if let Some(name) = &l.served_model_name {
                push(&["-a", "--alias"], vec!["--alias".into(), name.clone()]);
            }
            if let Some(d) = &l.draft_model_path {
                push(
                    &["-md", "--model-draft"],
                    vec!["--model-draft".into(), d.display().to_string()],
                );
            }
            for cv in &launch.control_vectors {
                out.push("--control-vector-scaled".into());
                out.push(cv.path.clone());
                out.push(cv.scale.to_string());
                if let Some([a, b]) = cv.layer_range {
                    out.push("--control-vector-layer-range".into());
                    out.push(a.to_string());
                    out.push(b.to_string());
                }
            }
        }
        EngineName::Vllm | EngineName::VllmOmni => {
            if let Some(n) = launch.max_concurrency {
                push(
                    &["--max-num-seqs"],
                    vec!["--max-num-seqs".into(), n.to_string()],
                );
            }
            if let Some(p) = &launch.tool_parser {
                push(
                    &["--tool-call-parser"],
                    vec![
                        "--enable-auto-tool-choice".into(),
                        "--tool-call-parser".into(),
                        p.clone(),
                    ],
                );
            }
        }
        EngineName::Sglang | EngineName::SglangDiffusion => {
            if let Some(n) = launch.max_concurrency {
                push(
                    &["--max-running-requests"],
                    vec!["--max-running-requests".into(), n.to_string()],
                );
            }
            if let Some(p) = &launch.tool_parser {
                push(
                    &["--tool-call-parser"],
                    vec!["--tool-call-parser".into(), p.clone()],
                );
            }
        }
        _ => {}
    }
    out
}

/// Vendor device-visibility environment for an assignment.
pub fn device_env(devices: &[DeviceAssignment]) -> BTreeMap<String, String> {
    let mut env = BTreeMap::new();
    let Some(first) = devices.first() else {
        return env;
    };
    let idx = devices
        .iter()
        .map(|d| d.index.to_string())
        .collect::<Vec<_>>()
        .join(",");
    let keys: &[&str] = match first.vendor {
        DeviceVendor::Nvidia => &["CUDA_VISIBLE_DEVICES"],
        DeviceVendor::Amd | DeviceVendor::Hygon => &["HIP_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES"],
        DeviceVendor::Metax => &["CUDA_VISIBLE_DEVICES", "MACA_VISIBLE_DEVICES"],
        DeviceVendor::Moorethreads => &["MUSA_VISIBLE_DEVICES"],
        DeviceVendor::Thead => &["CUDA_VISIBLE_DEVICES"],
        DeviceVendor::Apple => &[],
    };
    for k in keys {
        env.insert(k.to_string(), idx.clone());
    }
    env
}

fn is_loopback(host: &str) -> bool {
    let h = host.trim_matches(|c| c == '[' || c == ']');
    h == "localhost"
        || h.parse::<std::net::IpAddr>()
            .map(|ip| ip.is_loopback())
            .unwrap_or(false)
}

/// Force every host/bind flag to `bind`; returns the rewrites made.
pub fn enforce_bind(args: &mut [String], bind: &str) -> Vec<String> {
    let mut changes = vec![];
    let mut i = 0;
    while i < args.len() {
        let a = args[i].clone();
        if let Some((k, v)) = a.split_once('=') {
            if HOST_FLAGS.contains(&k) && v != bind && !(bind == "127.0.0.1" && is_loopback(v)) {
                changes.push(format!("{k}={v} -> {bind}"));
                args[i] = format!("{k}={bind}");
            }
        } else if HOST_FLAGS.contains(&a.as_str()) && i + 1 < args.len() {
            let v = args[i + 1].clone();
            if v != bind && !(bind == "127.0.0.1" && is_loopback(&v)) {
                changes.push(format!("{a} {v} -> {bind}"));
                args[i + 1] = bind.to_string();
            }
            i += 1;
        }
        i += 1;
    }
    changes
}

fn placeholder_values(l: &EngineLaunch, host: &str) -> BTreeMap<&'static str, Option<String>> {
    let p = |x: &Option<PathBuf>| x.as_ref().map(|p| p.display().to_string());
    let mut m = BTreeMap::new();
    m.insert("port", Some(l.port.to_string()));
    m.insert("host", Some(host.to_string()));
    m.insert("model_path", p(&l.model_path));
    m.insert(
        "model_dir",
        p(&l.model_dir).or_else(|| {
            l.model_path
                .as_ref()
                .and_then(|m| m.parent())
                .map(|d| d.display().to_string())
        }),
    );
    m.insert("context_size", l.launch.context_size.map(|c| c.to_string()));
    m.insert("served_model_name", l.served_model_name.clone());
    m.insert("tp_size", Some(l.launch.tp_size.to_string()));
    m.insert("pp_size", Some(l.launch.pp_size.to_string()));
    m.insert(
        "device_indices",
        Some(
            l.devices
                .iter()
                .map(|d| d.index.to_string())
                .collect::<Vec<_>>()
                .join(","),
        ),
    );
    m.insert("cache_dir", Some(l.cache_dir.display().to_string()));
    m.insert("draft_model_path", p(&l.draft_model_path));
    m.insert(
        "max_concurrency",
        l.launch.max_concurrency.map(|c| c.to_string()),
    );
    let d = l.launch.distributed.as_ref();
    m.insert("rank", d.map(|d| d.rank.to_string()));
    m.insert("world_size", d.map(|d| d.world_size.to_string()));
    m.insert("master_addr", d.map(|d| d.master_addr.clone()));
    m.insert("master_port", d.map(|d| d.master_port.to_string()));
    m.insert(
        "rpc_peers",
        d.map(|d| crate::distributed::rpc_peers(d).join(","))
            .filter(|s| !s.is_empty()),
    );
    m
}

fn render_one(s: &str, vals: &BTreeMap<&'static str, Option<String>>) -> Option<String> {
    let mut out = s.to_string();
    for (k, v) in vals {
        let ph = format!("{{{{{k}}}}}");
        if out.contains(&ph) {
            out = out.replace(&ph, v.as_ref()?);
        }
    }
    Some(out)
}

/// Render the template argv. A whole-element placeholder with no value is dropped together with
/// the flag right before it (`--max-model-len {{context_size}}`).
pub fn render_args(template: &[String], l: &EngineLaunch, host: &str) -> Result<Vec<String>> {
    let vals = placeholder_values(l, host);
    let mut flags = expand_flags(&l.launch.flags);
    let derived = derived_flags(l, &[template.to_vec(), flags.clone()].concat());
    flags.extend(derived);
    let mut out: Vec<String> = vec![];
    let mut flags_used = false;
    for t in template {
        if t == "{{flags}}" {
            out.extend(flags.iter().cloned());
            flags_used = true;
            continue;
        }
        match render_one(t, &vals) {
            Some(v) => out.push(v),
            None => {
                if out.last().is_some_and(|prev| prev.starts_with('-')) && !t.starts_with('-') {
                    out.pop();
                }
            }
        }
    }
    if !flags_used {
        out.extend(flags);
    }
    if out.is_empty() {
        bail!("launch template rendered to an empty command");
    }
    Ok(out)
}

/// Resolve an executable: absolute path as-is, configured engine path, then `PATH` and the
/// well-known locations of preinstalled engines.
pub fn resolve_executable(program: &str, configured: Option<&str>) -> Option<PathBuf> {
    if let Some(c) = configured {
        let p = expand_home(c);
        if p.is_file() {
            return Some(p);
        }
    }
    let p = expand_home(program);
    if p.is_absolute() {
        return p.is_file().then_some(p);
    }
    if let Some(found) = crate::system::which(program) {
        return Some(found);
    }
    let home = std::env::var_os("HOME").map(PathBuf::from)?;
    let candidates = [
        home.join(".unsloth/llama.cpp/build/bin").join(program),
        home.join(".venv/bin").join(program),
        PathBuf::from("/opt/homebrew/bin").join(program),
        PathBuf::from("/usr/local/bin").join(program),
    ];
    candidates.into_iter().find(|c| c.is_file())
}

pub fn expand_home(p: &str) -> PathBuf {
    if let Some(rest) = p.strip_prefix("~/") {
        if let Some(h) = std::env::var_os("HOME") {
            return PathBuf::from(h).join(rest);
        }
    }
    PathBuf::from(p)
}

/// The shared-library directory next to a native engine binary (llama.cpp ships its dylibs there).
pub fn library_dir_for(exe: &Path) -> Option<PathBuf> {
    let dir = exe.parent()?;
    let has_libs = std::fs::read_dir(dir).ok()?.flatten().any(|e| {
        let n = e.file_name().to_string_lossy().to_string();
        n.starts_with("lib") && (n.ends_with(".dylib") || n.contains(".so"))
    });
    has_libs.then(|| dir.to_path_buf())
}

/// The template command for this launch. A llama.cpp router-mode template (`--models-dir
/// {{model_dir}}`) launched for one model outside a router (cross-node pipeline parallel head,
/// which needs `--rpc` on the process that loads the model) becomes a single-model server:
/// `--models-dir <dir>` is replaced by `--model {{model_path}}`.
pub fn dedicated_command(l: &EngineLaunch) -> Vec<String> {
    let cmd = &l.engine.launch_template.command;
    if l.engine.name != EngineName::LlamaCpp
        || !l.engine.launch_template.router_mode
        || l.router.is_some()
        || l.model_path.is_none()
    {
        return cmd.clone();
    }
    let mut out = Vec::with_capacity(cmd.len());
    let mut it = cmd.iter();
    while let Some(word) = it.next() {
        if word == "--models-dir" {
            it.next();
            out.push("--model".into());
            out.push("{{model_path}}".into());
        } else if let Some(_dir) = word.strip_prefix("--models-dir=") {
            out.push("--model={{model_path}}".into());
        } else {
            out.push(word.clone());
        }
    }
    out
}

/// Writable cache dirs inside engine containers (their image layer; the mounts are read-only).
pub const CONTAINER_HF_MODULES: &str = "/tmp/gpudeck/hf_modules";
pub const CONTAINER_HF_ASSETS: &str = "/tmp/gpudeck/hf_assets";

/// Build the native or container process for a launch from its template.
pub fn build_proc(
    l: &EngineLaunch,
    configured_exe: Option<&str>,
    container_runtime: Option<&str>,
) -> Result<ProcSpec> {
    let tpl = &l.engine.launch_template;
    let container = l.engine.runtime == EngineRuntime::Container;
    // Engines bind loopback natively; inside a container (macOS port publish) they must bind
    // all container interfaces while the host side is published on 127.0.0.1 only.
    let linux = cfg!(target_os = "linux");
    let inner_host = if container && !linux {
        "0.0.0.0"
    } else {
        "127.0.0.1"
    };
    let mut args = render_args(&dedicated_command(l), l, inner_host)?;
    let rewrites = enforce_bind(&mut args, inner_host);
    if !rewrites.is_empty() {
        tracing::warn!(?rewrites, "engine bind address forced to {inner_host} (engines never listen on a network interface)");
    }
    if let Some(r) = &l.router {
        if !has_flag(&args, &["--models-preset"]) {
            args.push("--models-preset".into());
            args.push(r.preset_path.display().to_string());
        }
        if !has_flag(&args, &["--no-models-autoload", "--models-autoload"]) {
            args.push("--no-models-autoload".into());
        }
    }
    let mut env: BTreeMap<String, String> = BTreeMap::new();
    let vals = placeholder_values(l, inner_host);
    for (k, v) in &tpl.env {
        if let Some(v) = render_one(v, &vals) {
            env.insert(k.clone(), v);
        }
    }
    for (k, v) in device_env(&l.devices) {
        env.entry(k).or_insert(v);
    }
    for (k, v) in &l.launch.env {
        env.insert(k.clone(), v.clone());
    }
    if matches!(
        l.engine.name,
        EngineName::Vllm
            | EngineName::VllmOmni
            | EngineName::Sglang
            | EngineName::SglangDiffusion
            | EngineName::Mlx
            | EngineName::MlxAudio
            | EngineName::TensorrtLlm
    ) {
        env.entry("HF_HOME".into())
            .or_insert_with(|| l.cache_dir.join("hf").display().to_string());
        env.entry("HF_HUB_OFFLINE".into())
            .or_insert_with(|| "1".into());
        if container {
            // The cache (HF_HOME) and the weights are mounted read-only; transformers writes
            // remote-code modules to $HF_HOME/modules (TRT-LLM: "Read-only file system") and
            // huggingface_hub assets next to it: point both at the container's writable layer.
            env.entry("HF_MODULES_CACHE".into())
                .or_insert_with(|| CONTAINER_HF_MODULES.into());
            env.entry("HF_ASSETS_CACHE".into())
                .or_insert_with(|| CONTAINER_HF_ASSETS.into());
        }
        if !container {
            // huggingface_hub raises CacheNotFound on every `GET /v1/models` (mlx_lm.server
            // scans the hub cache) when the directory does not exist yet.
            if let Some(home) = env.get("HF_HOME") {
                let _ = std::fs::create_dir_all(Path::new(home).join("hub"));
            }
        }
    }
    let cwd = tpl.working_dir.as_ref().map(PathBuf::from);

    if container {
        let image =
            l.engine.image.clone().ok_or_else(|| {
                anyhow::anyhow!("container engine {} has no image", l.engine.name)
            })?;
        let runtime = container_runtime.unwrap_or("docker").to_string();
        let id = l
            .instance_id
            .or(l.engine_process_id)
            .map(|u| u.to_string())
            .unwrap_or_else(|| l.port.to_string());
        let name = format!("gpudeck-{id}");
        let mut run = vec![
            "run".to_string(),
            "--rm".into(),
            "--name".into(),
            name.clone(),
            "--label".into(),
            "gpudeck.managed=true".into(),
        ];
        if linux {
            run.extend(["--network".into(), "host".into()]);
        } else {
            run.extend(["-p".into(), format!("127.0.0.1:{}:{}", l.port, l.port)]);
        }
        match l.devices.first().map(|d| d.vendor) {
            Some(DeviceVendor::Nvidia) => run.extend([
                "--gpus".into(),
                format!(
                    "\"device={}\"",
                    l.devices
                        .iter()
                        .map(|d| d.index.to_string())
                        .collect::<Vec<_>>()
                        .join(",")
                ),
            ]),
            Some(DeviceVendor::Amd | DeviceVendor::Hygon) => run.extend([
                "--device".into(),
                "/dev/kfd".into(),
                "--device".into(),
                "/dev/dri".into(),
                "--group-add".into(),
                "video".into(),
            ]),
            Some(DeviceVendor::Moorethreads) => run.extend(["--device".into(), "/dev/dri".into()]),
            _ => {}
        }
        run.extend([
            "--ipc".into(),
            "host".into(),
            "--shm-size".into(),
            "16g".into(),
        ]);
        for dir in [
            // llama.cpp router: preset INI + models dir live in the router's state folder.
            l.router
                .as_ref()
                .and_then(|r| r.preset_path.parent().map(Path::to_path_buf)),
            l.model_dir.clone(),
            l.model_path
                .as_ref()
                .and_then(|p| p.parent().map(Path::to_path_buf)),
            Some(l.cache_dir.clone()),
        ]
        .into_iter()
        .flatten()
        {
            let d = dir.display().to_string();
            let vol = format!("{d}:{d}:ro");
            if !run.contains(&vol) {
                run.extend(["-v".into(), vol]);
            }
        }
        for (k, v) in &env {
            run.extend(["-e".into(), format!("{k}={v}")]);
        }
        if let Some(c) = &cwd {
            run.extend(["-w".into(), c.display().to_string()]);
        }
        // The template's first word is the program inside the image: run it as the entrypoint
        // so the command does not depend on the image's own ENTRYPOINT (vllm/vllm-openai is
        // `vllm serve`, NGC images wrap everything in nvidia_entrypoint.sh).
        let mut words = args.into_iter();
        if let Some(program) = words.next() {
            run.extend(["--entrypoint".into(), program]);
        }
        run.push(image);
        run.extend(words);
        return Ok(ProcSpec {
            program: runtime.clone(),
            args: run,
            env: BTreeMap::new(),
            cwd: None,
            container: Some(ContainerRun { runtime, name }),
        });
    }

    let program = args.remove(0);
    let exe = l
        .executable
        .clone()
        .or_else(|| resolve_executable(&program, configured_exe))
        .ok_or_else(|| {
            anyhow::anyhow!(
                "engine executable {program:?} not found (set engine_paths.{} in agent.toml)",
                l.engine.name
            )
        })?;
    if let Some(lib) = l.library_dir.clone().or_else(|| library_dir_for(&exe)) {
        let key = if cfg!(target_os = "macos") {
            "DYLD_LIBRARY_PATH"
        } else {
            "LD_LIBRARY_PATH"
        };
        let joined = match env.get(key) {
            Some(prev) => format!("{}:{prev}", lib.display()),
            None => lib.display().to_string(),
        };
        env.insert(key.to_string(), joined);
    }
    Ok(ProcSpec {
        program: exe.display().to_string(),
        args,
        env,
        cwd,
        container: None,
    })
}

/// llama.cpp router preset INI (`[name]` sections, keys = long flag names without dashes).
pub fn router_preset_ini(entries: &[(String, BTreeMap<String, String>)]) -> String {
    let mut out =
        String::from("; managed by gpudeck-agent; one section per model loaded into this router\n");
    for (name, kv) in entries {
        out.push_str(&format!("\n[{name}]\n"));
        for (k, v) in kv {
            out.push_str(&format!("{k} = {v}\n"));
        }
    }
    out
}

/// Router preset: model name → INI keys.
pub type RouterPreset = BTreeMap<String, BTreeMap<String, String>>;

/// Read back a preset written by [`router_preset_ini`] (re-adopting a running router).
pub fn parse_router_preset_ini(text: &str) -> RouterPreset {
    let mut out = RouterPreset::new();
    let mut cur: Option<String> = None;
    for line in text.lines() {
        let l = line.trim();
        if l.is_empty() || l.starts_with(';') || l.starts_with('#') {
            continue;
        }
        if let Some(name) = l.strip_prefix('[').and_then(|x| x.strip_suffix(']')) {
            out.entry(name.to_string()).or_default();
            cur = Some(name.to_string());
        } else if let (Some(c), Some((k, v))) = (&cur, l.split_once('=')) {
            out.entry(c.clone())
                .or_default()
                .insert(k.trim().to_string(), v.trim().to_string());
        }
    }
    out
}

/// Per-model router preset keys for an instance (`model`, `ctx-size`, `parallel`, flags...).
pub fn router_entry(l: &EngineLaunch) -> BTreeMap<String, String> {
    let mut kv = BTreeMap::new();
    if let Some(m) = &l.model_path {
        kv.insert("model".into(), m.display().to_string());
    }
    if let Some(c) = l.launch.context_size {
        kv.insert("ctx-size".into(), c.to_string());
    }
    if let Some(n) = l.launch.max_concurrency {
        kv.insert("parallel".into(), n.to_string());
    }
    if let Some(d) = &l.draft_model_path {
        kv.insert("model-draft".into(), d.display().to_string());
    }
    if let Some(cv) = l.launch.control_vectors.first() {
        kv.insert(
            "control-vector-scaled".into(),
            format!("{} {}", cv.path, cv.scale),
        );
        if let Some([a, b]) = cv.layer_range {
            kv.insert("control-vector-layer-range".into(), format!("{a} {b}"));
        }
    }
    for (k, v) in &l.launch.flags {
        let key = k.trim_start_matches('-').to_string();
        if HOST_FLAGS.contains(&format!("--{key}").as_str()) || key == "port" {
            continue;
        }
        match v {
            Value::Bool(true) => {
                kv.insert(key, "true".into());
            }
            Value::Bool(false) | Value::Null => {}
            other => {
                kv.insert(key, scalar(other));
            }
        }
    }
    // Each router model runs as its own llama-server child: embedding / rerank models need the
    // pooling endpoint switched on (chat template flags do not apply to them).
    match l.model.as_ref().map(|m| m.model_type) {
        Some(gpudeck_protocol::ModelType::Embedding) => {
            kv.insert("embeddings".into(), "true".into());
            kv.remove("jinja");
        }
        Some(gpudeck_protocol::ModelType::Rerank) => {
            kv.insert("rerank".into(), "true".into());
            kv.remove("jinja");
        }
        _ => {}
    }
    kv
}

#[cfg(test)]
mod tests {
    use super::*;
    use gpudeck_protocol::{EngineSpec, HealthConfig, LaunchSpec, LaunchTemplate, MetricsConfig};
    use uuid::Uuid;

    pub fn launch(name: EngineName, cmd: &[&str]) -> EngineLaunch {
        EngineLaunch {
            engine: EngineSpec {
                engine_version_id: Uuid::new_v4(),
                name,
                version: "1".into(),
                runtime: EngineRuntime::Native,
                image: None,
                artifact: None,
                launch_template: LaunchTemplate {
                    command: cmd.iter().map(|s| s.to_string()).collect(),
                    env: Default::default(),
                    working_dir: None,
                    router_mode: false,
                },
                health: HealthConfig::default(),
                metrics: MetricsConfig::default(),
                port_range: [10240, 10340],
                capabilities: Default::default(),
            },
            launch: LaunchSpec::default(),
            model: None,
            served_model_name: Some("qwen3-0.6b".into()),
            model_path: Some("/m/model.gguf".into()),
            model_paths: vec![],
            model_dir: None,
            draft_model_path: None,
            port: 10241,
            devices: vec![],
            executable: Some("/bin/echo".into()),
            library_dir: None,
            cache_dir: "/c".into(),
            router: None,
            instance_id: Some(Uuid::nil()),
            engine_process_id: None,
        }
    }

    #[test]
    fn flags_expand() {
        let flags: JsonObject = serde_json::from_value(serde_json::json!({"jinja": true, "no-x": false, "n-gpu-layers": 99, "c": 4096, "--lora": ["a", "b"], "flash-attn": "auto"})).unwrap();
        let v = expand_flags(&flags);
        assert_eq!(
            v,
            [
                "--lora",
                "a",
                "--lora",
                "b",
                "-c",
                "4096",
                "--flash-attn",
                "auto",
                "--jinja",
                "--n-gpu-layers",
                "99"
            ]
        );
    }

    #[test]
    fn renders_template_and_drops_missing() {
        let mut l = launch(
            EngineName::Vllm,
            &[
                "python3",
                "-m",
                "vllm",
                "--host",
                "0.0.0.0",
                "--port",
                "{{port}}",
                "--model",
                "{{model_dir}}",
                "--max-model-len",
                "{{context_size}}",
                "{{flags}}",
            ],
        );
        l.launch.flags =
            serde_json::from_value(serde_json::json!({"enable-prefix-caching": true})).unwrap();
        l.launch.max_concurrency = Some(8);
        let p = build_proc(&l, None, None).unwrap();
        assert_eq!(p.program, "/bin/echo");
        assert_eq!(
            p.args,
            [
                "-m",
                "vllm",
                "--host",
                "127.0.0.1",
                "--port",
                "10241",
                "--model",
                "/m",
                "--enable-prefix-caching",
                "--max-num-seqs",
                "8"
            ]
        );
        assert_eq!(p.env.get("HF_HUB_OFFLINE").map(String::as_str), Some("1"));
    }

    #[test]
    fn llama_dedicated_and_router() {
        let mut l = launch(
            EngineName::LlamaCpp,
            &[
                "llama-server",
                "--host=0.0.0.0",
                "--port",
                "{{port}}",
                "-m",
                "{{model_path}}",
                "{{flags}}",
            ],
        );
        l.launch.context_size = Some(4096);
        l.launch.control_vectors = vec![gpudeck_protocol::ControlVector {
            path: "/cv.gguf".into(),
            scale: 0.5,
            layer_range: Some([1, 9]),
        }];
        let p = build_proc(&l, None, None).unwrap();
        let s = p.args.join(" ");
        assert!(
            s.starts_with(
                "--host=127.0.0.1 --port 10241 -m /m/model.gguf --ctx-size 4096 --alias qwen3-0.6b"
            ),
            "{s}"
        );
        assert!(s.contains("--control-vector-scaled /cv.gguf 0.5 --control-vector-layer-range 1 9"));
        let mut r = launch(
            EngineName::LlamaCpp,
            &[
                "llama-server",
                "--port",
                "{{port}}",
                "--models-dir",
                "{{model_dir}}",
                "{{flags}}",
            ],
        );
        r.model_dir = Some("/r/models".into());
        r.router = Some(crate::backend::RouterFiles {
            models_dir: "/r/models".into(),
            preset_path: "/r/preset.ini".into(),
        });
        let p = build_proc(&r, None, None).unwrap();
        assert_eq!(
            p.args,
            [
                "--port",
                "10241",
                "--models-dir",
                "/r/models",
                "--models-preset",
                "/r/preset.ini",
                "--no-models-autoload"
            ]
        );
        let entry = router_entry(&l);
        assert_eq!(entry["ctx-size"], "4096");
        assert_eq!(entry["model"], "/m/model.gguf");
        let ini = router_preset_ini(&[("qwen3-0.6b".into(), entry)]);
        assert!(
            ini.contains("[qwen3-0.6b]\n")
                && ini.contains("\nctx-size = 4096\n")
                && ini.contains("control-vector-scaled = /cv.gguf 0.5\n")
        );
    }

    #[test]
    fn router_entry_embedding_model_enables_pooling() {
        let mut l = launch(EngineName::LlamaCpp, &["llama-server", "{{flags}}"]);
        l.launch.flags.insert("jinja".into(), Value::Bool(true));
        l.model = Some(
            serde_json::from_value(serde_json::json!({
                "model_id": Uuid::nil(), "model_version_id": Uuid::nil(), "name": "nomic-embed",
                "served_model_name": "nomic-embed", "type": "embedding", "format": "gguf",
                "files": [], "cache_dir": "/c"
            }))
            .unwrap(),
        );
        let entry = router_entry(&l);
        assert_eq!(entry.get("embeddings").map(String::as_str), Some("true"));
        assert!(!entry.contains_key("jinja"));
    }

    #[test]
    fn router_preset_round_trip() {
        let mut a = BTreeMap::new();
        a.insert("model".to_string(), "/m/a.gguf".to_string());
        a.insert("ctx-size".to_string(), "4096".to_string());
        a.insert("override-kv".to_string(), "x=int:1".to_string());
        let entries = vec![
            ("qwen3-8b".to_string(), a),
            ("emb".to_string(), BTreeMap::new()),
        ];
        let back = parse_router_preset_ini(&router_preset_ini(&entries));
        assert_eq!(back.len(), 2);
        assert_eq!(back["qwen3-8b"], entries[0].1);
        assert!(back["emb"].is_empty());
    }

    #[test]
    fn container_wrap() {
        let mut l = launch(
            EngineName::Vllm,
            &[
                "vllm",
                "serve",
                "{{model_dir}}",
                "--port",
                "{{port}}",
                "--host",
                "127.0.0.1",
            ],
        );
        l.engine.runtime = EngineRuntime::Container;
        l.engine.image = Some("vllm/vllm-openai:v0.11.0".into());
        l.devices = vec![DeviceAssignment {
            device_id: Uuid::nil(),
            index: 1,
            vendor: DeviceVendor::Nvidia,
            slice: None,
        }];
        let p = build_proc(&l, None, Some("docker")).unwrap();
        assert_eq!(p.program, "docker");
        let s = p.args.join(" ");
        assert!(s.contains("--gpus \"device=1\""), "{s}");
        assert!(s.contains("-e CUDA_VISIBLE_DEVICES=1"));
        // Read-only cache + weights, writable module / asset caches inside the container.
        assert!(
            s.contains(&format!("-e HF_MODULES_CACHE={CONTAINER_HF_MODULES}")),
            "{s}"
        );
        assert!(
            s.contains(&format!("-e HF_ASSETS_CACHE={CONTAINER_HF_ASSETS}")),
            "{s}"
        );
        assert!(s.contains("-e HF_HOME=/c/hf"), "{s}");
        assert!(s.contains("/c:/c:ro") && s.contains("/m:/m:ro"), "{s}");
        assert!(
            s.contains("--entrypoint vllm vllm/vllm-openai:v0.11.0 serve /m --port 10241"),
            "{s}"
        );
        if cfg!(target_os = "linux") {
            assert!(s.contains("--network host") && s.ends_with("--host 127.0.0.1"));
        } else {
            assert!(s.contains("-p 127.0.0.1:10241:10241") && s.ends_with("--host 0.0.0.0"));
        }
    }

    #[test]
    fn bind_enforcement() {
        let mut a: Vec<String> = ["--host", "::1", "--listen=10.0.0.1", "--bind", "0.0.0.0"]
            .iter()
            .map(|s| s.to_string())
            .collect();
        let changes = enforce_bind(&mut a, "127.0.0.1");
        assert_eq!(
            a,
            ["--host", "::1", "--listen=127.0.0.1", "--bind", "127.0.0.1"]
        );
        assert_eq!(changes.len(), 2);
    }

    #[test]
    fn device_visibility_env() {
        let d = |v, i| DeviceAssignment {
            device_id: Uuid::nil(),
            index: i,
            vendor: v,
            slice: None,
        };
        assert_eq!(
            device_env(&[d(DeviceVendor::Amd, 0), d(DeviceVendor::Amd, 2)])["HIP_VISIBLE_DEVICES"],
            "0,2"
        );
        assert!(device_env(&[d(DeviceVendor::Apple, 0)]).is_empty());
    }
}
