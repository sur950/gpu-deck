//! Node-side file commands (`protocol.md` §11): `scan_path` lists importable model candidates
//! under a path (Ollama stores, LM Studio, Hugging Face caches, MLX directories, plain GGUF /
//! safetensors / pickle) without writing anything, and `hash_files` computes sha256 of files with
//! streaming progress. Mirrors the control plane's `app/services/catalog/local_scanner.py` so a
//! node scan and a control-plane scan of the same tree give the same candidates.

use std::collections::{BTreeMap, HashSet};
use std::fs::File;
use std::io::{BufReader, Read, Seek, SeekFrom};
use std::path::{Component, Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use gpudeck_protocol::{
    error_code, ErrorInfo, HashFilesArgs, HashFilesResult, HashedFile, JsonObject, ModelFileFormat,
    ScanCandidate, ScanFile, ScanKind, ScanPathArgs, ScanPathResult,
};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use tokio_util::sync::CancellationToken;

use crate::commands::Ctx;

const OLLAMA_MODEL_MEDIA_TYPE: &str = "application/vnd.ollama.image.model";
const OLLAMA_PROJECTOR_MEDIA_TYPE: &str = "application/vnd.ollama.image.projector";
const SKIP_DIRS: &[&str] = &[".git", "__pycache__", "node_modules", ".cache", "blobs"];
const PICKLE_SUFFIXES: &[&str] = &[".bin", ".pt", ".pth", ".ckpt", ".pkl", ".pickle"];
const IGNORED_NAMES: &[&str] = &[
    ".gitattributes",
    "README.md",
    "LICENSE",
    "NOTICE",
    ".DS_Store",
];
const AUX_REQUIRED_NAMES: &[&str] = &[
    "config.json",
    "generation_config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "tokenizer.model",
    "special_tokens_map.json",
    "vocab.json",
    "merges.txt",
    "added_tokens.json",
    "preprocessor_config.json",
    "chat_template.jinja",
    "chat_template.json",
    "model.safetensors.index.json",
];
/// Config keys copied into `metadata` for directory candidates.
const CONFIG_KEYS: &[&str] = &[
    "model_type",
    "architectures",
    "max_position_embeddings",
    "num_hidden_layers",
    "num_attention_heads",
    "num_key_value_heads",
    "hidden_size",
    "head_dim",
    "vocab_size",
    "torch_dtype",
    "quantization",
    "quantization_config",
    "sliding_window",
    "rope_scaling",
];

fn err(code: &str, msg: impl Into<String>) -> ErrorInfo {
    ErrorInfo::new(code, msg)
}

/// `~`, `~/x` → home; anything else must be absolute.
pub fn expand_path(p: &str) -> Result<PathBuf, ErrorInfo> {
    let p = p.trim();
    let path = if p == "~" {
        std::env::var_os("HOME")
            .map(PathBuf::from)
            .ok_or_else(|| err(error_code::INVALID_ARGS, "HOME is not set"))?
    } else {
        crate::template::expand_home(p)
    };
    if !path.is_absolute() {
        return Err(err(
            error_code::INVALID_ARGS,
            format!("path must be absolute or start with ~/ (got {p:?})"),
        ));
    }
    Ok(path)
}

fn io_err(path: &Path, e: &std::io::Error) -> ErrorInfo {
    match e.kind() {
        std::io::ErrorKind::NotFound => err(
            error_code::PATH_NOT_FOUND,
            format!("{} does not exist", path.display()),
        ),
        std::io::ErrorKind::PermissionDenied => err(
            error_code::PERMISSION_DENIED,
            format!("{}: permission denied", path.display()),
        ),
        _ => err(error_code::INTERNAL, format!("{}: {e}", path.display())),
    }
}

// ---------------------------------------------------------------------------------- formats

pub fn detect_format(name: &str) -> ModelFileFormat {
    let l = name.to_ascii_lowercase();
    if l.ends_with(".gguf") {
        ModelFileFormat::Gguf
    } else if l.ends_with(".safetensors") {
        ModelFileFormat::Safetensors
    } else if PICKLE_SUFFIXES.iter().any(|s| l.ends_with(s)) {
        ModelFileFormat::Pickle
    } else {
        ModelFileFormat::Other
    }
}

pub fn is_required_file(name: &str, fmt: ModelFileFormat) -> bool {
    if IGNORED_NAMES.contains(&name) {
        return false;
    }
    if matches!(
        fmt,
        ModelFileFormat::Gguf | ModelFileFormat::Safetensors | ModelFileFormat::Pickle
    ) {
        return true;
    }
    if AUX_REQUIRED_NAMES.contains(&name) {
        return true;
    }
    [".json", ".jinja", ".txt", ".model", ".tiktoken"]
        .iter()
        .any(|s| name.ends_with(s))
        && !name.starts_with("README")
}

pub fn is_mlx_config(config: &JsonObject) -> bool {
    let q = config
        .get("quantization")
        .filter(|v| !v.is_null())
        .or_else(|| config.get("quantization_config"));
    match q.and_then(Value::as_object) {
        Some(q) => {
            q.contains_key("group_size")
                || q.get("quant_method").and_then(Value::as_str) == Some("mlx")
        }
        None => false,
    }
}

/// Candidate format from its files' formats (mlx wins when the config says so).
pub fn version_format(formats: &HashSet<ModelFileFormat>, is_mlx: bool) -> ModelFileFormat {
    if is_mlx && formats.contains(&ModelFileFormat::Safetensors) {
        return ModelFileFormat::Mlx;
    }
    for f in [
        ModelFileFormat::Gguf,
        ModelFileFormat::Safetensors,
        ModelFileFormat::Mlx,
        ModelFileFormat::Pickle,
    ] {
        if formats.contains(&f) {
            return f;
        }
    }
    ModelFileFormat::Other
}

/// `-00001-of-00003.gguf` → (`stem`, index).
fn shard_group(name: &str) -> Option<(String, u32)> {
    let lower = name.to_ascii_lowercase();
    let ext = if lower.ends_with(".gguf") {
        ".gguf"
    } else if lower.ends_with(".safetensors") {
        ".safetensors"
    } else {
        return None;
    };
    let stem = &name[..name.len() - ext.len()];
    let (head, tail) = stem.rsplit_once("-of-")?;
    let (base, idx) = head.rsplit_once('-')?;
    if tail.len() == 5
        && idx.len() == 5
        && tail.chars().all(|c| c.is_ascii_digit())
        && idx.chars().all(|c| c.is_ascii_digit())
    {
        return Some((base.to_string(), idx.parse().ok()?));
    }
    None
}

/// llama.cpp `LLAMA_FTYPE` → quantization label.
pub fn ftype_name(t: u64) -> Option<&'static str> {
    Some(match t {
        0 => "F32",
        1 => "F16",
        2 => "Q4_0",
        3 => "Q4_1",
        7 => "Q8_0",
        8 => "Q5_0",
        9 => "Q5_1",
        10 => "Q2_K",
        11 => "Q3_K_S",
        12 => "Q3_K_M",
        13 => "Q3_K_L",
        14 => "Q4_K_S",
        15 => "Q4_K_M",
        16 => "Q5_K_S",
        17 => "Q5_K_M",
        18 => "Q6_K",
        19 => "IQ2_XXS",
        20 => "IQ2_XS",
        21 => "Q2_K_S",
        22 => "IQ3_XS",
        23 => "IQ3_XXS",
        24 => "IQ1_S",
        25 => "IQ4_NL",
        26 => "IQ3_S",
        27 => "IQ3_M",
        28 => "IQ2_S",
        29 => "IQ2_M",
        30 => "IQ4_XS",
        31 => "IQ1_M",
        32 => "BF16",
        36 => "TQ1_0",
        37 => "TQ2_0",
        38 => "MXFP4_MOE",
        _ => return None,
    })
}

const QUANT_LABELS: &[&str] = &[
    "IQ2_XXS", "IQ3_XXS", "Q3_K_XL", "Q4_K_XL", "Q5_K_XL", "Q6_K_XL", "Q2_K_XL", "Q8_K_XL",
    "IQ1_S", "IQ1_M", "IQ2_XS", "IQ2_S", "IQ2_M", "IQ3_XS", "IQ3_S", "IQ3_M", "IQ4_NL", "IQ4_XS",
    "Q2_K_S", "Q2_K", "Q3_K_S", "Q3_K_M", "Q3_K_L", "Q4_K_S", "Q4_K_M", "Q5_K_S", "Q5_K_M", "Q6_K",
    "Q4_0", "Q4_1", "Q5_0", "Q5_1", "Q8_0", "MXFP4", "BF16", "F16", "FP16", "F32", "FP8", "AWQ",
    "GPTQ",
];

/// Quantization label from a file / directory name (`Qwen3-0.6B-Q4_K_M.gguf`, `...-4bit`).
pub fn quant_from_name(name: &str) -> Option<String> {
    let up = name.to_ascii_uppercase().replace('.', "_");
    for q in QUANT_LABELS {
        if let Some(pos) = up.find(q) {
            let before = up[..pos].chars().last();
            let after = up[pos + q.len()..].chars().next();
            let boundary = |c: Option<char>| c.is_none_or(|c| !c.is_ascii_alphanumeric());
            if boundary(before) && boundary(after) {
                return Some(q.to_string());
            }
        }
    }
    let lower = name.to_ascii_lowercase();
    for bits in ["2bit", "3bit", "4bit", "5bit", "6bit", "8bit"] {
        if lower.contains(bits) {
            return Some(bits.to_string());
        }
    }
    None
}

/// `Qwen3-0.6B-Q4_K_M` → `qwen3-0.6b-q4_k_m` (the control plane refines it).
pub fn suggested_name(hint: &str) -> String {
    let base = hint.rsplit('/').next().unwrap_or(hint);
    let base = base.strip_suffix(".gguf").unwrap_or(base);
    base.trim().to_ascii_lowercase().replace([' ', '/'], "-")
}

// ---------------------------------------------------------------------------------- GGUF

