//! Model cache (`protocol.md` §7): `<cache_dir>/<model_version_id>/<path>`, `.partial` while
//! downloading (resumable with HTTP Range), `.sha256ok` marker after verification, an index
//! (`.gpudeck-index.json`) of every item with `last_used_at` for LRU eviction, pinning, and
//! references to files that already exist on the node (Ollama blobs, LM Studio, any
//! `local_path` source) which are used in place and never copied or deleted.

use std::collections::{BTreeMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use futures::StreamExt;
use gpudeck_protocol::{
    error_code, CacheItem, CacheState, ErrorInfo, EvictCacheArgs, ModelFileFormat, ModelFileRef,
    ModelSource, ModelSourceKind, ModelSources, ModelSpec, PrefetchModelArgs, Timestamp,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio_util::sync::CancellationToken;
use uuid::Uuid;

const INDEX_FILE: &str = ".gpudeck-index.json";
/// sha256 of node files hashed before (`hash_files`, earlier verifications), keyed by path and
/// valid while size + mtime are unchanged: a load never re-reads tens of GB it already hashed.
const HASHES_FILE: &str = ".gpudeck-hashes.json";

/// A file's sha256 as of a size + mtime.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct KnownHash {
    pub size: u64,
    pub mtime_ns: u64,
    pub sha256: String,
}

fn mtime_ns(meta: &std::fs::Metadata) -> u64 {
    meta.modified()
        .ok()
        .and_then(|m| m.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_nanos() as u64)
        .unwrap_or(0)
}

fn path_key(p: &Path) -> String {
    std::fs::canonicalize(p)
        .unwrap_or_else(|_| p.to_path_buf())
        .display()
        .to_string()
}

fn save_index(dir: &Path, entries: &Mutex<BTreeMap<Uuid, Entry>>) {
    let list: Vec<Entry> = entries
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .values()
        .cloned()
        .collect();
    if let Err(e) = crate::state::write_json(&dir.join(INDEX_FILE), &list, 0o600) {
        tracing::warn!("cache index save failed: {e}");
    }
}

fn save_hashes(dir: &Path, hashes: &Mutex<BTreeMap<String, KnownHash>>) {
    let snapshot = hashes.lock().unwrap_or_else(|e| e.into_inner()).clone();
    if let Err(e) = crate::state::write_json(&dir.join(HASHES_FILE), &snapshot, 0o600) {
        tracing::warn!("hash store save failed: {e}");
    }
}
const PICKLE_EXT: &[&str] = &["bin", "pt", "pth", "pkl", "pickle", "ckpt"];

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Entry {
    pub item: CacheItem,
    /// File that existed before (Ollama blob, LM Studio, local_path source): never deleted.
    #[serde(default)]
    pub external: bool,
    #[serde(default)]
    pub pin_reason: Option<String>,
    /// `size:mtime` of an external file whose sha256 was verified.
    #[serde(default)]
    pub verified_stamp: Option<String>,
}

pub type Progress<'a> = &'a (dyn Fn(f64, String) + Send + Sync);

/// Callback fired on every cache item state change.
pub type ChangeHook = Arc<dyn Fn(&CacheItem) + Send + Sync>;

/// A model resolved to local files.
#[derive(Debug, Clone)]
pub struct Resolved {
    pub files: Vec<(ModelFileRef, PathBuf)>,
    /// First required GGUF (or first file) — `{{model_path}}`.
    pub primary: PathBuf,
    /// Directory holding the files — `{{model_dir}}`.
    pub dir: PathBuf,
    pub bytes_downloaded: u64,
}

pub struct CacheManager {
    pub dir: PathBuf,
    entries: Arc<Mutex<BTreeMap<Uuid, Entry>>>,
    hashes: Arc<Mutex<BTreeMap<String, KnownHash>>>,
    /// `verify_local_files`: `background` | `blocking` | `off`.
    verify_mode: Mutex<String>,
    /// One background verification at a time (disk bandwidth for the engines).
    verify_slot: Arc<tokio::sync::Semaphore>,
    limit: AtomicU64,
    min_free: AtomicU64,
    sources: Mutex<ModelSources>,
    http: reqwest::Client,
    downloads: Arc<tokio::sync::Semaphore>,
    /// Emits `cache_changed` (set by the agent).
    pub on_change: Mutex<Option<ChangeHook>>,
}

impl CacheManager {
    pub fn open(dir: PathBuf, limit: Option<u64>, max_downloads: usize) -> anyhow::Result<Self> {
        std::fs::create_dir_all(&dir)?;
        let limit = limit.unwrap_or_else(|| {
            crate::system::disk_total(&dir)
                .map(|t| t / 10 * 7)
                .unwrap_or(0)
        });
        let mut entries: BTreeMap<Uuid, Entry> =
            crate::state::read_json::<Vec<Entry>>(&dir.join(INDEX_FILE))
                .ok()
                .flatten()
                .unwrap_or_default()
                .into_iter()
                .map(|e| (e.item.model_file_id, e))
                .collect();
        for e in entries.values_mut() {
            if e.item.state == CacheState::Ready && !Path::new(&e.item.local_path).exists() {
                e.item.state = CacheState::Evicted;
            }
            if matches!(
                e.item.state,
                CacheState::Downloading | CacheState::Queued | CacheState::Evicting
            ) {
                // Interrupted by an agent restart; resumes from .partial on the next request.
                e.item.state = CacheState::Error;
                e.item.error_message = Some("interrupted by agent restart".into());
            }
        }
        entries.retain(|_, e| e.item.state != CacheState::Evicted);
        let http = crate::tls::http_client_builder()
            .connect_timeout(Duration::from_secs(30))
            .read_timeout(Duration::from_secs(120))
            .build()?;
        let hashes: BTreeMap<String, KnownHash> = crate::state::read_json(&dir.join(HASHES_FILE))
            .ok()
            .flatten()
            .unwrap_or_default();
        let me = Self {
            dir,
            entries: Arc::new(Mutex::new(entries)),
            hashes: Arc::new(Mutex::new(hashes)),
            verify_mode: Mutex::new("background".into()),
            verify_slot: Arc::new(tokio::sync::Semaphore::new(1)),
            limit: AtomicU64::new(limit),
            min_free: AtomicU64::new(0),
            sources: Mutex::new(ModelSources::default()),
            http,
            downloads: Arc::new(tokio::sync::Semaphore::new(max_downloads.max(1))),
            on_change: Mutex::new(None),
        };
        me.save();
        Ok(me)
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, BTreeMap<Uuid, Entry>> {
        self.entries.lock().unwrap_or_else(|e| e.into_inner())
    }

    pub fn save(&self) {
        save_index(&self.dir, &self.entries);
    }

    /// `verify_local_files` (agent.toml): `background` (default), `blocking`, `off`.
    pub fn set_verify_mode(&self, mode: &str) {
        *self.verify_mode.lock().unwrap_or_else(|e| e.into_inner()) = mode.to_string();
    }

    fn verify_mode(&self) -> String {
        self.verify_mode
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .clone()
    }

    /// Remember the sha256 of `path` (as of its current size + mtime), e.g. from `hash_files`.
    pub fn record_hash(&self, path: &Path, sha256: &str) {
        let Ok(meta) = std::fs::metadata(path) else {
            return;
        };
        self.hashes
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .insert(
                path_key(path),
                KnownHash {
                    size: meta.len(),
                    mtime_ns: mtime_ns(&meta),
                    sha256: sha256.to_string(),
                },
            );
    }

    pub fn save_hashes(&self) {
        save_hashes(&self.dir, &self.hashes);
    }

    /// The sha256 of `path` if it was hashed before and has not changed since.
    pub fn known_hash(&self, path: &Path, meta: &std::fs::Metadata) -> Option<String> {
        self.hashes
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .get(&path_key(path))
            .filter(|k| k.size == meta.len() && k.mtime_ns == mtime_ns(meta))
            .map(|k| k.sha256.clone())
    }

    pub fn set_limits(&self, limit: Option<u64>, min_free: Option<u64>) {
        if let Some(l) = limit {
            self.limit.store(l, Ordering::Relaxed);
        }
        if let Some(m) = min_free {
            self.min_free.store(m, Ordering::Relaxed);
        }
    }

    pub fn set_sources(&self, s: ModelSources) {
        *self.sources.lock().unwrap_or_else(|e| e.into_inner()) = s;
    }

    pub fn limit_bytes(&self) -> u64 {
        self.limit.load(Ordering::Relaxed)
    }

    pub fn min_free_bytes(&self) -> u64 {
        self.min_free.load(Ordering::Relaxed)
    }

    pub fn items(&self) -> Vec<CacheItem> {
        self.lock().values().map(|e| e.item.clone()).collect()
    }

    /// Bytes the cache occupies on disk (external references are not counted).
    pub fn used_bytes(&self) -> u64 {
        self.lock()
            .values()
            .filter(|e| !e.external && e.item.state == CacheState::Ready)
            .map(|e| e.item.size_bytes)
            .sum()
    }

    pub fn free_bytes(&self) -> Option<u64> {
        crate::system::disk_free(&self.dir)
    }

    fn update(&self, id: Uuid, f: impl FnOnce(&mut Entry)) -> Option<CacheItem> {
        let item = {
            let mut g = self.lock();
            let e = g.get_mut(&id)?;
            f(e);
            e.item.clone()
        };
        let cb = self
            .on_change
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .clone();
        if let Some(cb) = cb {
            cb(&item);
        }
        Some(item)
    }

    /// Mark files as used now (LRU).
    pub fn touch(&self, ids: &[Uuid]) {
        let now = gpudeck_protocol::now();
        {
            let mut g = self.lock();
            for id in ids {
                if let Some(e) = g.get_mut(id) {
                    e.item.last_used_at = Some(now);
                }
            }
        }
        self.save();
    }

    fn version_dir(&self, model_version_id: Uuid, requested: &str) -> PathBuf {
        let req = PathBuf::from(requested);
        if !requested.is_empty()
            && req.is_absolute()
            && req.starts_with(&self.dir)
            && !requested.contains("..")
        {
            req
        } else {
            if !requested.is_empty() {
                tracing::warn!(
                    requested,
                    "cache_dir outside the agent cache root; using {}",
                    self.dir.display()
                );
            }
            self.dir.join(model_version_id.to_string())
        }
    }

    pub fn is_pickle(f: &ModelFileRef) -> bool {
        f.format == Some(ModelFileFormat::Pickle)
            || Path::new(&f.path)
                .extension()
                .and_then(|e| e.to_str())
                .map(|e| PICKLE_EXT.contains(&e.to_ascii_lowercase().as_str()))
                .unwrap_or(false)
    }

    /// Make every required file of `spec` available locally.
    pub async fn ensure_model(
        &self,
        spec: &ModelSpec,
        pin: bool,
        cancel: &CancellationToken,
        progress: Progress<'_>,
    ) -> Result<Resolved, ErrorInfo> {
        let files: Vec<ModelFileRef> = spec
            .files
            .iter()
            .filter(|f| f.is_required)
            .cloned()
            .collect();
        let files = if files.is_empty() {
            spec.files.clone()
        } else {
            files
        };
        self.ensure_files(
            spec.model_version_id,
            &files,
            &spec.cache_dir,
            spec.source.as_ref(),
            spec.allow_pickle,
            pin,
            None,
            None,
            cancel,
            progress,
        )
        .await
    }

    #[allow(clippy::too_many_arguments)]
    pub async fn ensure_files(
        &self,
        model_version_id: Uuid,
        files: &[ModelFileRef],
        cache_dir: &str,
        source: Option<&ModelSource>,
        allow_pickle: bool,
        pin: bool,
        pin_reason: Option<String>,
        max_bps: Option<u64>,
        cancel: &CancellationToken,
        progress: Progress<'_>,
    ) -> Result<Resolved, ErrorInfo> {
        if files.is_empty() {
            return Err(ErrorInfo::new(
                error_code::INVALID_ARGS,
                "model has no files",
            ));
        }
        if !allow_pickle {
            if let Some(f) = files.iter().find(|f| Self::is_pickle(f)) {
                return Err(ErrorInfo::new(
                    error_code::PICKLE_BLOCKED,
                    format!(
                        "{} is a pickle-based weight file; allow_pickle is off",
                        f.path
                    ),
                ));
            }
        }
        let vdir = self.version_dir(model_version_id, cache_dir);
        let local_root = source
            .filter(|s| s.kind == ModelSourceKind::LocalPath)
            .map(|s| crate::template::expand_home(&s.repo_id));
        let total: u64 = files.iter().map(|f| f.size_bytes).sum::<u64>().max(1);
        let mut done_bytes = 0u64;
        let mut downloaded = 0u64;
        let mut out = vec![];
        for f in files {
            if cancel.is_cancelled() {
                return Err(ErrorInfo::new(error_code::CANCELLED, "cancelled"));
            }
            let base = done_bytes;
            let report = |b: u64, msg: String| {
                progress(((base + b) as f64 * 100.0 / total as f64).min(100.0), msg)
            };
            let path = match &local_root {
                Some(root) => {
                    self.reference_local(
                        model_version_id,
                        f,
                        root,
                        pin,
                        pin_reason.clone(),
                        &report,
                    )
                    .await?
                }
                None => {
                    let (p, n) = self
                        .fetch(
                            model_version_id,
                            f,
                            &vdir,
                            source,
                            pin,
                            pin_reason.clone(),
                            max_bps,
                            cancel,
                            &report,
                        )
                        .await?;
                    downloaded += n;
                    p
                }
            };
            done_bytes += f.size_bytes;
            out.push((f.clone(), path));
        }
        self.save();
        let primary = out
            .iter()
            .find(|(f, p)| {
                f.format == Some(ModelFileFormat::Gguf)
                    || p.extension().is_some_and(|e| e == "gguf")
            })
            .map(|(_, p)| p.clone())
            .unwrap_or_else(|| out[0].1.clone());
        let dir = match &local_root {
            Some(r) if r.is_dir() => r.clone(),
            _ => vdir,
        };
        Ok(Resolved {
            files: out,
            primary,
            dir,
            bytes_downloaded: downloaded,
        })
    }

    async fn reference_local(
        &self,
        mvid: Uuid,
        f: &ModelFileRef,
        root: &Path,
        pin: bool,
        pin_reason: Option<String>,
        report: &(dyn Fn(u64, String) + Sync),
    ) -> Result<PathBuf, ErrorInfo> {
        let p = if Path::new(&f.path).is_absolute() {
            PathBuf::from(&f.path)
        } else {
            root.join(&f.path)
        };
        let p = if !p.exists() && root.is_file() {
            root.to_path_buf()
        } else {
            p
        };
        let meta = std::fs::metadata(&p).map_err(|e| {
            ErrorInfo::new(
                error_code::CACHE_ITEM_NOT_FOUND,
                format!("local model file {}: {e}", p.display()),
            )
        })?;
        let stamp = format!(
            "{}:{}",
            meta.len(),
            meta.modified()
                .ok()
                .and_then(|m| m.duration_since(std::time::UNIX_EPOCH).ok())
                .map(|d| d.as_secs())
                .unwrap_or(0)
        );
        let already = self
            .lock()
            .get(&f.model_file_id)
            .and_then(|e| e.verified_stamp.clone())
            == Some(stamp.clone());
        let mut verified = already;
        let mut verify_later = None;
        if let (Some(expected), false) = (&f.sha256, already) {
            let mode = self.verify_mode();
            let content_addressed = p
                .file_name()
                .and_then(|n| n.to_str())
                .is_some_and(|n| n.strip_prefix("sha256-") == Some(expected.as_str()));
            if content_addressed {
                // Ollama blobs are named by their digest (verified by Ollama on pull).
                verified = true;
            } else if let Some(got) = self.known_hash(&p, &meta) {
                // Hashed before (hash_files at registration, an earlier load): no re-read.
                if &got != expected {
                    return Err(ErrorInfo::new(
                        error_code::MODEL_CHECKSUM_MISMATCH,
                        format!("{}: sha256 {got} != {expected}", p.display()),
                    ));
                }
                verified = true;
            } else if mode == "off" {
                tracing::debug!(path = %p.display(), "sha256 not verified (verify_local_files = off)");
            } else if mode == "background" && (f.size_bytes == 0 || f.size_bytes == meta.len()) {
                // In place, owned by the node operator: start the engine now and verify while
                // it loads (a 30 GB checkpoint is ~25 s of hashing); a mismatch is reported.
                verify_later = Some(expected.clone());
            } else {
                report(0, format!("verifying {}", p.display()));
                let t = Instant::now();
                let got = sha256_file(&p)
                    .await
                    .map_err(|e| ErrorInfo::new(error_code::INTERNAL, e.to_string()))?;
                tracing::info!(path = %p.display(), "sha256 verified in {:.1}s", t.elapsed().as_secs_f64());
                self.record_hash(&p, &got);
                self.save_hashes();
                if &got != expected {
                    return Err(ErrorInfo::new(
                        error_code::MODEL_CHECKSUM_MISMATCH,
                        format!("{}: sha256 {got} != {expected}", p.display()),
                    ));
                }
                verified = true;
            }
        }
        let item = CacheItem {
            model_file_id: f.model_file_id,
            model_version_id: mvid,
            local_path: p.display().to_string(),
            size_bytes: meta.len(),
            state: CacheState::Ready,
            progress_pct: 100.0,
            sha256_verified: verified,
            pinned: pin
                || self
                    .lock()
                    .get(&f.model_file_id)
                    .is_some_and(|e| e.item.pinned),
            last_used_at: Some(gpudeck_protocol::now()),
            error_message: None,
        };
        self.lock().insert(
            f.model_file_id,
            Entry {
                item,
                external: true,
                pin_reason,
                verified_stamp: verified.then(|| stamp.clone()),
            },
        );
        self.update(f.model_file_id, |_| {});
        if let Some(expected) = verify_later {
            self.verify_in_background(f.model_file_id, p.clone(), expected, stamp);
        }
        report(f.size_bytes, format!("using local file {}", p.display()));
        Ok(p)
    }

    /// sha256 of an in-place local file after the load went ahead: records the hash and marks
    /// the item verified, or reports the mismatch on the item (`cache_changed`) and in the log.
    fn verify_in_background(&self, id: Uuid, path: PathBuf, expected: String, stamp: String) {
        let Ok(rt) = tokio::runtime::Handle::try_current() else {
            return;
        };
        let entries = self.entries.clone();
        let hashes = self.hashes.clone();
        let dir = self.dir.clone();
        let slot = self.verify_slot.clone();
        let hook = self
            .on_change
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .clone();
        rt.spawn(async move {
            let _permit = slot.acquire_owned().await;
            let t = Instant::now();
            let got = match sha256_file(&path).await {
                Ok(h) => h,
                Err(e) => {
                    // Agent shutdown cancels it; the next load verifies again.
                    tracing::info!(path = %path.display(), "background sha256 not finished: {e}");
                    return;
                }
            };
            if let Ok(meta) = std::fs::metadata(&path) {
                hashes.lock().unwrap_or_else(|e| e.into_inner()).insert(
                    path_key(&path),
                    KnownHash {
                        size: meta.len(),
                        mtime_ns: mtime_ns(&meta),
                        sha256: got.clone(),
                    },
                );
                save_hashes(&dir, &hashes);
            }
            let ok = got == expected;
            if ok {
                tracing::info!(path = %path.display(), "sha256 verified in the background in {:.1}s", t.elapsed().as_secs_f64());
            } else {
                tracing::error!(path = %path.display(), "sha256 mismatch: {got} != registered {expected} (the file changed after it was registered)");
            }
            let item = {
                let mut g = entries.lock().unwrap_or_else(|e| e.into_inner());
                g.get_mut(&id).map(|e| {
                    if ok {
                        e.verified_stamp = Some(stamp);
                        e.item.sha256_verified = true;
                        e.item.error_message = None;
                    } else {
                        e.item.sha256_verified = false;
                        e.item.error_message = Some(format!(
                            "sha256 mismatch: {} is {got}, registered {expected}",
                            path.display()
                        ));
                    }
                    e.item.clone()
                })
            };
            save_index(&dir, &entries);
            if let (Some(cb), Some(item)) = (hook, item) {
                cb(&item);
            }
        });
    }

    #[allow(clippy::too_many_arguments)]
    async fn fetch(
        &self,
        mvid: Uuid,
        f: &ModelFileRef,
        vdir: &Path,
        source: Option<&ModelSource>,
        pin: bool,
        pin_reason: Option<String>,
        max_bps: Option<u64>,
        cancel: &CancellationToken,
        report: &(dyn Fn(u64, String) + Sync),
    ) -> Result<(PathBuf, u64), ErrorInfo> {
        let rel = Path::new(&f.path);
        if rel.is_absolute() || f.path.split('/').any(|c| c == "..") {
            return Err(ErrorInfo::new(
                error_code::INVALID_ARGS,
                format!("invalid file path {}", f.path),
            ));
        }
        let dest = vdir.join(rel);
        let marker = marker_path(&dest);
        let have = dest.exists()
            && (f.sha256.is_none()
                || std::fs::read_to_string(&marker)
                    .map(|m| Some(m.trim().to_string()) == f.sha256)
                    .unwrap_or(false));
        let mk = |state, pct: f64, err: Option<String>, verified: bool| CacheItem {
            model_file_id: f.model_file_id,
            model_version_id: mvid,
            local_path: dest.display().to_string(),
            size_bytes: f.size_bytes,
            state,
            progress_pct: pct,
            sha256_verified: verified,
            pinned: pin,
            last_used_at: Some(gpudeck_protocol::now()),
            error_message: err,
        };
        let prev_pinned = self
            .lock()
            .get(&f.model_file_id)
            .is_some_and(|e| e.item.pinned);
        if have {
            let mut item = mk(CacheState::Ready, 100.0, None, f.sha256.is_some());
            item.pinned |= prev_pinned;
            self.lock().insert(
                f.model_file_id,
                Entry {
                    item,
                    external: false,
                    pin_reason,
                    verified_stamp: None,
                },
            );
            report(f.size_bytes, format!("{} cached", f.path));
            return Ok((dest, 0));
        }
        let Some(src) = source else {
            return Err(ErrorInfo::new(
                error_code::CACHE_ITEM_NOT_FOUND,
                format!("{} is not cached and the model has no source", f.path),
            ));
        };
        let _permit = tokio::select! {
            p = self.downloads.clone().acquire_owned() => p.map_err(|_| ErrorInfo::new(error_code::INTERNAL, "download semaphore closed"))?,
            _ = cancel.cancelled() => return Err(ErrorInfo::new(error_code::CANCELLED, "cancelled")),
        };
        // Space: remaining bytes + min_free must fit; try an LRU sweep first.
        let partial = partial_path(&dest);
        let have_partial = std::fs::metadata(&partial).map(|m| m.len()).unwrap_or(0);
        let need = f.size_bytes.saturating_sub(have_partial) + self.min_free_bytes();
        if let Some(free) = self.free_bytes() {
            if free < need {
                self.sweep_lru(need - free, &HashSet::new());
                if self.free_bytes().unwrap_or(0) < need {
                    return Err(ErrorInfo::new(
                        error_code::INSUFFICIENT_DISK,
                        format!("need {need} bytes free for {}, have {free}", f.path),
                    ));
                }
            }
        }
        let mut item = mk(CacheState::Downloading, 0.0, None, false);
        item.pinned |= prev_pinned;
        self.lock().insert(
            f.model_file_id,
            Entry {
                item,
                external: false,
                pin_reason,
                verified_stamp: None,
            },
        );
        self.update(f.model_file_id, |_| {});
        let url = download_url(
            src,
            &f.path,
            &self
                .sources
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .clone(),
        );
        let res = self
            .download(
                &url,
                src.token.as_deref(),
                &partial,
                f.size_bytes,
                max_bps,
                cancel,
                &|b| {
                    let pct = if f.size_bytes > 0 {
                        b as f64 * 100.0 / f.size_bytes as f64
                    } else {
                        0.0
                    };
                    self.lock()
                        .entry(f.model_file_id)
                        .and_modify(|e| e.item.progress_pct = pct.min(100.0));
                    report(b, format!("downloading {} ({:.0}%)", f.path, pct));
                },
            )
            .await;
        let n = match res {
            Ok(n) => n,
            Err(e) => {
                let msg = e.message.clone();
                self.update(f.model_file_id, |en| {
                    en.item.state = CacheState::Error;
                    en.item.error_message = Some(msg);
                });
                return Err(e);
            }
        };
        if let Some(expected) = &f.sha256 {
            report(f.size_bytes, format!("verifying {}", f.path));
            let got = sha256_file(&partial)
                .await
                .map_err(|e| ErrorInfo::new(error_code::INTERNAL, e.to_string()))?;
            if &got != expected {
                let _ = std::fs::remove_file(&partial);
                self.update(f.model_file_id, |en| {
                    en.item.state = CacheState::Error;
                    en.item.error_message = Some("sha256 mismatch".into());
                });
                return Err(ErrorInfo::new(
                    error_code::MODEL_CHECKSUM_MISMATCH,
                    format!("{}: sha256 {got} != {expected}", f.path),
                ));
            }
            let _ = std::fs::write(&marker, &got);
        }
        std::fs::rename(&partial, &dest).map_err(|e| {
            ErrorInfo::new(
                error_code::INTERNAL,
                format!("rename {}: {e}", dest.display()),
            )
        })?;
        let size = std::fs::metadata(&dest)
            .map(|m| m.len())
            .unwrap_or(f.size_bytes);
        self.update(f.model_file_id, |en| {
            en.item.state = CacheState::Ready;
            en.item.progress_pct = 100.0;
            en.item.size_bytes = size;
            en.item.sha256_verified = f.sha256.is_some();
            en.item.error_message = None;
        });
        Ok((dest, n))
    }

    /// Resumable GET into `partial`. Returns bytes transferred in this call.
    #[allow(clippy::too_many_arguments)]
    async fn download(
        &self,
        url: &str,
        token: Option<&str>,
        partial: &Path,
        expected: u64,
        max_bps: Option<u64>,
        cancel: &CancellationToken,
        tick: &(dyn Fn(u64) + Sync),
    ) -> Result<u64, ErrorInfo> {
        let fail = |m: String| ErrorInfo::new(error_code::MODEL_DOWNLOAD_FAILED, m).retryable();
        if let Some(d) = partial.parent() {
            std::fs::create_dir_all(d).map_err(|e| fail(format!("create {}: {e}", d.display())))?;
        }
        let mut have = std::fs::metadata(partial).map(|m| m.len()).unwrap_or(0);
        if expected > 0 && have == expected {
            return Ok(0);
        }
        let mut req = self.http.get(url);
        if let Some(t) = token {
            req = req.bearer_auth(t);
        }
        if have > 0 {
            req = req.header(reqwest::header::RANGE, format!("bytes={have}-"));
        }
        let resp = tokio::select! {
            r = req.send() => r.map_err(|e| fail(format!("GET {}: {e}", redact_url(url))))?,
            _ = cancel.cancelled() => return Err(ErrorInfo::new(error_code::CANCELLED, "cancelled")),
        };
        let status = resp.status();
        if !(status.is_success()) {
            let mut e = fail(format!("GET {}: HTTP {status}", redact_url(url)));
            e.retryable = status.is_server_error() || status.as_u16() == 429;
            return Err(e);
        }
        let mut file = if status.as_u16() == 206 {
            tokio::fs::OpenOptions::new()
                .append(true)
                .open(partial)
                .await
        } else {
            have = 0;
            tokio::fs::File::create(partial).await
        }
        .map_err(|e| fail(format!("open {}: {e}", partial.display())))?;
        let mut stream = resp.bytes_stream();
        let started = Instant::now();
        let mut got = 0u64;
        let mut last_tick = Instant::now();
        loop {
            let chunk = tokio::select! {
                c = stream.next() => c,
                _ = cancel.cancelled() => { let _ = file.flush().await; return Err(ErrorInfo::new(error_code::CANCELLED, "cancelled")); }
            };
            let Some(chunk) = chunk else { break };
            let chunk = chunk.map_err(|e| fail(format!("read {}: {e}", redact_url(url))))?;
            file.write_all(&chunk)
                .await
                .map_err(|e| fail(format!("write: {e}")))?;
            got += chunk.len() as u64;
            if let Some(bps) = max_bps.filter(|b| *b > 0) {
                let should = Duration::from_secs_f64(got as f64 / bps as f64);
                let el = started.elapsed();
                if should > el {
                    tokio::time::sleep(should - el).await;
                }
            }
            if last_tick.elapsed() > Duration::from_millis(500) {
                tick(have + got);
                last_tick = Instant::now();
            }
        }
        file.flush()
            .await
            .map_err(|e| fail(format!("flush: {e}")))?;
        tick(have + got);
        if expected > 0 && have + got != expected {
            return Err(fail(format!(
                "size mismatch for {}: got {} of {expected} bytes",
                redact_url(url),
                have + got
            )));
        }
        Ok(got)
    }

    /// Prefetch command.
    pub async fn prefetch(
        &self,
        a: &PrefetchModelArgs,
        cancel: &CancellationToken,
        progress: Progress<'_>,
    ) -> Result<serde_json::Value, ErrorInfo> {
        let started = Instant::now();
        let r = self
            .ensure_files(
                a.model_version_id,
                &a.files,
                &a.cache_dir,
                a.source.as_ref(),
                a.allow_pickle,
                a.pin,
                a.pin_reason.clone(),
                a.max_bandwidth_bytes_per_s,
                cancel,
                progress,
            )
            .await?;
        let g = self.lock();
        let files: Vec<serde_json::Value> = r
            .files
            .iter()
            .map(|(f, p)| serde_json::json!({"model_file_id": f.model_file_id, "local_path": p.display().to_string(), "size_bytes": g.get(&f.model_file_id).map(|e| e.item.size_bytes).unwrap_or(f.size_bytes), "sha256_verified": g.get(&f.model_file_id).map(|e| e.item.sha256_verified).unwrap_or(false)}))
            .collect();
        Ok(
            serde_json::json!({"model_version_id": a.model_version_id, "files": files, "bytes_downloaded": r.bytes_downloaded, "seconds": started.elapsed().as_secs_f64()}),
        )
    }

    /// Evict command. `in_use` = files of running instances.
    pub fn evict(
        &self,
        a: &EvictCacheArgs,
        in_use: &HashSet<Uuid>,
    ) -> Result<serde_json::Value, ErrorInfo> {
        let selectors = [
            a.model_version_id.is_some(),
            a.model_file_ids.is_some(),
            a.unpinned_older_than_seconds.is_some(),
            a.target_free_bytes.is_some(),
        ]
        .iter()
        .filter(|b| **b)
        .count();
        if selectors != 1 {
            return Err(ErrorInfo::new(error_code::INVALID_ARGS, "exactly one of model_version_id, model_file_ids, unpinned_older_than_seconds, target_free_bytes"));
        }
        if let Some(target) = a.target_free_bytes {
            let free = self.free_bytes().unwrap_or(0);
            let (ids, freed) = if free >= target {
                (vec![], 0)
            } else {
                self.sweep_lru(target - free, in_use)
            };
            return Ok(serde_json::json!({"evicted": ids, "freed_bytes": freed}));
        }
        let now = gpudeck_protocol::now();
        let selected: Vec<Uuid> = {
            let g = self.lock();
            g.values()
                .filter(|e| {
                    match (
                        &a.model_version_id,
                        &a.model_file_ids,
                        a.unpinned_older_than_seconds,
                    ) {
                        (Some(v), _, _) => e.item.model_version_id == *v,
                        (_, Some(ids), _) => ids.contains(&e.item.model_file_id),
                        (_, _, Some(age)) => {
                            !e.item.pinned
                                && e.item
                                    .last_used_at
                                    .map(|t| (now - t).num_seconds() >= age as i64)
                                    .unwrap_or(true)
                        }
                        _ => false,
                    }
                })
                .map(|e| e.item.model_file_id)
                .collect()
        };
        if selected.is_empty() && (a.model_version_id.is_some() || a.model_file_ids.is_some()) {
            return Err(ErrorInfo::new(
                error_code::CACHE_ITEM_NOT_FOUND,
                "no cached files match",
            ));
        }
        if a.unpin {
            for id in &selected {
                self.update(*id, |e| {
                    e.item.pinned = false;
                    e.pin_reason = None;
                });
            }
            self.save();
            return Ok(serde_json::json!({"evicted": [], "freed_bytes": 0, "unpinned": selected}));
        }
        let busy: Vec<Uuid> = selected
            .iter()
            .filter(|id| in_use.contains(id))
            .cloned()
            .collect();
        if !busy.is_empty() && !a.force {
            return Err(ErrorInfo::new(
                error_code::CACHE_IN_USE,
                format!("{} file(s) are used by a running instance", busy.len()),
            )
            .with_details(
                serde_json::json!({"model_file_ids": busy})
                    .as_object()
                    .cloned()
                    .unwrap_or_default(),
            ));
        }
        let mut freed = 0;
        for id in &selected {
            freed += self.remove(*id);
        }
        self.save();
        Ok(serde_json::json!({"evicted": selected, "freed_bytes": freed}))
    }

    fn remove(&self, id: Uuid) -> u64 {
        let Some(e) = self.lock().get(&id).cloned() else {
            return 0;
        };
        self.update(id, |e| e.item.state = CacheState::Evicting);
        let mut freed = 0;
        if !e.external {
            let p = PathBuf::from(&e.item.local_path);
            if std::fs::remove_file(&p).is_ok() {
                freed = e.item.size_bytes;
            }
            let _ = std::fs::remove_file(marker_path(&p));
            let _ = std::fs::remove_file(partial_path(&p));
            // Remove empty parent dirs up to the cache root.
            let mut d = p.parent().map(Path::to_path_buf);
            while let Some(dir) = d {
                if dir == self.dir
                    || !dir.starts_with(&self.dir)
                    || std::fs::remove_dir(&dir).is_err()
                {
                    break;
                }
                d = dir.parent().map(Path::to_path_buf);
            }
        }
        self.update(id, |e| e.item.state = CacheState::Evicted);
        self.lock().remove(&id);
        freed
    }

    /// Evict unpinned, unused items, least recently used first, until `bytes` are freed.
    pub fn sweep_lru(&self, bytes: u64, in_use: &HashSet<Uuid>) -> (Vec<Uuid>, u64) {
        let mut cands: Vec<(Option<Timestamp>, Uuid, u64)> = self
            .lock()
            .values()
            .filter(|e| {
                !e.item.pinned
                    && !e.external
                    && e.item.state == CacheState::Ready
                    && !in_use.contains(&e.item.model_file_id)
            })
            .map(|e| (e.item.last_used_at, e.item.model_file_id, e.item.size_bytes))
            .collect();
        cands.sort_by_key(|c| c.0);
        let (mut freed, mut ids) = (0, vec![]);
        for (_, id, _) in cands {
            if freed >= bytes {
                break;
            }
            freed += self.remove(id);
            ids.push(id);
        }
        if !ids.is_empty() {
            self.save();
        }
        (ids, freed)
    }

    /// Enforce `cache_limit_bytes` and `cache_min_free_bytes`. Returns bytes freed.
    pub fn enforce(&self, in_use: &HashSet<Uuid>) -> u64 {
        let mut want = 0;
        let used = self.used_bytes();
        let limit = self.limit_bytes();
        if limit > 0 && used > limit {
            want = used - limit;
        }
        let min_free = self.min_free_bytes();
        if let Some(free) = self.free_bytes() {
            if min_free > free {
                want = want.max(min_free - free);
            }
        }
        if want == 0 {
            return 0;
        }
        self.sweep_lru(want, in_use).1
    }
}

pub fn partial_path(p: &Path) -> PathBuf {
    let mut s = p.as_os_str().to_owned();
    s.push(".partial");
    PathBuf::from(s)
}

pub fn marker_path(p: &Path) -> PathBuf {
    let mut s = p.as_os_str().to_owned();
    s.push(".sha256ok");
    PathBuf::from(s)
}

fn redact_url(u: &str) -> String {
    u.split('?').next().unwrap_or(u).to_string()
}

/// Download URL for a file of a HF / ModelScope repo (mirror endpoints honoured).
pub fn download_url(src: &ModelSource, path: &str, mirrors: &ModelSources) -> String {
    let rev = src.revision.clone().unwrap_or_else(|| "main".into());
    let enc_path: String = path
        .split('/')
        .map(|c| urlencoding::encode(c).into_owned())
        .collect::<Vec<_>>()
        .join("/");
    match src.kind {
        ModelSourceKind::Modelscope => {
            let base = src
                .endpoint
                .clone()
                .or_else(|| mirrors.modelscope_endpoint.clone())
                .unwrap_or_else(|| "https://modelscope.cn".into());
            format!(
                "{}/api/v1/models/{}/repo?Revision={}&FilePath={}",
                base.trim_end_matches('/'),
                src.repo_id,
                urlencoding::encode(&rev),
                urlencoding::encode(path)
            )
        }
        _ => {
            let base = src
                .endpoint
                .clone()
                .or_else(|| mirrors.huggingface_endpoint.clone())
                .unwrap_or_else(|| "https://huggingface.co".into());
            format!(
                "{}/{}/resolve/{}/{}",
                base.trim_end_matches('/'),
                src.repo_id,
                urlencoding::encode(&rev),
                enc_path
            )
        }
    }
}

pub async fn sha256_file(p: &Path) -> std::io::Result<String> {
    let mut f = tokio::fs::File::open(p).await?;
    let mut h = Sha256::new();
    let mut buf = vec![0u8; 1 << 20];
    loop {
        let n = f.read(&mut buf).await?;
        if n == 0 {
            break;
        }
        h.update(&buf[..n]);
    }
    Ok(hex::encode(h.finalize()))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn file(path: &str, size: u64, sha: Option<String>) -> ModelFileRef {
        ModelFileRef {
            model_file_id: Uuid::new_v4(),
            path: path.into(),
            format: None,
            size_bytes: size,
            sha256: sha,
            is_required: true,
        }
    }

    #[test]
    fn urls() {
        let hf = ModelSource {
            kind: ModelSourceKind::Huggingface,
            repo_id: "Qwen/Qwen3-0.6B-GGUF".into(),
            revision: None,
            endpoint: None,
            token: None,
        };
        assert_eq!(
            download_url(&hf, "sub dir/q4.gguf", &ModelSources::default()),
            "https://huggingface.co/Qwen/Qwen3-0.6B-GGUF/resolve/main/sub%20dir/q4.gguf"
        );
        let mirrors = ModelSources {
            huggingface_endpoint: Some("https://hf-mirror.local/".into()),
            modelscope_endpoint: None,
        };
        assert!(download_url(&hf, "a.gguf", &mirrors).starts_with("https://hf-mirror.local/Qwen"));
        let ms = ModelSource {
            kind: ModelSourceKind::Modelscope,
            repo_id: "qwen/Qwen3".into(),
            revision: Some("v1".into()),
            endpoint: None,
            token: None,
        };
        assert_eq!(download_url(&ms, "a/b.safetensors", &ModelSources::default()), "https://modelscope.cn/api/v1/models/qwen/Qwen3/repo?Revision=v1&FilePath=a%2Fb.safetensors");
    }

    #[tokio::test]
    async fn local_reference_pickle_and_evict() {
        let dir = tempfile::tempdir().unwrap();
        let ext = tempfile::tempdir().unwrap();
        std::fs::write(ext.path().join("blob"), b"GGUFdata").unwrap();
        let sha = hex::encode(Sha256::digest(b"GGUFdata"));
        let cm = CacheManager::open(dir.path().join("cache"), Some(1 << 30), 2).unwrap();
        cm.set_verify_mode("blocking");
        let src = ModelSource {
            kind: ModelSourceKind::LocalPath,
            repo_id: ext.path().display().to_string(),
            revision: None,
            endpoint: None,
            token: None,
        };
        let f = file("blob", 8, Some(sha.clone()));
        let cancel = CancellationToken::new();
        let r = cm
            .ensure_files(
                Uuid::new_v4(),
                std::slice::from_ref(&f),
                "",
                Some(&src),
                false,
                false,
                None,
                None,
                &cancel,
                &|_, _| {},
            )
            .await
            .unwrap();
        assert_eq!(r.primary, ext.path().join("blob"));
        let items = cm.items();
        assert!(items[0].sha256_verified && items[0].state == CacheState::Ready);
        assert_eq!(cm.used_bytes(), 0, "external files do not count");
        // Bad checksum
        let bad = file("blob", 8, Some("0".repeat(64)));
        let e = cm
            .ensure_files(
                Uuid::new_v4(),
                &[bad],
                "",
                Some(&src),
                false,
                false,
                None,
                None,
                &cancel,
                &|_, _| {},
            )
            .await
            .unwrap_err();
        assert_eq!(e.code, error_code::MODEL_CHECKSUM_MISMATCH);
        // Pickle blocked
        let e = cm
            .ensure_files(
                Uuid::new_v4(),
                &[file("pytorch_model.bin", 1, None)],
                "",
                Some(&src),
                false,
                false,
                None,
                None,
                &cancel,
                &|_, _| {},
            )
            .await
            .unwrap_err();
        assert_eq!(e.code, error_code::PICKLE_BLOCKED);
        // In-use eviction refused, external never deleted
        let mut used = HashSet::new();
        used.insert(f.model_file_id);
        let ev = EvictCacheArgs {
            model_file_ids: Some(vec![f.model_file_id]),
            ..Default::default()
        };
        assert_eq!(
            cm.evict(&ev, &used).unwrap_err().code,
            error_code::CACHE_IN_USE
        );
        let res = cm.evict(&ev, &HashSet::new()).unwrap();
        assert_eq!(res["freed_bytes"], 0);
        assert!(ext.path().join("blob").exists());
        assert!(cm.items().is_empty());
    }

    #[tokio::test]
    async fn local_files_verified_in_the_background_and_remembered() {
        let dir = tempfile::tempdir().unwrap();
        let ext = tempfile::tempdir().unwrap();
        std::fs::write(ext.path().join("w.safetensors"), b"weights-1").unwrap();
        std::fs::write(ext.path().join("x.safetensors"), b"weights-2").unwrap();
        let sha = hex::encode(Sha256::digest(b"weights-1"));
        let cm = CacheManager::open(dir.path().join("cache"), Some(1 << 30), 2).unwrap();
        let changes = Arc::new(Mutex::new(vec![]));
        let c2 = changes.clone();
        *cm.on_change.lock().unwrap() = Some(Arc::new(move |i: &CacheItem| {
            c2.lock().unwrap().push(i.clone())
        }));
        let src = ModelSource {
            kind: ModelSourceKind::LocalPath,
            repo_id: ext.path().display().to_string(),
            revision: None,
            endpoint: None,
            token: None,
        };
        let cancel = CancellationToken::new();
        let load = |f: ModelFileRef| {
            let cm = &cm;
            let src = src.clone();
            let cancel = cancel.clone();
            async move {
                cm.ensure_files(
                    Uuid::new_v4(),
                    std::slice::from_ref(&f),
                    "",
                    Some(&src),
                    false,
                    false,
                    None,
                    None,
                    &cancel,
                    &|_, _| {},
                )
                .await
            }
        };
        // Background (default): the load returns before hashing; the item turns verified.
        let good = file("w.safetensors", 9, Some(sha.clone()));
        load(good.clone()).await.unwrap();
        for _ in 0..100 {
            if cm.items().iter().any(|i| i.sha256_verified) {
                break;
            }
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
        assert!(cm.items().iter().any(|i| i.sha256_verified));
        let p = ext.path().join("w.safetensors");
        assert_eq!(
            cm.known_hash(&p, &std::fs::metadata(&p).unwrap()),
            Some(sha.clone())
        );
        // A mismatch found in the background is reported on the item (cache_changed).
        let bad = file("x.safetensors", 9, Some("0".repeat(64)));
        load(bad.clone()).await.unwrap();
        for _ in 0..100 {
            if changes
                .lock()
                .unwrap()
                .iter()
                .any(|i| i.error_message.is_some())
            {
                break;
            }
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
        let errs: Vec<CacheItem> = changes
            .lock()
            .unwrap()
            .iter()
            .filter(|i| i.error_message.is_some())
            .cloned()
            .collect();
        assert!(errs[0]
            .error_message
            .as_ref()
            .unwrap()
            .contains("sha256 mismatch"));
        // Known now: any later load of x with the wrong sha fails at once, without hashing.
        let e = load(bad).await.unwrap_err();
        assert_eq!(e.code, error_code::MODEL_CHECKSUM_MISMATCH);
        // Size differs from the registered size: verified before the load (blocking).
        std::fs::write(ext.path().join("y.safetensors"), b"short").unwrap();
        let e = load(file("y.safetensors", 9, Some(sha))).await.unwrap_err();
        assert_eq!(e.code, error_code::MODEL_CHECKSUM_MISMATCH);
    }

    #[tokio::test]
    async fn resumable_download_and_lru() {
        use tokio::io::{AsyncReadExt as _, AsyncWriteExt as _};
        // Minimal HTTP server honouring Range.
        let body: Vec<u8> = (0..100_000u32).map(|i| (i % 251) as u8).collect();
        let sha = hex::encode(Sha256::digest(&body));
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let served = body.clone();
        tokio::spawn(async move {
            loop {
                let Ok((mut s, _)) = listener.accept().await else {
                    break;
                };
                let data = served.clone();
                tokio::spawn(async move {
                    let mut buf = vec![0u8; 4096];
                    let n = s.read(&mut buf).await.unwrap_or(0);
                    let req = String::from_utf8_lossy(&buf[..n]).to_ascii_lowercase();
                    assert!(req.contains("authorization: bearer tok"));
                    let start = req
                        .lines()
                        .find_map(|l| l.strip_prefix("range: bytes="))
                        .and_then(|r| r.trim_end_matches('-').parse::<usize>().ok());
                    let (status, slice) = match start {
                        Some(st) => ("206 Partial Content", &data[st..]),
                        None => ("200 OK", &data[..]),
                    };
                    let head = format!(
                        "HTTP/1.1 {status}\r\ncontent-length: {}\r\nconnection: close\r\n\r\n",
                        slice.len()
                    );
                    let _ = s.write_all(head.as_bytes()).await;
                    let _ = s.write_all(slice).await;
                });
            }
        });
        let dir = tempfile::tempdir().unwrap();
        let cm = CacheManager::open(dir.path().to_path_buf(), Some(1 << 30), 1).unwrap();
        let mvid = Uuid::new_v4();
        let f = file("org/m.gguf", body.len() as u64, Some(sha));
        // Pre-seed a partial file: the download must resume from it.
        let dest = dir.path().join(mvid.to_string()).join("org/m.gguf");
        std::fs::create_dir_all(dest.parent().unwrap()).unwrap();
        std::fs::write(partial_path(&dest), &body[..40_000]).unwrap();
        let src = ModelSource {
            kind: ModelSourceKind::Huggingface,
            repo_id: "org/m".into(),
            revision: None,
            endpoint: Some(format!("http://{addr}")),
            token: Some("tok".into()),
        };
        let cancel = CancellationToken::new();
        let a = PrefetchModelArgs {
            model_version_id: mvid,
            files: vec![f.clone()],
            cache_dir: String::new(),
            source: Some(src),
            pin: false,
            pin_reason: None,
            allow_pickle: false,
            max_bandwidth_bytes_per_s: None,
        };
        let res = cm.prefetch(&a, &cancel, &|_, _| {}).await.unwrap();
        assert_eq!(res["bytes_downloaded"], 60_000);
        assert_eq!(std::fs::read(&dest).unwrap(), body);
        assert!(marker_path(&dest).exists());
        assert_eq!(cm.used_bytes(), body.len() as u64);
        // Second prefetch is a cache hit.
        let res = cm.prefetch(&a, &cancel, &|_, _| {}).await.unwrap();
        assert_eq!(res["bytes_downloaded"], 0);
        // LRU sweep removes it (unpinned, not in use).
        let (ids, freed) = cm.sweep_lru(1, &HashSet::new());
        assert_eq!(ids, vec![f.model_file_id]);
        assert_eq!(freed, body.len() as u64);
        assert!(!dest.exists());
        // Index persisted.
        let again = CacheManager::open(dir.path().to_path_buf(), None, 1).unwrap();
        assert!(again.items().is_empty());
    }
}