/// Scalar header keys of a GGUF file (arrays skipped; strings capped at 256 chars).
pub fn read_gguf_metadata(path: &Path) -> std::io::Result<JsonObject> {
    let f = File::open(path)?;
    let mut r = BufReader::with_capacity(1 << 16, f);
    let mut magic = [0u8; 4];
    r.read_exact(&mut magic)?;
    if &magic != b"GGUF" {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            "not a GGUF file",
        ));
    }
    let version = read_u32(&mut r)?;
    let (_tensors, kv_count) = if version == 1 {
        (read_u32(&mut r)? as u64, read_u32(&mut r)? as u64)
    } else {
        (read_u64(&mut r)?, read_u64(&mut r)?)
    };
    let mut out = JsonObject::new();
    out.insert("gguf.version".into(), json!(version));
    for _ in 0..kv_count.min(100_000) {
        let key = read_str(&mut r, version, 4096)?;
        let ty = read_u32(&mut r)?;
        match read_value(&mut r, ty, version)? {
            Some(v) if !key.starts_with("tokenizer.") => {
                out.insert(key, v);
            }
            _ => {}
        }
        if out.len() >= 128 {
            break;
        }
    }
    Ok(out)
}

fn read_u32<R: Read>(r: &mut R) -> std::io::Result<u32> {
    let mut b = [0u8; 4];
    r.read_exact(&mut b)?;
    Ok(u32::from_le_bytes(b))
}

fn read_u64<R: Read>(r: &mut R) -> std::io::Result<u64> {
    let mut b = [0u8; 8];
    r.read_exact(&mut b)?;
    Ok(u64::from_le_bytes(b))
}

fn read_len<R: Read>(r: &mut R, version: u32) -> std::io::Result<u64> {
    if version == 1 {
        Ok(read_u32(r)? as u64)
    } else {
        read_u64(r)
    }
}

fn read_str<R: Read + Seek>(r: &mut R, version: u32, cap: usize) -> std::io::Result<String> {
    let len = read_len(r, version)?;
    if len > 64 << 20 {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            "GGUF string too long",
        ));
    }
    let take = (len as usize).min(cap);
    let mut buf = vec![0u8; take];
    r.read_exact(&mut buf)?;
    if (len as usize) > take {
        r.seek(SeekFrom::Current((len as usize - take) as i64))?;
    }
    Ok(String::from_utf8_lossy(&buf).into_owned())
}

fn fixed_size(ty: u32) -> Option<i64> {
    Some(match ty {
        0 | 1 | 7 => 1,
        2 | 3 => 2,
        4..=6 => 4,
        10..=12 => 8,
        _ => return None,
    })
}

fn read_value<R: Read + Seek>(r: &mut R, ty: u32, version: u32) -> std::io::Result<Option<Value>> {
    let mut b8 = [0u8; 8];
    Ok(Some(match ty {
        0 => {
            r.read_exact(&mut b8[..1])?;
            json!(b8[0])
        }
        1 => {
            r.read_exact(&mut b8[..1])?;
            json!(b8[0] as i8)
        }
        2 => {
            r.read_exact(&mut b8[..2])?;
            json!(u16::from_le_bytes([b8[0], b8[1]]))
        }
        3 => {
            r.read_exact(&mut b8[..2])?;
            json!(i16::from_le_bytes([b8[0], b8[1]]))
        }
        4 => json!(read_u32(r)?),
        5 => json!(read_u32(r)? as i32),
        6 => json!(f32::from_bits(read_u32(r)?) as f64),
        7 => {
            r.read_exact(&mut b8[..1])?;
            json!(b8[0] != 0)
        }
        8 => json!(read_str(r, version, 256)?),
        9 => {
            let ity = read_u32(r)?;
            let n = read_len(r, version)?;
            match fixed_size(ity) {
                Some(sz) => {
                    r.seek(SeekFrom::Current(sz * n as i64))?;
                }
                None if ity == 8 => {
                    for _ in 0..n {
                        let l = read_len(r, version)?;
                        r.seek(SeekFrom::Current(l as i64))?;
                    }
                }
                None => {
                    for _ in 0..n {
                        read_value(r, ity, version)?;
                    }
                }
            }
            return Ok(None);
        }
        10 => json!(read_u64(r)?),
        11 => json!(read_u64(r)? as i64),
        12 => json!(f64::from_bits(read_u64(r)?)),
        other => {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                format!("unknown GGUF value type {other}"),
            ))
        }
    }))
}

fn is_gguf(path: &Path) -> bool {
    let mut m = [0u8; 4];
    File::open(path)
        .and_then(|mut f| f.read_exact(&mut m))
        .is_ok()
        && &m == b"GGUF"
}

// ---------------------------------------------------------------------------------- scanner

struct Scanner<'a> {
    max_depth: u32,
    max_entries: u64,
    read_metadata: bool,
    visited: u64,
    truncated: bool,
    cancel: &'a CancellationToken,
}

fn size_of(p: &Path) -> u64 {
    std::fs::metadata(p).map(|m| m.len()).unwrap_or(0)
}

fn real(p: &Path) -> PathBuf {
    std::fs::canonicalize(p).unwrap_or_else(|_| p.to_path_buf())
}

fn read_json_obj(p: &Path) -> JsonObject {
    std::fs::read_to_string(p)
        .ok()
        .and_then(|t| serde_json::from_str::<Value>(&t).ok())
        .and_then(|v| v.as_object().cloned())
        .unwrap_or_default()
}

fn subdirs(d: &Path) -> Vec<PathBuf> {
    let mut v: Vec<PathBuf> = std::fs::read_dir(d)
        .map(|rd| {
            rd.flatten()
                .filter(|e| e.file_type().map(|t| t.is_dir()).unwrap_or(false))
                .map(|e| e.path())
                .collect()
        })
        .unwrap_or_default();
    v.sort();
    v
}

fn name_of(p: &Path) -> String {
    p.file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default()
}

/// `models--org--name` → (`org`, `name`).
fn hf_cache_repo(name: &str) -> Option<(String, String)> {
    let rest = name.strip_prefix("models--")?;
    let (org, model) = rest.split_once("--")?;
    (!org.is_empty() && !model.is_empty() && !org.starts_with('-'))
        .then(|| (org.to_string(), model.to_string()))
}

impl Scanner<'_> {
    fn tick(&mut self) -> bool {
        if self.cancel.is_cancelled() {
            self.truncated = true;
            return false;
        }
        self.visited += 1;
        if self.visited > self.max_entries {
            self.truncated = true;
            return false;
        }
        true
    }

    fn scan(&mut self, root: &Path, recursive: bool) -> Vec<ScanCandidate> {
        if root.is_file() {
            return self.single_file(root).into_iter().collect();
        }
        let mut out = vec![];
        if let Some(oroot) = find_ollama_root(root) {
            out.extend(self.scan_ollama(&oroot, Some(root)));
            if oroot == root || matches!(name_of(root).as_str(), "manifests" | "blobs") {
                return dedupe(out);
            }
        }
        if let Some(lms) = find_lmstudio_root(root) {
            out.extend(self.scan_lmstudio(&lms));
            return dedupe(out);
        }
        out.extend(self.walk(root, 0, recursive));
        dedupe(out)
    }

    fn single_file(&mut self, file: &Path) -> Option<ScanCandidate> {
        let parent = file.parent()?;
        if let Some(oroot) = find_ollama_root(parent) {
            let manifests = oroot.join("manifests");
            if name_of(parent) == "blobs" {
                for m in walk_files(&manifests, 8) {
                    if let Some(c) = self.ollama_candidate(&oroot, &manifests, &m) {
                        if Path::new(&c.path) == file {
                            return Some(c);
                        }
                    }
                }
            } else if let Some(c) = self.ollama_candidate(&oroot, &manifests, file) {
                return Some(c);
            }
        }
        let name = name_of(file);
        match detect_format(&name) {
            ModelFileFormat::Gguf if is_gguf(&real(file)) => {
                let mut c = self
                    .gguf_candidates(parent, &[file.to_path_buf()], &[])
                    .into_iter()
                    .next()?;
                c.path = file.display().to_string();
                if let Some(hex) = name
                    .strip_prefix("sha256-")
                    .filter(|h| h.len() == 64 && h.chars().all(|c| c.is_ascii_hexdigit()))
                {
                    c.files[0].sha256 = Some(hex.to_ascii_lowercase());
                }
                Some(c)
            }
            ModelFileFormat::Safetensors | ModelFileFormat::Pickle => self
                .candidates_in_dir(parent, false, 0)
                .into_iter()
                .find(|c| Path::new(&c.path) == parent),
            _ => None,
        }
    }

    // ------------------------------------------------------------------ Ollama

    fn scan_ollama(&mut self, root: &Path, only_under: Option<&Path>) -> Vec<ScanCandidate> {
        let manifests = root.join("manifests");
        let whole =
            only_under.is_none_or(|u| u == root || u == manifests || u == root.join("blobs"));
        let mut out = vec![];
        for m in walk_files(&manifests, 8) {
            if name_of(&m).starts_with('.') {
                continue;
            }
            if !whole && !m.starts_with(only_under.unwrap_or(root)) {
                continue;
            }
            if !self.tick() {
                break;
            }
            if let Some(c) = self.ollama_candidate(root, &manifests, &m) {
                out.push(c);
            }
        }
        out
    }

    fn ollama_candidate(
        &mut self,
        root: &Path,
        manifests: &Path,
        mpath: &Path,
    ) -> Option<ScanCandidate> {
        let manifest = read_json_obj(mpath);
        let layers = manifest.get("layers")?.as_array()?;
        let model_layer = layers.iter().find(|l| {
            l.get("mediaType").and_then(Value::as_str) == Some(OLLAMA_MODEL_MEDIA_TYPE)
        })?;
        let sha = model_layer
            .get("digest")?
            .as_str()?
            .strip_prefix("sha256:")?
            .to_string();
        let blob = root.join("blobs").join(format!("sha256-{sha}"));
        if !blob.is_file() {
            return None;
        }
        let rel: Vec<String> = mpath
            .strip_prefix(manifests)
            .ok()?
            .components()
            .map(|c| c.as_os_str().to_string_lossy().into_owned())
            .collect();
        let (name, suggested_name_) = if rel.len() >= 4 {
            let (ns, model, tag) = (
                &rel[rel.len() - 3],
                &rel[rel.len() - 2],
                &rel[rel.len() - 1],
            );
            if ns == "library" {
                (format!("{model}:{tag}"), format!("{model}:{tag}"))
            } else {
                (format!("{ns}/{model}:{tag}"), format!("{ns}-{model}:{tag}"))
            }
        } else {
            let n = rel.join("/");
            (n.clone(), suggested_name(&n))
        };
        let layer_size = |l: &Value, p: &Path| {
            l.get("size")
                .and_then(Value::as_u64)
                .unwrap_or_else(|| size_of(p))
        };
        let mut files = vec![ScanFile {
            path: name_of(&blob),
            absolute_path: blob.display().to_string(),
            size_bytes: layer_size(model_layer, &blob),
            format: ModelFileFormat::Gguf,
            sha256: Some(sha),
            is_required: true,
        }];
        for l in layers.iter().filter(|l| {
            l.get("mediaType").and_then(Value::as_str) == Some(OLLAMA_PROJECTOR_MEDIA_TYPE)
        }) {
            let Some(pd) = l
                .get("digest")
                .and_then(Value::as_str)
                .and_then(|d| d.strip_prefix("sha256:"))
            else {
                continue;
            };
            let pblob = root.join("blobs").join(format!("sha256-{pd}"));
            if pblob.is_file() {
                files.push(ScanFile {
                    path: name_of(&pblob),
                    absolute_path: pblob.display().to_string(),
                    size_bytes: layer_size(l, &pblob),
                    format: ModelFileFormat::Gguf,
                    sha256: Some(pd.to_string()),
                    is_required: true,
                });
            }
        }
        let meta = self.gguf_meta(&blob);
        let mut suggested = suggest(&suggested_name_, &meta, None);
        suggested.insert("ollama_name".into(), json!(name));
        Some(ScanCandidate {
            path: blob.display().to_string(),
            kind: ScanKind::OllamaBlob,
            format: ModelFileFormat::Gguf,
            total_size_bytes: files.iter().map(|f| f.size_bytes).sum(),
            files,
            suggested,
            metadata: meta,
        })
    }

    // ------------------------------------------------------------------ LM Studio

    fn scan_lmstudio(&mut self, lms: &Path) -> Vec<ScanCandidate> {
        let mut out = vec![];
        for base in [
            lms.join("models"),
            lms.join(".internal").join("bundled-models"),
        ] {
            for publisher in subdirs(&base) {
                let pname = name_of(&publisher);
                if pname.starts_with('.') {
                    continue;
                }
                for model_dir in subdirs(&publisher) {
                    if !self.tick() {
                        return out;
                    }
                    for mut c in self.candidates_in_dir(&model_dir, true, 0) {
                        c.kind = ScanKind::Lmstudio;
                        refine_suggestion(&mut c.suggested, &name_of(&model_dir));
                        c.suggested
                            .entry("publisher")
                            .or_insert_with(|| json!(pname));
                        c.suggested.insert(
                            "lmstudio_name".into(),
                            json!(format!("{pname}/{}", name_of(&model_dir))),
                        );
                        out.push(c);
                    }
                }
            }
        }
        out
    }

    // ------------------------------------------------------------------ generic walk

    fn walk(&mut self, root: &Path, depth: u32, recursive: bool) -> Vec<ScanCandidate> {
        let mut out = vec![];
        if !self.tick() {
            return out;
        }
        if let Some((org, model)) = hf_cache_repo(&name_of(root)) {
            return self.scan_hf_repo(root, &org, &model);
        }
        let subs = subdirs(root);
        if name_of(root) == "hub" || subs.iter().any(|s| hf_cache_repo(&name_of(s)).is_some()) {
            for s in &subs {
                if let Some((org, model)) = hf_cache_repo(&name_of(s)) {
                    out.extend(self.scan_hf_repo(s, &org, &model));
                }
            }
            if !out.is_empty() {
                return out;
            }
        }
        let cands = self.candidates_in_dir(root, false, depth);
        let leaf = cands.iter().any(|c| {
            matches!(c.kind, ScanKind::MlxDir | ScanKind::Plain) && Path::new(&c.path) == root
        });
        out.extend(cands);
        if leaf {
            return out;
        }
        if recursive && depth < self.max_depth {
            for sub in subs {
                let n = name_of(&sub);
                if SKIP_DIRS.contains(&n.as_str())
                    || (n.starts_with('.') && depth > 0 && n != ".lmstudio" && n != ".ollama")
                {
                    continue;
                }
                if sub.join("manifests").is_dir() && sub.join("blobs").is_dir() {
                    out.extend(self.scan_ollama(&sub, None));
                    continue;
                }
                if n == ".lmstudio" && sub.join("models").is_dir() {
                    out.extend(self.scan_lmstudio(&sub));
                    continue;
                }
                out.extend(self.walk(&sub, depth + 1, recursive));
            }
        }
        out
    }

    fn scan_hf_repo(&mut self, repo: &Path, org: &str, model: &str) -> Vec<ScanCandidate> {
        let mut out = vec![];
        for snap in subdirs(&repo.join("snapshots")) {
            for mut c in self.candidates_in_dir(&snap, true, 0) {
                c.kind = ScanKind::HfCache;
                // The snapshot directory is a commit hash: the repo name is the better hint.
                refine_suggestion(&mut c.suggested, &format!("{org}/{model}"));
                c.suggested
                    .insert("repo_id".into(), json!(format!("{org}/{model}")));
                c.suggested.insert("revision".into(), json!(name_of(&snap)));
                c.suggested
                    .insert("name".into(), json!(suggested_name(model)));
                out.push(c);
            }
        }
        out
    }

    /// Candidates rooted at `d` itself: GGUF files (grouped by shard set) and/or one
    /// safetensors / MLX / pickle directory candidate.
    fn candidates_in_dir(&mut self, d: &Path, recursive: bool, depth: u32) -> Vec<ScanCandidate> {
        if d.join("model_index.json").is_file() {
            // A diffusers pipeline: one candidate for the whole tree (text encoder, transformer
            // / unet, VAE, tokenizer, scheduler), never its components on their own.
            return self.pipeline_candidate(d).into_iter().collect();
        }
        let Ok(rd) = std::fs::read_dir(d) else {
            return vec![];
        };
        let mut entries: Vec<PathBuf> = rd
            .flatten()
            .map(|e| e.path())
            .filter(|p| !name_of(p).starts_with('.'))
            .collect();
        entries.sort();
        // Files and symlinks to files (HF snapshots are symlinks into blobs/).
        let files: Vec<PathBuf> = entries.iter().filter(|p| p.is_file()).cloned().collect();
        self.visited += files.len() as u64;
        let mut out = vec![];
        let config = if files.iter().any(|f| name_of(f) == "config.json") {
            read_json_obj(&d.join("config.json"))
        } else {
            JsonObject::new()
        };
        let gguf: Vec<PathBuf> = files
            .iter()
            .filter(|f| detect_format(&name_of(f)) == ModelFileFormat::Gguf)
            .cloned()
            .collect();
        if !gguf.is_empty() {
            out.extend(self.gguf_candidates(d, &gguf, &files));
        }
        let dir_formats: HashSet<ModelFileFormat> = files
            .iter()
            .map(|f| detect_format(&name_of(f)))
            .filter(|f| !matches!(f, ModelFileFormat::Gguf | ModelFileFormat::Other))
            .collect();
        if !dir_formats.is_empty() {
            let is_mlx = is_mlx_config(&config) || name_of(d).to_ascii_lowercase().contains("mlx");
            let fmt = version_format(&dir_formats, is_mlx);
            let scanned: Vec<ScanFile> = files
                .iter()
                .filter_map(|f| {
                    let n = name_of(f);
                    let ffmt = detect_format(&n);
                    if ffmt == ModelFileFormat::Gguf
                        || (ffmt == ModelFileFormat::Other && !is_required_file(&n, ffmt))
                    {
                        return None;
                    }
                    let r = real(f);
                    Some(ScanFile {
                        path: n.clone(),
                        absolute_path: r.display().to_string(),
                        size_bytes: size_of(&r),
                        format: ffmt,
                        sha256: None,
                        is_required: is_required_file(&n, ffmt),
                    })
                })
                .collect();
            if !scanned.is_empty() {
                let mut meta = JsonObject::new();
                for k in CONFIG_KEYS {
                    if let Some(v) = config.get(*k) {
                        meta.insert((*k).into(), v.clone());
                    }
                }
                let mut suggested = suggest(&name_of(d), &JsonObject::new(), Some(&config));
                if is_mlx && suggested.get("type").is_none_or(Value::is_null) {
                    suggested.insert("type".into(), json!("chat"));
                }
                if is_mlx && suggested.get("quantization").is_none_or(Value::is_null) {
                    if let Some(bits) = config
                        .get("quantization")
                        .and_then(|q| q.get("bits"))
                        .and_then(Value::as_u64)
                    {
                        suggested.insert("quantization".into(), json!(format!("{bits}bit")));
                    }
                }
                out.push(ScanCandidate {
                    path: d.display().to_string(),
                    kind: if fmt == ModelFileFormat::Mlx {
                        ScanKind::MlxDir
                    } else {
                        ScanKind::Plain
                    },
                    format: fmt,
                    total_size_bytes: scanned.iter().map(|f| f.size_bytes).sum(),
                    files: scanned,
                    suggested,
                    metadata: meta,
                });
            }
        }
        if recursive && depth < self.max_depth {
            for sub in subdirs(d) {
                let n = name_of(&sub);
                if SKIP_DIRS.contains(&n.as_str()) || n.starts_with('.') {
                    continue;
                }
                if !self.tick() {
                    break;
                }
                out.extend(self.candidates_in_dir(&sub, true, depth + 1));
            }
        }
        out
    }

    /// A diffusers pipeline directory (`model_index.json` at its root): every component file,
    /// paths relative to the root (`transformer/diffusion_pytorch_model-00001-of-00005.safetensors`).
    /// Pickle weights of a component that also has safetensors are optional.
    fn pipeline_candidate(&mut self, d: &Path) -> Option<ScanCandidate> {
        let index = read_json_obj(&d.join("model_index.json"));
        let class = index
            .get("_class_name")
            .and_then(Value::as_str)
            .unwrap_or("DiffusionPipeline")
            .to_string();
        let components: Vec<String> = index
            .iter()
            .filter(|(k, v)| !k.starts_with('_') && v.is_array())
            .map(|(k, _)| k.clone())
            .collect();
        let mut stack = vec![(d.to_path_buf(), 0u32)];
        let mut files: Vec<(String, PathBuf)> = vec![];
        while let Some((dir, depth)) = stack.pop() {
            let Ok(rd) = std::fs::read_dir(&dir) else {
                continue;
            };
            for e in rd.flatten() {
                let p = e.path();
                let n = name_of(&p);
                if n.starts_with('.') || SKIP_DIRS.contains(&n.as_str()) {
                    continue;
                }
                if p.is_dir() {
                    // Follow component dirs only (no symlinked dirs).
                    if depth < 3 && !e.file_type().map(|t| t.is_symlink()).unwrap_or(false) {
                        stack.push((p, depth + 1));
                    }
                } else if p.is_file() {
                    let rel = p
                        .strip_prefix(d)
                        .unwrap_or(&p)
                        .components()
                        .map(|c| c.as_os_str().to_string_lossy().into_owned())
                        .collect::<Vec<_>>()
                        .join("/");
                    files.push((rel, p));
                }
            }
            self.visited += 1;
        }
        files.sort();
        let safetensors_dirs: HashSet<String> = files
            .iter()
            .filter(|(r, _)| detect_format(r) == ModelFileFormat::Safetensors)
            .map(|(r, _)| r.rsplit_once('/').map(|(d, _)| d).unwrap_or("").to_string())
            .collect();
        let scanned: Vec<ScanFile> = files
            .iter()
            .filter_map(|(rel, p)| {
                let n = rel.rsplit('/').next().unwrap_or(rel);
                let fmt = detect_format(n);
                if fmt == ModelFileFormat::Other && !is_required_file(n, fmt) {
                    return None;
                }
                let dir = rel.rsplit_once('/').map(|(d, _)| d).unwrap_or("");
                let required = is_required_file(n, fmt)
                    && !(fmt == ModelFileFormat::Pickle && safetensors_dirs.contains(dir));
                let r = real(p);
                Some(ScanFile {
                    path: rel.clone(),
                    absolute_path: r.display().to_string(),
                    size_bytes: size_of(&r),
                    format: fmt,
                    sha256: None,
                    is_required: required,
                })
            })
            .collect();
        if scanned.is_empty() {
            return None;
        }
        let formats: HashSet<ModelFileFormat> = scanned
            .iter()
            .filter(|f| f.is_required)
            .map(|f| f.format)
            .filter(|f| *f != ModelFileFormat::Other)
            .collect();
        let fmt = version_format(&formats, false);
        let name = name_of(d);
        let mut suggested = suggest(&name, &JsonObject::new(), None);
        suggested.insert("type".into(), json!(pipeline_type(&class, &name)));
        if suggested.get("family").is_none_or(Value::is_null) {
            suggested.insert("family".into(), json!(detect_family(&[&class], None)));
        }
        suggested.insert("pipeline_class".into(), json!(class));
        // A transformer / UNet config may carry the quantization (FP8 checkpoints).
        if suggested.get("quantization").is_none_or(Value::is_null) {
            for c in ["transformer", "unet", "text_encoder"] {
                let cfg = read_json_obj(&d.join(c).join("config.json"));
                if let Some(q) = quant_from_config(&cfg) {
                    suggested.insert("quantization".into(), json!(q));
                    break;
                }
            }
        }
        let mut meta = JsonObject::new();
        meta.insert("pipeline".into(), json!("diffusers"));
        meta.insert("pipeline_class".into(), json!(class));
        meta.insert("components".into(), json!(components));
        if let Some(v) = index.get("_diffusers_version") {
            meta.insert("diffusers_version".into(), v.clone());
        }
        Some(ScanCandidate {
            path: d.display().to_string(),
            kind: ScanKind::Plain,
            format: fmt,
            total_size_bytes: scanned.iter().map(|f| f.size_bytes).sum(),
            files: scanned,
            suggested,
            metadata: meta,
        })
    }

    fn gguf_candidates(
        &mut self,
        d: &Path,
        gguf: &[PathBuf],
        _all: &[PathBuf],
    ) -> Vec<ScanCandidate> {
        let mut groups: BTreeMap<String, Vec<PathBuf>> = BTreeMap::new();
        for p in gguf {
            let n = name_of(p);
            let key = shard_group(&n).map(|(b, _)| b).unwrap_or(n);
            groups.entry(key).or_default().push(p.clone());
        }
        let mmproj: Vec<PathBuf> = gguf
            .iter()
            .filter(|p| name_of(p).to_ascii_lowercase().contains("mmproj"))
            .cloned()
            .collect();
        let n_groups = groups.len();
        let mut out = vec![];
        for (_, mut parts) in groups {
            if n_groups > 1
                && parts
                    .iter()
                    .any(|p| name_of(p).to_ascii_lowercase().contains("mmproj"))
            {
                continue;
            }
            parts.sort();
            let first = real(&parts[0]);
            if !is_gguf(&first) {
                continue;
            }
            let mut files: Vec<ScanFile> = parts
                .iter()
                .map(|p| {
                    let r = real(p);
                    ScanFile {
                        path: name_of(p),
                        absolute_path: r.display().to_string(),
                        size_bytes: size_of(&r),
                        format: ModelFileFormat::Gguf,
                        sha256: None,
                        is_required: true,
                    }
                })
                .collect();
            for proj in mmproj.iter().filter(|m| !parts.contains(m)) {
                let r = real(proj);
                files.push(ScanFile {
                    path: name_of(proj),
                    absolute_path: r.display().to_string(),
                    size_bytes: size_of(&r),
                    format: ModelFileFormat::Gguf,
                    sha256: None,
                    is_required: false,
                });
            }
            let meta = self.gguf_meta(&first);
            let stem = name_of(&parts[0]);
            let stem = shard_group(&stem)
                .map(|(b, _)| b)
                .unwrap_or_else(|| stem.trim_end_matches(".gguf").to_string());
            let suggested = suggest(&stem, &meta, None);
            let single = parts.len() == 1 && mmproj.is_empty();
            out.push(ScanCandidate {
                path: if single {
                    parts[0].display().to_string()
                } else {
                    d.display().to_string()
                },
                kind: ScanKind::Plain,
                format: ModelFileFormat::Gguf,
                total_size_bytes: files.iter().map(|f| f.size_bytes).sum(),
                files,
                suggested,
                metadata: meta,
            });
        }
        out
    }

    fn gguf_meta(&self, p: &Path) -> JsonObject {
        if !self.read_metadata {
            return JsonObject::new();
        }
        match read_gguf_metadata(p) {
            Ok(m) => m,
            Err(e) => {
                tracing::debug!("gguf header unreadable for {}: {e}", p.display());
                JsonObject::new()
            }
        }
    }
}

/// Suggestions from a name hint plus GGUF metadata or an HF config: name, family, type,
/// quantization (GGUF file type, then the name — `FP8`, `Q4_K_M`, `4bit` — then the config's
/// `quantization_config`), context size (`max_position_embeddings`, also in `text_config`) and
/// architecture. Same rules as the control plane's `local_scanner.py` / `detect.py`; the control
/// plane may refine what is still `null`.
fn suggest(name_hint: &str, gguf: &JsonObject, config: Option<&JsonObject>) -> JsonObject {
    let gguf_arch = gguf
        .get("general.architecture")
        .and_then(Value::as_str)
        .map(str::to_string);
    let arch = gguf_arch.clone().or_else(|| {
        config
            .and_then(|c| c.get("model_type"))
            .and_then(Value::as_str)
            .map(str::to_string)
    });
    let display = gguf.get("general.name").and_then(Value::as_str);
    let quant = gguf
        .get("general.file_type")
        .and_then(Value::as_u64)
        .and_then(ftype_name)
        .map(str::to_string)
        .or_else(|| quant_from_name(name_hint))
        .or_else(|| config.and_then(quant_from_config));
    let ctx = gguf_arch
        .as_ref()
        .and_then(|a| {
            gguf.get(&format!("{a}.context_length"))
                .and_then(Value::as_u64)
        })
        .or_else(|| config.and_then(context_from_config));
    let hints = [display.unwrap_or_default(), name_hint];
    let family = detect_family(&hints, arch.as_deref());
    let type_name = format!("{} {name_hint}", display.unwrap_or_default());
    let config_for_type = config.filter(|c| c.contains_key("architectures"));
    let mtype = detect_type(&type_name, gguf_arch.as_deref(), config_for_type);
    let mut s = JsonObject::new();
    s.insert("name".into(), json!(suggested_name(name_hint)));
    s.insert("family".into(), json!(family));
    s.insert("type".into(), json!(mtype));
    s.insert("quantization".into(), json!(quant));
    s.insert("context_size".into(), json!(ctx));
    s.insert("architecture".into(), json!(arch));
    if let Some(label) = gguf.get("general.size_label").and_then(Value::as_str) {
        s.insert("size_label".into(), json!(label));
    }
    if let Some(n) = display {
        s.insert("display_name".into(), json!(n));
    }
    s
}

/// Fill what a directory name could not tell from a better hint (the HF repo name of a cache
/// snapshot, whose directory is a commit hash).
fn refine_suggestion(s: &mut JsonObject, hint: &str) {
    let null = |s: &JsonObject, k: &str| s.get(k).is_none_or(Value::is_null);
    if null(s, "quantization") {
        if let Some(q) = quant_from_name(hint) {
            s.insert("quantization".into(), json!(q));
        }
    }
    if null(s, "family") {
        let arch = s
            .get("architecture")
            .and_then(Value::as_str)
            .map(str::to_string);
        if let Some(f) = detect_family(&[hint], arch.as_deref()) {
            s.insert("family".into(), json!(f));
        }
    }
    if null(s, "type") {
        if let Some(t) = detect_type(hint, None, None) {
            s.insert("type".into(), json!(t));
        }
    }
}

/// Quantization from an HF `config.json` (`quantization_config` / `quantization`, also inside
/// `text_config`): FP8, AWQ, GPTQ, NF4 / 8BIT (bitsandbytes), compressed-tensors schemes,
/// ModelOpt (`FP8`, `NVFP4`), MXFP4, or `<bits>bit` (MLX).
pub fn quant_from_config(config: &JsonObject) -> Option<String> {
    let q = config
        .get("quantization_config")
        .or_else(|| config.get("quantization"))
        .and_then(Value::as_object)
        .or_else(|| {
            config
                .get("text_config")
                .and_then(Value::as_object)
                .and_then(|t| t.get("quantization_config"))
                .and_then(Value::as_object)
        })?;
    let method = q
        .get("quant_method")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_ascii_lowercase();
    let bits = q.get("bits").and_then(Value::as_u64);
    let label = match method.as_str() {
        "fp8" | "fbgemm_fp8" | "ptpc_fp8" => Some("FP8".to_string()),
        "awq" => Some("AWQ".into()),
        "gptq" | "gptq_marlin" => Some("GPTQ".into()),
        "mxfp4" => Some("MXFP4".into()),
        "bitsandbytes" => Some(
            if q.get("load_in_4bit").and_then(Value::as_bool) == Some(true) {
                match q.get("bnb_4bit_quant_type").and_then(Value::as_str) {
                    Some("nf4") => "NF4",
                    _ => "4BIT",
                }
            } else {
                "8BIT"
            }
            .to_string(),
        ),
        "modelopt" => q
            .get("quant_algo")
            .and_then(Value::as_str)
            .map(|a| a.to_ascii_uppercase()),
        "compressed-tensors" => compressed_tensors_label(q),
        _ => None,
    };
    label.or_else(|| bits.map(|b| format!("{b}bit")))
}

/// compressed-tensors: the weight scheme of the first config group (`FP8`, `INT4`, `INT8`, ...).
fn compressed_tensors_label(q: &JsonObject) -> Option<String> {
    let groups = q.get("config_groups")?.as_object()?;
    let w = groups.values().find_map(|g| g.get("weights"))?;
    let bits = w.get("num_bits").and_then(Value::as_u64)?;
    let float = w.get("type").and_then(Value::as_str) == Some("float");
    Some(match (float, bits) {
        (true, 8) => "FP8".into(),
        (true, 4) => "FP4".into(),
        (false, b) => format!("INT{b}"),
        (true, b) => format!("FP{b}"),
    })
}

/// Context length from an HF config (same keys as the control plane, `text_config` too).
pub fn context_from_config(config: &JsonObject) -> Option<u64> {
    for k in [
        "max_position_embeddings",
        "n_positions",
        "seq_length",
        "max_seq_len",
    ] {
        if let Some(v) = config.get(k).and_then(Value::as_u64).filter(|v| *v > 0) {
            return Some(v);
        }
    }
    config
        .get("text_config")
        .and_then(Value::as_object)
        .and_then(context_from_config)
}

fn is_word(c: char) -> bool {
    c.is_ascii_alphanumeric() || c == '_'
}

/// `pat` occurs in `s` as a whole word (regex `\bpat\b`).
fn whole_word(s: &str, pat: &str) -> bool {
    s.match_indices(pat).any(|(i, m)| {
        s[..i].chars().last().is_none_or(|c| !is_word(c))
            && s[i + m.len()..].chars().next().is_none_or(|c| !is_word(c))
    })
}

/// `pat` followed by an ASCII digit (`sd-?\d`, `\bwan\d`).
fn followed_by_digit(s: &str, pat: &str, word_boundary_before: bool) -> bool {
    s.match_indices(pat).any(|(i, m)| {
        s[i + m.len()..]
            .chars()
            .next()
            .is_some_and(|c| c.is_ascii_digit())
            && (!word_boundary_before || s[..i].chars().last().is_none_or(|c| !is_word(c)))
    })
}

/// Model family from the architecture (GGUF `general.architecture` / config `model_type`), then
/// name hints. Ported from the control plane's `detect.detect_family` (same order).
pub fn detect_family(hints: &[&str], architecture: Option<&str>) -> Option<&'static str> {
    if let Some(a) = architecture {
        let fam = match a.to_ascii_lowercase().as_str() {
            "qwen3" | "qwen3moe" => Some("qwen3"),
            "qwen2" | "qwen2moe" | "qwen2vl" => Some("qwen2"),
            "llama" => Some("llama"),
            "llama4" => Some("llama4"),
            "gemma3" | "gemma3n" => Some("gemma3"),
            "gemma2" | "gemma" => Some("gemma"),
            "mistral3" => Some("mistral"),
            "deepseek2" | "deepseek" => Some("deepseek"),
            "phi3" | "phi2" => Some("phi"),
            "glm4" | "chatglm" => Some("glm"),
            "granite" => Some("granite"),
            "internlm2" => Some("internlm"),
            "nomic-bert" | "nomic-bert-moe" => Some("nomic"),
            "bert" => Some("bge"),
            "jina-bert-v2" => Some("jina"),
            "gpt-oss" => Some("gpt_oss"),
            _ => None,
        };
        if fam.is_some() {
            return fam;
        }
    }
    let any = |l: &str, pats: &[&str]| pats.iter().any(|p| l.contains(p));
    for h in hints.iter().filter(|h| !h.is_empty()) {
        let l = h.to_ascii_lowercase();
        let l = l.as_str();
        let fam = if any(l, &["qwen3", "qwen-3"]) {
            "qwen3"
        } else if any(l, &["qwen2", "qwen-2", "qwq"]) {
            "qwen2"
        } else if l.contains("qwen") {
            "qwen"
        } else if any(l, &["llama-4", "llama4"]) {
            "llama4"
        } else if any(l, &["llama-3", "llama3"]) {
            "llama3"
        } else if any(l, &["llama", "vicuna", "alpaca"]) {
            "llama"
        } else if any(l, &["gemma-3", "gemma3"]) {
            "gemma3"
        } else if l.contains("gemma") {
            "gemma"
        } else if any(
            l,
            &[
                "mistral",
                "mixtral",
                "codestral",
                "devstral",
                "magistral",
                "ministral",
            ],
        ) {
            "mistral"
        } else if l.contains("deepseek") {
            "deepseek"
        } else if l.contains("phi") {
            "phi"
        } else if l.contains("glm") {
            "glm"
        } else if l.contains("kimi") {
            "kimi"
        } else if l.contains("granite") {
            "granite"
        } else if l.contains("internlm") {
            "internlm"
        } else if l.contains("hermes") {
            "hermes"
        } else if any(l, &["gpt-oss", "gpt_oss"]) {
            "gpt_oss"
        } else if l.contains("nomic") {
            "nomic"
        } else if l.contains("bge") {
            "bge"
        } else if l.contains("e5-") || whole_word(l, "e5") {
            "e5"
        } else if l.contains("whisper") {
            "whisper"
        } else if l.contains("kokoro") {
            "kokoro"
        } else if l.contains("cosyvoice") {
            "cosyvoice"
        } else if any(l, &["stable-diffusion", "stablediffusion", "sdxl", "sd3"])
            || followed_by_digit(l, "sd-", false)
            || followed_by_digit(l, "sd", false)
        {
            "stable_diffusion"
        } else if l.contains("flux") {
            "flux"
        } else if l.contains("wan2") || followed_by_digit(l, "wan", true) {
            "wan"
        } else if l.contains("hunyuan") {
            "hunyuan"
        } else {
            continue;
        };
        return Some(fam);
    }
    None
}

fn looks_thinking(lower: &str) -> bool {
    ["-r1", "qwq", "thinking", "reason"]
        .iter()
        .any(|k| lower.contains(k))
}

/// Best-effort model type (`chat`, `thinking`, `vision`, `embedding`, `rerank`, `stt`, `tts`,
/// `image`, `video`). Ported from the control plane's `detect.detect_type` (no pipeline tags on
/// a node): GGUF architecture, then `config.json` architectures, then the name.
pub fn detect_type(
    name: &str,
    gguf_arch: Option<&str>,
    config: Option<&JsonObject>,
) -> Option<&'static str> {
    let lower = name.to_ascii_lowercase();
    if let Some(a) = gguf_arch.map(str::to_ascii_lowercase) {
        if [
            "bert",
            "nomic-bert",
            "nomic-bert-moe",
            "jina-bert-v2",
            "xlm-roberta",
        ]
        .contains(&a.as_str())
        {
            return Some(if lower.contains("rerank") {
                "rerank"
            } else {
                "embedding"
            });
        }
        if a.contains("whisper") {
            return Some("stt");
        }
    }
    if let Some(c) = config {
        let archs: Vec<String> = c
            .get("architectures")
            .and_then(Value::as_array)
            .map(|a| {
                a.iter()
                    .filter_map(Value::as_str)
                    .map(str::to_ascii_lowercase)
                    .collect()
            })
            .unwrap_or_default();
        let joined = archs.join(" ");
        if joined.contains("forconditionalgeneration")
            && (joined.contains("vl") || joined.contains("vision"))
        {
            return Some("vision");
        }
        if joined.contains("whisper") {
            return Some("stt");
        }
        if joined.contains("forsequenceclassification") {
            return Some("rerank");
        }
        if !archs.is_empty()
            && archs
                .iter()
                .all(|a| a.ends_with("model") && !a.contains("causallm"))
        {
            return Some("embedding");
        }
        if joined.contains("causallm") || joined.contains("forconditionalgeneration") {
            return Some(if looks_thinking(&lower) {
                "thinking"
            } else {
                "chat"
            });
        }
    }
    let has = |ks: &[&str]| ks.iter().any(|k| lower.contains(k));
    Some(if lower.contains("rerank") {
        "rerank"
    } else if has(&["embed", "bge-", "e5-"]) {
        "embedding"
    } else if has(&["whisper", "parakeet"]) {
        "stt"
    } else if has(&["tts", "kokoro", "cosyvoice"]) {
        "tts"
    } else if has(&["stable-diffusion", "sdxl", "flux", "sd3"]) {
        "image"
    } else if has(&["wan2", "hunyuanvideo", "text-to-video", "t2v"]) {
        "video"
    } else if has(&["-vl", "vision", "llava"]) {
        "vision"
    } else if looks_thinking(&lower) {
        "thinking"
    } else if gguf_arch.is_some() || has(&["instruct", "chat", "-it"]) {
        "chat"
    } else {
        return None;
    })
}

/// Type of a diffusers pipeline from its `_class_name` (and name hints).
pub fn pipeline_type(class: &str, name: &str) -> &'static str {
    let l = format!("{class} {name}").to_ascii_lowercase();
    if [
        "video",
        "wan",
        "i2v",
        "t2v",
        "ltx",
        "cogvideo",
        "mochi",
        "hunyuanvideo",
    ]
    .iter()
    .any(|k| l.contains(k))
    {
        "video"
    } else if ["audio", "music"].iter().any(|k| l.contains(k)) {
        "tts"
    } else {
        "image"
    }
}

fn find_ollama_root(path: &Path) -> Option<PathBuf> {
    path.ancestors()
        .find(|c| c.join("manifests").is_dir() && c.join("blobs").is_dir())
        .map(Path::to_path_buf)
}

fn find_lmstudio_root(path: &Path) -> Option<PathBuf> {
    if name_of(path) == ".lmstudio" && path.join("models").is_dir() {
        return Some(path.to_path_buf());
    }
    if name_of(path) == "models" && path.parent().is_some_and(|p| name_of(p) == ".lmstudio") {
        return path.parent().map(Path::to_path_buf);
    }
    None
}

/// Every regular file under `root` (no symlinked dirs), depth-limited, sorted.
fn walk_files(root: &Path, max_depth: u32) -> Vec<PathBuf> {
    let mut out = vec![];
    let mut stack = vec![(root.to_path_buf(), 0u32)];
    while let Some((d, depth)) = stack.pop() {
        let Ok(rd) = std::fs::read_dir(&d) else {
            continue;
        };
        for e in rd.flatten() {
            let Ok(t) = e.file_type() else { continue };
            if t.is_dir() && depth < max_depth {
                stack.push((e.path(), depth + 1));
            } else if t.is_file() {
                out.push(e.path());
            }
        }
    }
    out.sort();
    out
}

fn dedupe(cands: Vec<ScanCandidate>) -> Vec<ScanCandidate> {
    let mut seen = HashSet::new();
    let mut out: Vec<ScanCandidate> = cands
        .into_iter()
        .filter(|c| seen.insert(c.path.clone()))
        .collect();
    out.sort_by(|a, b| a.path.cmp(&b.path));
    out
}

/// `scan_path` (blocking; run on a blocking thread).
pub fn scan_blocking(
    args: &ScanPathArgs,
    cancel: &CancellationToken,
) -> Result<ScanPathResult, ErrorInfo> {
    let started = Instant::now();
    let root = expand_path(&args.path)?;
    std::fs::metadata(&root).map_err(|e| io_err(&root, &e))?;
    if root.is_dir() {
        std::fs::read_dir(&root).map_err(|e| io_err(&root, &e))?;
    }
    let mut s = Scanner {
        max_depth: args.max_depth.clamp(1, 10),
        max_entries: args.max_entries.max(1),
        read_metadata: args.read_metadata,
        visited: 0,
        truncated: false,
        cancel,
    };
    let candidates = s.scan(&root, args.recursive);
    if cancel.is_cancelled() {
        return Err(err(error_code::CANCELLED, "cancelled"));
    }
    Ok(ScanPathResult {
        path: root.display().to_string(),
        candidates,
        truncated: s.truncated,
        visited: s.visited,
        seconds: started.elapsed().as_secs_f64(),
    })
}

pub async fn scan_path(ctx: &Ctx, args: ScanPathArgs) -> Result<Value, ErrorInfo> {
    ctx.progress(None, format!("scanning {}", args.path));
    let cancel = ctx.cancel.clone();
    let res = tokio::task::spawn_blocking(move || scan_blocking(&args, &cancel))
        .await
        .map_err(|e| err(error_code::INTERNAL, format!("scan task: {e}")))??;
    serde_json::to_value(res).map_err(|e| err(error_code::INTERNAL, e.to_string()))
}

// ---------------------------------------------------------------------------------- hash_files

/// Resolve one requested path against `root` (must stay inside it).
pub fn resolve_hash_path(root: Option<&Path>, p: &str) -> Result<PathBuf, ErrorInfo> {
    match root {
        None => expand_path(p),
        Some(root) => {
            let rel = Path::new(p);
            if rel.is_absolute() {
                let canon = real(rel);
                if canon.starts_with(real(root)) {
                    return Ok(rel.to_path_buf());
                }
                return Err(err(
                    error_code::INVALID_ARGS,
                    format!("{p} is outside root"),
                ));
            }
            if rel.components().any(|c| matches!(c, Component::ParentDir)) {
                return Err(err(error_code::INVALID_ARGS, format!("{p} escapes root")));
            }
            let joined = root.join(rel);
            // Symlinks may point elsewhere (HF snapshot → blobs); allow targets under the root's
            // parent tree only when the link itself is inside the root.
            Ok(joined)
        }
    }
}

/// sha256 of one file, adding to `done` as bytes are read; aborts on cancel.
pub fn sha256_file_blocking(
    path: &Path,
    done: &AtomicU64,
    cancel: &CancellationToken,
) -> std::io::Result<(String, u64)> {
    let mut f = File::open(path)?;
    let meta = f.metadata()?;
    if !meta.is_file() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "not a regular file",
        ));
    }
    let mut h = Sha256::new();
    let mut buf = vec![0u8; 1 << 20];
    let mut n_total = 0u64;
    loop {
        if cancel.is_cancelled() {
            return Err(std::io::Error::new(
                std::io::ErrorKind::Interrupted,
                "cancelled",
            ));
        }
        let n = f.read(&mut buf)?;
        if n == 0 {
            break;
        }
        h.update(&buf[..n]);
        n_total += n as u64;
        done.fetch_add(n as u64, Ordering::Relaxed);
    }
    Ok((hex::encode(h.finalize()), n_total))
}

/// `hash_files`. Hashes are also remembered by the cache (`record`, keyed by path + size +
/// mtime) so a later `load_model` of the same files does not read them again.
pub async fn hash_files(
    ctx: &Ctx,
    args: HashFilesArgs,
    record: Option<Arc<crate::cache::CacheManager>>,
) -> Result<Value, ErrorInfo> {
    if args.algorithm != "sha256" {
        return Err(err(
            error_code::INVALID_ARGS,
            format!("unsupported algorithm {}", args.algorithm),
        ));
    }
    let started = Instant::now();
    let root = match &args.root {
        Some(r) => {
            let p = expand_path(r)?;
            if !p.is_dir() {
                return Err(err(
                    error_code::PATH_NOT_FOUND,
                    format!("root {} is not a directory", p.display()),
                ));
            }
            Some(p)
        }
        None => None,
    };
    let targets: Vec<(String, Result<PathBuf, ErrorInfo>)> = args
        .paths
        .iter()
        .map(|p| (p.clone(), resolve_hash_path(root.as_deref(), p)))
        .collect();
    let total: u64 = targets
        .iter()
        .filter_map(|(_, r)| r.as_ref().ok())
        .map(|p| size_of(p))
        .sum::<u64>()
        .max(1);
    let done = Arc::new(AtomicU64::new(0));
    let cancel = ctx.cancel.clone();
    let n = targets.len();
    let d2 = done.clone();
    let worker = tokio::task::spawn_blocking(move || {
        let mut out = vec![];
        for (i, (orig, r)) in targets.into_iter().enumerate() {
            if cancel.is_cancelled() {
                break;
            }
            let item = match r {
                Err(e) => HashedFile {
                    path: orig,
                    sha256: None,
                    size_bytes: None,
                    error: Some(e),
                },
                Ok(p) => match sha256_file_blocking(&p, &d2, &cancel) {
                    Ok((sha, size)) => {
                        if let Some(c) = &record {
                            c.record_hash(&p, &sha);
                        }
                        HashedFile {
                            path: orig,
                            sha256: Some(sha),
                            size_bytes: Some(size),
                            error: None,
                        }
                    }
                    Err(e) if e.kind() == std::io::ErrorKind::Interrupted => break,
                    Err(e) => HashedFile {
                        path: orig,
                        sha256: None,
                        size_bytes: None,
                        error: Some(io_err(&p, &e)),
                    },
                },
            };
            out.push((i, item));
        }
        if let Some(c) = &record {
            c.save_hashes();
        }
        out
    });
    tokio::pin!(worker);
    let interval = Duration::from_secs_f64(args.progress_interval_seconds.max(0.2));
    let files = loop {
        tokio::select! {
            r = &mut worker => break r.map_err(|e| err(error_code::INTERNAL, format!("hash task: {e}")))?,
            _ = tokio::time::sleep(interval) => {
                let b = done.load(Ordering::Relaxed);
                ctx.progress(Some((b as f64 / total as f64 * 100.0).min(99.9)), format!("{} of {} bytes hashed ({n} files)", b, total));
            }
        }
    };
    if ctx.cancel.is_cancelled() {
        return Err(err(error_code::CANCELLED, "cancelled"));
    }
    let files: Vec<HashedFile> = files.into_iter().map(|(_, f)| f).collect();
    let hashes = files
        .iter()
        .filter_map(|f| f.sha256.clone().map(|s| (f.path.clone(), s)))
        .collect();
    let res = HashFilesResult {
        root: root.map(|r| r.display().to_string()),
        files,
        hashes,
        bytes_hashed: done.load(Ordering::Relaxed),
        seconds: started.elapsed().as_secs_f64(),
    };
    serde_json::to_value(res).map_err(|e| err(error_code::INTERNAL, e.to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Minimal GGUF v3 header with a few scalar keys and a string array (skipped).
    pub fn gguf_bytes(arch: &str, ctx: u32, ftype: u32) -> Vec<u8> {
        let mut b = b"GGUF".to_vec();
        b.extend(3u32.to_le_bytes());
        b.extend(0u64.to_le_bytes());
        b.extend(5u64.to_le_bytes());
        let s = |b: &mut Vec<u8>, x: &str| {
            b.extend((x.len() as u64).to_le_bytes());
            b.extend(x.as_bytes());
        };
        s(&mut b, "general.architecture");
        b.extend(8u32.to_le_bytes());
        s(&mut b, arch);
        s(&mut b, "general.name");
        b.extend(8u32.to_le_bytes());
        s(&mut b, "Tiny Model");
        s(&mut b, "tokenizer.ggml.tokens");
        b.extend(9u32.to_le_bytes());
        b.extend(8u32.to_le_bytes());
        b.extend(2u64.to_le_bytes());
        s(&mut b, "a");
        s(&mut b, "bc");
        s(&mut b, &format!("{arch}.context_length"));
        b.extend(4u32.to_le_bytes());
        b.extend(ctx.to_le_bytes());
        s(&mut b, "general.file_type");
        b.extend(4u32.to_le_bytes());
        b.extend(ftype.to_le_bytes());
        b.extend([0u8; 64]);
        b
    }

    fn write(p: &Path, bytes: &[u8]) {
        std::fs::create_dir_all(p.parent().unwrap()).unwrap();
        std::fs::write(p, bytes).unwrap();
    }

    fn sha(bytes: &[u8]) -> String {
        hex::encode(Sha256::digest(bytes))
    }

    #[test]
    fn gguf_header() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("m.gguf");
        write(&p, &gguf_bytes("qwen3", 40960, 15));
        let m = read_gguf_metadata(&p).unwrap();
        assert_eq!(m["general.architecture"], "qwen3");
        assert_eq!(m["qwen3.context_length"], 40960);
        assert_eq!(m["general.file_type"], 15);
        assert!(!m.contains_key("tokenizer.ggml.tokens"));
        assert_eq!(ftype_name(15), Some("Q4_K_M"));
        assert_eq!(
            quant_from_name("Qwen3-0.6B-Q4_K_M.gguf").as_deref(),
            Some("Q4_K_M")
        );
        assert_eq!(quant_from_name("Qwen3-0.6B-4bit").as_deref(), Some("4bit"));
        assert_eq!(quant_from_name("plain-model"), None);
        assert_eq!(shard_group("m-00002-of-00003.gguf"), Some(("m".into(), 2)));
    }

    fn scan(p: &Path) -> ScanPathResult {
        scan_blocking(
            &ScanPathArgs::new(p.display().to_string()),
            &CancellationToken::new(),
        )
        .unwrap()
    }

    #[test]
    fn ollama_store() {
        let d = tempfile::tempdir().unwrap();
        let root = d.path().join(".ollama/models");
        let model = gguf_bytes("qwen3", 40960, 15);
        let digest = sha(&model);
        write(&root.join(format!("blobs/sha256-{digest}")), &model);
        let manifest = json!({"schemaVersion": 2, "layers": [
            {"mediaType": OLLAMA_MODEL_MEDIA_TYPE, "digest": format!("sha256:{digest}"), "size": model.len()},
            {"mediaType": "application/vnd.ollama.image.template", "digest": "sha256:00", "size": 1}
        ]});
        write(
            &root.join("manifests/registry.ollama.ai/library/qwen3/0.6b"),
            manifest.to_string().as_bytes(),
        );
        for p in [root.clone(), root.join("manifests")] {
            let r = scan(&p);
            assert_eq!(r.candidates.len(), 1, "{p:?}");
            let c = &r.candidates[0];
            assert_eq!(c.kind, ScanKind::OllamaBlob);
            assert_eq!(c.files[0].sha256.as_deref(), Some(digest.as_str()));
            assert_eq!(c.suggested["ollama_name"], "qwen3:0.6b");
            assert_eq!(c.suggested["quantization"], "Q4_K_M");
            assert_eq!(c.suggested["context_size"], 40960);
        }
        // A blob path resolves through the manifests.
        let blob = root.join(format!("blobs/sha256-{digest}"));
        let r = scan(&blob);
        assert_eq!(r.candidates[0].kind, ScanKind::OllamaBlob);
        // Walking from the home dir finds the hidden .ollama store at depth > 0.
        let r = scan(d.path());
        assert!(r.candidates.iter().any(|c| c.kind == ScanKind::OllamaBlob));
    }

    #[test]
    fn lmstudio_hf_mlx_plain() {
        let d = tempfile::tempdir().unwrap();
        let lms = d.path().join(".lmstudio");
        write(
            &lms.join("models/lmstudio-community/Qwen3-GGUF/Qwen3-0.6B-Q8_0.gguf"),
            &gguf_bytes("qwen3", 4096, 7),
        );
        let mlx = lms.join("models/mlx-community/Qwen3-0.6B-4bit");
        write(&mlx.join("config.json"), json!({"model_type": "qwen3", "max_position_embeddings": 40960, "quantization": {"bits": 4, "group_size": 64}}).to_string().as_bytes());
        write(&mlx.join("model.safetensors"), b"st");
        write(&mlx.join("tokenizer.json"), b"{}");
        write(&mlx.join("README.md"), b"x");
        let r = scan(&lms);
        assert_eq!(r.candidates.len(), 2, "{:#?}", r.candidates);
        assert!(r.candidates.iter().all(|c| c.kind == ScanKind::Lmstudio));
        let m = r
            .candidates
            .iter()
            .find(|c| c.format == ModelFileFormat::Mlx)
            .unwrap();
        assert_eq!(
            m.suggested["lmstudio_name"],
            "mlx-community/Qwen3-0.6B-4bit"
        );
        assert_eq!(m.suggested["quantization"], "4bit");
        assert!(m.files.iter().all(|f| f.path != "README.md"));
        assert_eq!(m.metadata["max_position_embeddings"], 40960);

        // HF cache: snapshot files are symlinks into blobs/.
        let repo = d.path().join("hub/models--Qwen--Qwen3-0.6B");
        write(&repo.join("blobs/abc"), b"weights");
        write(
            &repo.join("blobs/cfg"),
            json!({"model_type": "qwen3"}).to_string().as_bytes(),
        );
        let snap = repo.join("snapshots/rev1");
        std::fs::create_dir_all(&snap).unwrap();
        std::os::unix::fs::symlink(repo.join("blobs/abc"), snap.join("model.safetensors")).unwrap();
        std::os::unix::fs::symlink(repo.join("blobs/cfg"), snap.join("config.json")).unwrap();
        let r = scan(&d.path().join("hub"));
        assert_eq!(r.candidates.len(), 1);
        let c = &r.candidates[0];
        assert_eq!(c.kind, ScanKind::HfCache);
        assert_eq!(c.suggested["repo_id"], "Qwen/Qwen3-0.6B");
        assert_eq!(c.suggested["revision"], "rev1");
        let w = c
            .files
            .iter()
            .find(|f| f.path == "model.safetensors")
            .unwrap();
        assert!(w.absolute_path.ends_with("blobs/abc") && w.size_bytes == 7);

        // Plain: a sharded GGUF set + mmproj in one dir, and a pickle-only dir.
        let plain = d.path().join("models");
        write(
            &plain.join("big/m-00001-of-00002.gguf"),
            &gguf_bytes("llama", 8192, 18),
        );
        write(&plain.join("big/m-00002-of-00002.gguf"), b"rest");
        write(&plain.join("old/pytorch_model.bin"), b"pk");
        write(&plain.join("old/config.json"), b"{}");
        let r = scan(&plain);
        assert_eq!(r.candidates.len(), 2, "{:#?}", r.candidates);
        let big = r
            .candidates
            .iter()
            .find(|c| c.format == ModelFileFormat::Gguf)
            .unwrap();
        assert_eq!(big.files.len(), 2);
        assert!(big.path.ends_with("big"));
        assert_eq!(big.suggested["quantization"], "Q6_K");
        let old = r
            .candidates
            .iter()
            .find(|c| c.format == ModelFileFormat::Pickle)
            .unwrap();
        assert_eq!(old.kind, ScanKind::Plain);
        // Single GGUF file path.
        let r = scan(&plain.join("big/m-00001-of-00002.gguf"));
        assert_eq!(r.candidates.len(), 1);
    }

    #[test]
    fn diffusers_pipeline_is_one_candidate() {
        let d = tempfile::tempdir().unwrap();
        // HF cache layout: snapshot files are symlinks into blobs/.
        let repo = d.path().join("hub/models--Qwen--Qwen-Image-Edit-2511");
        let snap = repo.join("snapshots/r1");
        let blob = |name: &str, body: &[u8]| {
            let b = repo.join("blobs").join(name);
            write(&b, body);
            b
        };
        let files: &[(&str, &[u8])] = &[
            ("model_index.json", br#"{"_class_name": "QwenImageEditPlusPipeline", "_diffusers_version": "0.36.0", "transformer": ["diffusers", "QwenImageTransformer2DModel"], "vae": ["diffusers", "AutoencoderKLQwenImage"], "text_encoder": ["transformers", "Qwen2_5_VLForConditionalGeneration"], "scheduler": ["diffusers", "FlowMatchEulerDiscreteScheduler"]}"#),
            ("transformer/config.json", br#"{"_class_name": "QwenImageTransformer2DModel"}"#),
            ("transformer/diffusion_pytorch_model-00001-of-00002.safetensors", b"t1"),
            ("transformer/diffusion_pytorch_model-00002-of-00002.safetensors", b"t2"),
            ("transformer/diffusion_pytorch_model.bin", b"pk"),
            ("vae/config.json", b"{}"),
            ("vae/diffusion_pytorch_model.safetensors", b"v"),
            ("text_encoder/config.json", br#"{"architectures": ["Qwen2_5_VLForConditionalGeneration"], "model_type": "qwen2_5_vl"}"#),
            ("text_encoder/model.safetensors", b"te"),
            ("tokenizer/tokenizer.json", b"{}"),
            ("scheduler/scheduler_config.json", b"{}"),
            ("README.md", b"x"),
        ];
        for (i, (rel, body)) in files.iter().enumerate() {
            let b = blob(&format!("b{i}"), body);
            let link = snap.join(rel);
            std::fs::create_dir_all(link.parent().unwrap()).unwrap();
            std::os::unix::fs::symlink(&b, &link).unwrap();
        }
        let r = scan(&d.path().join("hub"));
        assert_eq!(r.candidates.len(), 1, "{:#?}", r.candidates);
        let c = &r.candidates[0];
        assert_eq!(c.kind, ScanKind::HfCache);
        assert_eq!(Path::new(&c.path), snap);
        assert_eq!(c.format, ModelFileFormat::Safetensors);
        assert_eq!(c.suggested["type"], "image");
        assert_eq!(c.suggested["family"], "qwen");
        assert_eq!(c.suggested["pipeline_class"], "QwenImageEditPlusPipeline");
        assert_eq!(c.suggested["repo_id"], "Qwen/Qwen-Image-Edit-2511");
        assert_eq!(c.metadata["pipeline"], "diffusers");
        let paths: Vec<&str> = c.files.iter().map(|f| f.path.as_str()).collect();
        for want in [
            "model_index.json",
            "transformer/diffusion_pytorch_model-00001-of-00002.safetensors",
            "vae/diffusion_pytorch_model.safetensors",
            "text_encoder/model.safetensors",
            "tokenizer/tokenizer.json",
            "scheduler/scheduler_config.json",
        ] {
            assert!(paths.contains(&want), "{want} in {paths:?}");
        }
        assert!(!paths.contains(&"README.md"));
        let pk = c
            .files
            .iter()
            .find(|f| f.path == "transformer/diffusion_pytorch_model.bin")
            .unwrap();
        assert!(!pk.is_required, "pickle next to safetensors is optional");
        assert!(c.files.iter().all(|f| f.absolute_path.contains("/blobs/")));
        // Pointing at the pipeline directory itself (plain layout) gives the same candidate.
        let r = scan(&snap);
        assert_eq!(r.candidates.len(), 1);
        // A video pipeline.
        let wan = d.path().join("models/Wan2.2-T2V-A14B-Diffusers");
        write(
            &wan.join("model_index.json"),
            br#"{"_class_name": "WanPipeline"}"#,
        );
        write(&wan.join("transformer/x.safetensors"), b"w");
        let r = scan(&d.path().join("models"));
        assert_eq!(r.candidates.len(), 1);
        assert_eq!(r.candidates[0].suggested["type"], "video");
        assert_eq!(r.candidates[0].suggested["family"], "wan");
    }

    #[test]
    fn safetensors_suggestions_from_name_and_config() {
        let d = tempfile::tempdir().unwrap();
        // FP8 in the name + quantization_config, context in text_config, HF cache layout.
        let repo = d.path().join("hub/models--Qwen--Qwen3.8-27B-FP8");
        let snap = repo.join("snapshots/abc123");
        write(
            &snap.join("config.json"),
            json!({
                "architectures": ["Qwen3_5ForConditionalGeneration"], "model_type": "qwen3_5",
                "text_config": {"max_position_embeddings": 262144, "num_hidden_layers": 64},
                "quantization_config": {"quant_method": "fp8", "weight_block_size": [128, 128]}
            })
            .to_string()
            .as_bytes(),
        );
        write(&snap.join("model-00001-of-00002.safetensors"), b"a");
        write(&snap.join("model-00002-of-00002.safetensors"), b"b");
        let r = scan(&d.path().join("hub"));
        assert_eq!(r.candidates.len(), 1);
        let c = &r.candidates[0].suggested;
        assert_eq!(c["quantization"], "FP8");
        assert_eq!(c["context_size"], 262144);
        assert_eq!(c["family"], "qwen3");
        assert_eq!(c["type"], "chat");
        // FP8 from the config alone (no hint in the name); embedding / rerank / vision types.
        let plain = d.path().join("m");
        write(&plain.join("ckpt/config.json"), json!({"architectures": ["LlamaForCausalLM"], "model_type": "llama", "max_position_embeddings": 131072, "quantization_config": {"quant_method": "compressed-tensors", "config_groups": {"group_0": {"weights": {"num_bits": 8, "type": "float"}}}}}).to_string().as_bytes());
        write(&plain.join("ckpt/model.safetensors"), b"x");
        write(
            &plain.join("Qwen3-Embedding-0.6B/config.json"),
            json!({"architectures": ["Qwen3Model"], "model_type": "qwen3"})
                .to_string()
                .as_bytes(),
        );
        write(&plain.join("Qwen3-Embedding-0.6B/model.safetensors"), b"x");
        write(&plain.join("Qwen2.5-VL-7B-Instruct-AWQ/config.json"), json!({"architectures": ["Qwen2_5_VLForConditionalGeneration"], "quantization_config": {"quant_method": "awq", "bits": 4}}).to_string().as_bytes());
        write(
            &plain.join("Qwen2.5-VL-7B-Instruct-AWQ/model.safetensors"),
            b"x",
        );
        let r = scan(&plain);
        let by = |end: &str| {
            r.candidates
                .iter()
                .find(|c| c.path.ends_with(end))
                .unwrap_or_else(|| panic!("{end}: {:#?}", r.candidates))
                .suggested
                .clone()
        };
        let ck = by("ckpt");
        assert_eq!(
            (
                ck["quantization"].as_str(),
                ck["context_size"].as_u64(),
                ck["family"].as_str(),
                ck["type"].as_str()
            ),
            (Some("FP8"), Some(131072), Some("llama"), Some("chat"))
        );
        let emb = by("Qwen3-Embedding-0.6B");
        assert_eq!(
            (emb["type"].as_str(), emb["family"].as_str()),
            (Some("embedding"), Some("qwen3"))
        );
        let vl = by("Qwen2.5-VL-7B-Instruct-AWQ");
        assert_eq!(
            (vl["type"].as_str(), vl["quantization"].as_str()),
            (Some("vision"), Some("AWQ"))
        );
    }

    #[test]
    fn detection_helpers() {
        let q = |v: Value| quant_from_config(v.as_object().unwrap());
        assert_eq!(
            q(json!({"quantization_config": {"quant_method": "fp8"}})).as_deref(),
            Some("FP8")
        );
        assert_eq!(
            q(json!({"quantization_config": {"quant_method": "gptq", "bits": 4}})).as_deref(),
            Some("GPTQ")
        );
        assert_eq!(q(json!({"quantization_config": {"quant_method": "bitsandbytes", "load_in_4bit": true, "bnb_4bit_quant_type": "nf4"}})).as_deref(), Some("NF4"));
        assert_eq!(
            q(json!({"quantization_config": {"quant_method": "modelopt", "quant_algo": "NVFP4"}}))
                .as_deref(),
            Some("NVFP4")
        );
        assert_eq!(
            q(json!({"quantization": {"bits": 4, "group_size": 64}})).as_deref(),
            Some("4bit")
        );
        assert_eq!(
            q(json!({"text_config": {"quantization_config": {"quant_method": "mxfp4"}}}))
                .as_deref(),
            Some("MXFP4")
        );
        assert_eq!(q(json!({"torch_dtype": "bfloat16"})), None);
        assert_eq!(detect_family(&["bge-small-en"], None), Some("bge"));
        assert_eq!(detect_family(&["multilingual-e5-large"], None), Some("e5"));
        assert_eq!(
            detect_family(&["stable-diffusion-xl"], None),
            Some("stable_diffusion")
        );
        assert_eq!(detect_family(&["x"], Some("gemma3")), Some("gemma3"));
        assert_eq!(
            detect_family(&["DeepSeek-R1-Distill"], None),
            Some("deepseek")
        );
        assert_eq!(
            detect_type("DeepSeek-R1-Distill-Qwen-7B", Some("qwen2"), None),
            Some("thinking")
        );
        assert_eq!(
            detect_type("nomic-embed", Some("nomic-bert"), None),
            Some("embedding")
        );
        assert_eq!(detect_type("bge-reranker-v2", None, None), Some("rerank"));
        assert_eq!(detect_type("whisper-large-v3", None, None), Some("stt"));
        assert_eq!(detect_type("snapshot-abc", None, None), None);
    }

    #[test]
    fn scan_errors_and_limits() {
        let c = CancellationToken::new();
        let e = scan_blocking(&ScanPathArgs::new("relative/path"), &c).unwrap_err();
        assert_eq!(e.code, error_code::INVALID_ARGS);
        let e = scan_blocking(&ScanPathArgs::new("/definitely/not/here"), &c).unwrap_err();
        assert_eq!(e.code, error_code::PATH_NOT_FOUND);
        let d = tempfile::tempdir().unwrap();
        for i in 0..20 {
            write(&d.path().join(format!("d{i}/x.txt")), b"x");
        }
        let mut a = ScanPathArgs::new(d.path().display().to_string());
        a.max_entries = 5;
        let r = scan_blocking(&a, &c).unwrap();
        assert!(r.truncated);
        assert!(expand_path("~").unwrap().is_absolute());
    }

    /// Real stores on this machine (`~/.ollama/models`, `~/.lmstudio`): `cargo test -p gpudeck-agent
    /// live_scan_home -- --ignored --nocapture`.
    #[test]
    #[ignore = "reads the developer's real model stores"]
    fn live_scan_home() {
        for p in ["~/.ollama/models", "~/.lmstudio"] {
            let Ok(r) = scan_blocking(&ScanPathArgs::new(p), &CancellationToken::new()) else {
                continue;
            };
            println!(
                "{p}: {} candidates, visited {}, {:.3}s",
                r.candidates.len(),
                r.visited,
                r.seconds
            );
            for c in r.candidates.iter().take(8) {
                println!(
                    "  {} {} {} files={} {}B {:?}",
                    c.kind.as_str(),
                    c.format,
                    c.path,
                    c.files.len(),
                    c.total_size_bytes,
                    c.suggested
                );
            }
        }
    }

    #[tokio::test]
    async fn hashing() {
        let d = tempfile::tempdir().unwrap();
        write(&d.path().join("a.bin"), b"hello");
        write(&d.path().join("sub/b.bin"), &vec![7u8; 3 << 20]);
        let progress = Arc::new(std::sync::Mutex::new(vec![]));
        let p2 = progress.clone();
        let ctx = Ctx::new(
            uuid::Uuid::new_v4(),
            CancellationToken::new(),
            Arc::new(move |pct, m| p2.lock().unwrap().push((pct, m))),
        );
        let mut args = HashFilesArgs::new(
            Some(d.path().display().to_string()),
            vec![
                "a.bin".into(),
                "sub/b.bin".into(),
                "../x".into(),
                "missing".into(),
            ],
        );
        args.progress_interval_seconds = 0.2;
        let cache_dir = tempfile::tempdir().unwrap();
        let cm = Arc::new(
            crate::cache::CacheManager::open(cache_dir.path().to_path_buf(), Some(1 << 30), 1)
                .unwrap(),
        );
        let v = hash_files(&ctx, args, Some(cm.clone())).await.unwrap();
        let r: HashFilesResult = serde_json::from_value(v).unwrap();
        assert_eq!(r.hashes["a.bin"], sha(b"hello"));
        // Remembered for later loads (path + size + mtime), persisted with the cache.
        let a = d.path().join("a.bin");
        let meta = std::fs::metadata(&a).unwrap();
        assert_eq!(cm.known_hash(&a, &meta), Some(sha(b"hello")));
        let again =
            crate::cache::CacheManager::open(cache_dir.path().to_path_buf(), Some(1 << 30), 1)
                .unwrap();
        assert_eq!(again.known_hash(&a, &meta), Some(sha(b"hello")));
        assert_eq!(r.hashes["sub/b.bin"], sha(&vec![7u8; 3 << 20]));
        assert_eq!(
            r.files[2].error.as_ref().unwrap().code,
            error_code::INVALID_ARGS
        );
        assert_eq!(
            r.files[3].error.as_ref().unwrap().code,
            error_code::PATH_NOT_FOUND
        );
        assert_eq!(r.bytes_hashed, 5 + (3 << 20));
        // Absolute paths without a root.
        let abs = d.path().join("a.bin").display().to_string();
        let v = hash_files(&ctx, HashFilesArgs::new(None, vec![abs.clone()]), None)
            .await
            .unwrap();
        assert_eq!(v["hashes"][&abs], sha(b"hello"));
        // Cancelled before start.
        let cancel = CancellationToken::new();
        cancel.cancel();
        let ctx = Ctx::new(uuid::Uuid::new_v4(), cancel, Arc::new(|_, _| {}));
        let e = hash_files(&ctx, HashFilesArgs::new(None, vec![abs]), None)
            .await
            .unwrap_err();
        assert_eq!(e.code, error_code::CANCELLED);
    }
}
