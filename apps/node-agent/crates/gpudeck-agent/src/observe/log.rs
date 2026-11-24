//! The `log_file` source of an observed server: a read-only follower ([`LogTail`]), parsers for
//! vLLM / SGLang periodic stats lines and startup lines, an access-log parser (uvicorn, gunicorn,
//! nginx combined) and the redaction applied before lines are shipped to the hub.

use std::collections::{BTreeMap, HashMap, VecDeque};
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use gpudeck_protocol::{EngineSample, InstanceHealth, JsonObject, ObservedEngineKind};
use serde_json::json;

/// Bytes read from the log per cycle; a backlog beyond `MAX_BACKLOG` is skipped.
const READ_CHUNK: u64 = 1024 * 1024;
const MAX_BACKLOG: u64 = 8 * 1024 * 1024;
/// A single line longer than this is dropped (bounded buffer).
const MAX_LINE: usize = 64 * 1024;
/// Startup lines (model, version, state) are looked for in this much of the head and the tail
/// of the existing file when the agent starts following it.
const HEAD_SCAN: u64 = 1024 * 1024;
const TAIL_SCAN: u64 = 256 * 1024;
/// No stats line for this long = idle (vLLM stops logging when idle).
const IDLE_AFTER: Duration = Duration::from_secs(30);
/// Distinct `METHOD /path` keys tracked; more go to `OTHER`.
const MAX_ENDPOINTS: usize = 200;
const TOP_ENDPOINTS: usize = 10;

fn truncate(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

// ------------------------------------------------------------------------------------ tail

/// Read-only follower of one log file: never locks, writes or truncates it.
#[derive(Debug)]
pub struct LogTail {
    pub path: PathBuf,
    offset: u64,
    ident: Option<(u64, u64)>,
    partial: Vec<u8>,
    started: bool,
}

/// Lines read by one [`LogTail::poll`]: `bootstrap` (first poll only: lines of the existing
/// file's head and tail, used for model / version / state) and new `lines`.
#[derive(Debug, Default)]
pub struct LogRead {
    pub bootstrap: Vec<String>,
    pub lines: Vec<String>,
    /// Bytes skipped because the follower fell more than 8 MiB behind.
    pub skipped_bytes: u64,
}

#[cfg(unix)]
fn file_ident(m: &std::fs::Metadata) -> Option<(u64, u64)> {
    use std::os::unix::fs::MetadataExt;
    Some((m.dev(), m.ino()))
}

#[cfg(not(unix))]
fn file_ident(_m: &std::fs::Metadata) -> Option<(u64, u64)> {
    None
}

fn read_range(path: &Path, from: u64, len: u64) -> std::io::Result<Vec<u8>> {
    let mut f = std::fs::File::open(path)?;
    f.seek(SeekFrom::Start(from))?;
    let mut buf = Vec::with_capacity(len.min(READ_CHUNK * 2) as usize);
    f.take(len).read_to_end(&mut buf)?;
    Ok(buf)
}

fn lines_of(buf: &[u8]) -> Vec<String> {
    buf.split(|b| *b == b'\n')
        .filter(|l| !l.is_empty() && l.len() <= MAX_LINE)
        .map(|l| {
            String::from_utf8_lossy(l)
                .trim_end_matches('\r')
                .to_string()
        })
        .collect()
}

impl LogTail {
    pub fn new(path: PathBuf) -> Self {
        Self {
            path,
            offset: 0,
            ident: None,
            partial: vec![],
            started: false,
        }
    }

    /// New complete lines since the last call. The first successful call starts at the end of
    /// the file (plus the bootstrap scan); a new inode (rename rotation) or a shorter file
    /// (copytruncate) restarts from the beginning of the current file.
    pub fn poll(&mut self) -> std::io::Result<LogRead> {
        let meta = std::fs::metadata(&self.path)?;
        let len = meta.len();
        let ident = file_ident(&meta);
        let mut out = LogRead::default();
        if !self.started {
            self.started = true;
            self.ident = ident;
            self.offset = len;
            let head = read_range(&self.path, 0, len.min(HEAD_SCAN))?;
            out.bootstrap = lines_of(&head);
            if len > HEAD_SCAN {
                let from = len.saturating_sub(TAIL_SCAN).max(HEAD_SCAN);
                let mut t = lines_of(&read_range(&self.path, from, len - from)?);
                if !t.is_empty() {
                    t.remove(0); // probably a partial line
                }
                out.bootstrap.extend(t);
            }
            return Ok(out);
        }
        if ident != self.ident {
            self.ident = ident;
            self.offset = 0;
            self.partial.clear();
        } else if len < self.offset {
            self.offset = 0;
            self.partial.clear();
        }
        if len == self.offset {
            return Ok(out);
        }
        if len - self.offset > MAX_BACKLOG {
            let to = len - READ_CHUNK;
            out.skipped_bytes = to - self.offset;
            self.offset = to;
            self.partial.clear();
        }
        let want = (len - self.offset).min(READ_CHUNK);
        let buf = read_range(&self.path, self.offset, want)?;
        self.offset += buf.len() as u64;
        let mut data = std::mem::take(&mut self.partial);
        data.extend_from_slice(&buf);
        match data.iter().rposition(|b| *b == b'\n') {
            Some(i) => {
                out.lines = lines_of(&data[..=i]);
                self.partial = data[i + 1..].to_vec();
            }
            None => self.partial = data,
        }
        if self.partial.len() > MAX_LINE {
            self.partial.clear();
        }
        Ok(out)
    }
}

// ----------------------------------------------------------------------------- redaction

const SECRET_PREFIXES: &[(&str, usize)] = &[
    ("sk-", 16),
    ("hf_", 16),
    ("gd-", 40),
    ("gdj_", 32),
    ("ghp_", 20),
    ("gho_", 20),
    ("github_pat_", 20),
    ("xoxb-", 10),
    ("xoxp-", 10),
    ("AKIA", 16),
    ("eyJ", 20),
];

const SECRET_KEYS: &[&str] = &[
    "authorization",
    "api_key",
    "api-key",
    "apikey",
    "access_token",
    "refresh_token",
    "token",
    "secret",
    "password",
    "passwd",
    "bearer",
];

fn token_char(c: char) -> bool {
    c.is_ascii_alphanumeric() || matches!(c, '_' | '-' | '.' | '+' | '/' | '=' | '~')
}

/// Mask obvious credentials in a log line before it leaves the node: well-known token prefixes
/// (`sk-`, `hf_`, `gd-`, `ghp_`, AWS `AKIA`, JWTs...), `Bearer <token>`, and the value of
/// `key=value` / `key: value` / `"key": "value"` pairs whose key names a secret. Numbers are
/// left alone (`#token: 4096`).
pub fn redact(line: &str) -> String {
    redact_prefixes(redact_keys(line))
}

fn redact_keys(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let lower = s.to_ascii_lowercase();
    let mut i = 0;
    'outer: while i < s.len() {
        for key in SECRET_KEYS {
            if !lower[i..].starts_with(key) {
                continue;
            }
            let before_ok = lower[..i]
                .chars()
                .next_back()
                .is_none_or(|c| !c.is_ascii_alphanumeric());
            if !before_ok {
                continue;
            }
            let mut j = i + key.len();
            let rest = &s[j..];
            // optional closing quote of a JSON / repr key, then the separator
            let mut k = 0;
            let rb = rest.as_bytes();
            if k < rb.len() && (rb[k] == b'"' || rb[k] == b'\'') {
                k += 1;
            }
            let sep = if *key == "bearer" {
                k < rb.len() && rb[k] == b' '
            } else {
                k < rb.len() && (rb[k] == b'=' || rb[k] == b':')
            };
            if !sep {
                continue;
            }
            k += 1;
            while k < rb.len() && (rb[k] == b' ' || rb[k] == b'"' || rb[k] == b'\'') {
                k += 1;
            }
            // `Authorization: Bearer xyz`: the scheme stays, the credential goes.
            let scheme_skip = ["bearer ", "basic ", "token "]
                .iter()
                .find(|p| rest[k..].to_ascii_lowercase().starts_with(**p))
                .map(|p| p.len())
                .unwrap_or(0);
            k += scheme_skip;
            let vstart = k;
            while k < rb.len()
                && !matches!(
                    rb[k],
                    b' ' | b'"' | b'\'' | b',' | b'&' | b';' | b')' | b'}' | b']'
                )
            {
                k += 1;
            }
            let value = &rest[vstart..k];
            let numeric = value.chars().all(|c| c.is_ascii_digit() || c == '.');
            if value.len() >= 4 && !numeric && value != "[REDACTED]" {
                j += vstart;
                out.push_str(&s[i..j]);
                out.push_str("[REDACTED]");
                i = j + value.len();
                continue 'outer;
            }
        }
        let ch = s[i..].chars().next().unwrap_or(' ');
        out.push(ch);
        i += ch.len_utf8();
    }
    out
}

fn redact_prefixes(line: String) -> String {
    let mut s = line;
    for (prefix, min) in SECRET_PREFIXES {
        let mut from = 0;
        while let Some(rel) = s[from..].find(prefix) {
            let at = from + rel;
            let boundary = s[..at]
                .chars()
                .next_back()
                .is_none_or(|c| !c.is_ascii_alphanumeric());
            let body_start = at + prefix.len();
            let body_len = s[body_start..]
                .char_indices()
                .find(|(_, c)| !token_char(*c))
                .map(|(i, _)| i)
                .unwrap_or(s.len() - body_start);
            if boundary && body_len >= *min {
                s.replace_range(body_start..body_start + body_len, "[REDACTED]");
                from = body_start + "[REDACTED]".len();
            } else {
                from = body_start;
            }
        }
    }
    s
}

/// tqdm / progress-bar output (`45%|████▌ | 9/20 [00:03<00:04, 2.51it/s]`), skipped for stats and
/// not shipped.
pub fn is_progress_line(line: &str) -> bool {
    line.contains("it/s]")
        || line.contains("s/it]")
        || (line.contains("%|") && line.contains('|'))
        || line.contains('█')
}

// ---------------------------------------------------------------------------- access logs

const METHODS: &[&str] = &["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"];
/// Paths the agent itself (or a typical health checker) polls: not counted, not shipped.
const PROBE_PATHS: &[&str] = &["/metrics", "/health", "/v1/models", "/ping", "/favicon.ico"];

/// One access-log request.
#[derive(Debug, Clone, PartialEq)]
pub struct AccessLine {
    pub method: String,
    /// Normalised: no query string, ids replaced by `:id`.
    pub path: String,
    pub status: u16,
    pub latency_ms: Option<f64>,
}

impl AccessLine {
    pub fn is_probe(&self) -> bool {
        matches!(self.method.as_str(), "GET" | "HEAD") && PROBE_PATHS.contains(&self.path.as_str())
    }
}

/// Replace id-like path segments and drop the query string (it can carry secrets).
pub fn normalize_path(path: &str) -> String {
    let p = path.split(['?', '#']).next().unwrap_or("");
    let segs: Vec<String> = p
        .split('/')
        .map(|seg| {
            let hexish = seg.len() >= 16 && seg.chars().all(|c| c.is_ascii_hexdigit() || c == '-');
            if (!seg.is_empty() && seg.chars().all(|c| c.is_ascii_digit())) || hexish {
                ":id".to_string()
            } else {
                seg.to_string()
            }
        })
        .collect();
    let out = segs.join("/");
    truncate(if out.is_empty() { "/" } else { &out }, 128)
}

fn seconds_after(rest: &str, key: &str) -> Option<f64> {
    let i = rest.find(key)? + key.len();
    let v: String = rest[i..]
        .trim_start_matches(['"', '\''])
        .chars()
        .take_while(|c| c.is_ascii_digit() || *c == '.')
        .collect();
    v.parse().ok()
}

/// Parse uvicorn (`INFO: 127.0.0.1:5 - "POST /v1/embed HTTP/1.1" 200 OK`), gunicorn and nginx
/// combined (`1.2.3.4 - - [date] "POST /x HTTP/1.1" 200 512 "-" "ua"`) access lines. Latency is
/// read only when the line carries it (`rt=`, `request_time=`, `upstream_response_time=`, in
/// seconds).
pub fn parse_access(line: &str) -> Option<AccessLine> {
    for m in METHODS {
        let Some(i) = line.find(&format!("\"{m} ")) else {
            continue;
        };
        let start = i + 1;
        let end = start + line[start..].find('"')?;
        let mut parts = line[start..end].split(' ');
        let method = parts.next()?;
        let target = parts.next()?;
        if !parts.next().is_some_and(|p| p.starts_with("HTTP/")) || !target.starts_with('/') {
            return None;
        }
        let rest = &line[end + 1..];
        let status: u16 = rest.split_whitespace().next()?.parse().ok()?;
        if !(100..=599).contains(&status) {
            return None;
        }
        let latency_ms = seconds_after(rest, "request_time=")
            .or_else(|| seconds_after(rest, "upstream_response_time="))
            .or_else(|| seconds_after(rest, "rt="))
            .map(|s| s * 1000.0);
        return Some(AccessLine {
            method: method.to_string(),
            path: normalize_path(target),
            status,
            latency_ms,
        });
    }
    None
}

/// Request counters from access lines (cumulative since the agent started following the log).
#[derive(Debug, Default)]
pub struct AccessStats {
    pub total: u64,
    pub success: u64,
    pub server_errors: u64,
    classes: BTreeMap<String, u64>,
    endpoints: HashMap<String, u64>,
    latency: VecDeque<(Instant, f64)>,
    history: VecDeque<(Instant, u64)>,
}

impl AccessStats {
    pub fn record(&mut self, a: &AccessLine, now: Instant) {
        self.total += 1;
        if a.status < 400 {
            self.success += 1;
        }
        if a.status >= 500 {
            self.server_errors += 1;
        }
        *self
            .classes
            .entry(format!("{}xx", a.status / 100))
            .or_insert(0) += 1;
        let key = format!("{} {}", a.method, a.path);
        let key = if self.endpoints.contains_key(&key) || self.endpoints.len() < MAX_ENDPOINTS {
            key
        } else {
            "OTHER".to_string()
        };
        *self.endpoints.entry(key).or_insert(0) += 1;
        if let Some(ms) = a.latency_ms {
            self.latency.push_back((now, ms));
        }
    }

    fn tick(&mut self, now: Instant) {
        while self
            .latency
            .front()
            .is_some_and(|(t, _)| now.duration_since(*t) > Duration::from_secs(60))
        {
            self.latency.pop_front();
        }
        self.history.push_back((now, self.total));
        while self
            .history
            .front()
            .is_some_and(|(t, _)| now.duration_since(*t) > Duration::from_secs(60))
        {
            self.history.pop_front();
        }
    }

    /// Requests per minute over the last minute (scaled when less history is available).
    pub fn per_minute(&self, now: Instant) -> Option<f64> {
        let (t0, c0) = *self.history.front()?;
        let dt = now.duration_since(t0).as_secs_f64();
        if dt < 5.0 {
            return None;
        }
        Some((self.total.saturating_sub(c0)) as f64 * 60.0 / dt)
    }

    fn top(&self) -> Vec<serde_json::Value> {
        let mut v: Vec<(&String, &u64)> = self.endpoints.iter().collect();
        v.sort_by(|a, b| b.1.cmp(a.1).then(a.0.cmp(b.0)));
        v.into_iter()
            .take(TOP_ENDPOINTS)
            .map(|(k, n)| {
                let (m, p) = k.split_once(' ').unwrap_or((k.as_str(), ""));
                json!({"method": m, "path": p, "count": n})
            })
            .collect()
    }

    fn latency_stats(&self) -> (Option<f64>, Option<f64>, Option<f64>) {
        if self.latency.is_empty() {
            return (None, None, None);
        }
        let mut v: Vec<f64> = self.latency.iter().map(|(_, ms)| *ms).collect();
        v.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
        let q = |p: f64| v[((v.len() - 1) as f64 * p).round() as usize];
        (
            Some(v.iter().sum::<f64>() / v.len() as f64),
            Some(q(0.5)),
            Some(q(0.95)),
        )
    }
}

// ------------------------------------------------------------------------ engine stat lines

/// One periodic stats line (per data-parallel engine).
#[derive(Debug, Clone, Default, PartialEq)]
pub struct StatLine {
    pub prompt_tps: Option<f64>,
    pub generation_tps: Option<f64>,
    pub running: Option<u64>,
    pub waiting: Option<u64>,
    /// 0-100.
    pub kv_cache_usage_pct: Option<f64>,
    /// 0-1.
    pub prefix_cache_hit_rate: Option<f64>,
    pub kv_cache_used_tokens: Option<u64>,
}

fn num(s: &str) -> Option<f64> {
    let t: String = s
        .trim()
        .chars()
        .take_while(|c| c.is_ascii_digit() || matches!(c, '.' | '-' | 'e' | 'E' | '+'))
        .collect();
    t.parse().ok()
}

/// Value after `key` up to the next `,` / `)` / end (`key` includes its separator).
fn after<'a>(line: &'a str, key: &str) -> Option<&'a str> {
    let i = line.find(key)? + key.len();
    let rest = &line[i..];
    let end = rest.find([',', ')']).unwrap_or(rest.len());
    Some(rest[..end].trim())
}

fn unquote(s: &str) -> &str {
    s.trim()
        .trim_start_matches('[')
        .trim_end_matches(']')
        .trim()
        .trim_matches(|c| c == '\'' || c == '"')
}

/// Parse a vLLM periodic stats line (V0 `metrics.py` and V1 `loggers.py`, 0.5 to current):
/// `[Engine 000: ]Avg prompt throughput: X tokens/s, Avg generation throughput: Y tokens/s,
/// Running: N reqs, (Waiting|Pending): M reqs, [Swapped: S reqs, ]GPU KV cache usage: Z%,
/// [CPU KV cache usage: C%, ][Prefix cache hit rate: W%]`. Returns `(engine key, stats)`.
pub fn parse_vllm_stats(line: &str) -> Option<(String, StatLine)> {
    let start = line.find("Avg prompt throughput:")?;
    let engine = line[..start]
        .rfind("Engine ")
        .map(|i| {
            line[i + 7..start]
                .trim()
                .trim_end_matches(':')
                .trim()
                .to_string()
        })
        .unwrap_or_else(|| "0".into());
    let mut s = StatLine::default();
    for part in line[start..].split(", ") {
        let Some((k, v)) = part.split_once(':') else {
            continue;
        };
        let k = k.trim().to_ascii_lowercase();
        let v = v.trim().trim_end_matches('.');
        match k.as_str() {
            "avg prompt throughput" => s.prompt_tps = num(v),
            "avg generation throughput" => s.generation_tps = num(v),
            "running" => s.running = num(v).map(|x| x as u64),
            "waiting" | "pending" => s.waiting = num(v).map(|x| x as u64),
            "gpu kv cache usage" | "kv cache usage" => s.kv_cache_usage_pct = num(v),
            "prefix cache hit rate" => {
                let v = v.strip_prefix("GPU:").unwrap_or(v);
                s.prefix_cache_hit_rate = num(v).map(|x| x / 100.0);
            }
            _ => {}
        }
    }
    (s.prompt_tps.is_some() || s.running.is_some()).then_some((engine, s))
}

/// Parse an SGLang scheduler line: `Decode batch[.,] #running-req: N, #token: T, token usage:
/// U, ..., gen throughput (token/s): G, #queue-req: Q` or `Prefill batch[.,] ...`.
pub fn parse_sglang_stats(line: &str) -> Option<(String, StatLine)> {
    let decode = line.contains("Decode batch");
    if !decode && !line.contains("Prefill batch") {
        return None;
    }
    let mut s = StatLine {
        running: after(line, "#running-req:").and_then(num).map(|x| x as u64),
        waiting: after(line, "#queue-req:").and_then(num).map(|x| x as u64),
        kv_cache_usage_pct: after(line, "token usage:").and_then(num).map(|x| x * 100.0),
        ..Default::default()
    };
    if decode {
        s.generation_tps = after(line, "gen throughput (token/s):").and_then(num);
        s.kv_cache_used_tokens = after(line, "#token:").and_then(num).map(|x| x as u64);
    }
    let dp = line
        .find(" DP")
        .map(|i| {
            line[i + 3..]
                .chars()
                .take_while(|c| c.is_ascii_digit())
                .collect::<String>()
        })
        .filter(|d| !d.is_empty())
        .unwrap_or_else(|| "0".into());
    (s.running.is_some() || s.waiting.is_some()).then_some((dp, s))
}

/// Model name from a startup line (`served_model_name=X`, `'served_model_name': ['X']`,
/// `model='X'`, `'model': 'X'`, SGLang `model_path='X'`).
pub fn parse_model(line: &str) -> Option<String> {
    let pick = |key: &str| {
        after(line, key)
            .map(unquote)
            .filter(|v| !v.is_empty() && *v != "None")
            .map(|v| truncate(v, 512))
    };
    pick("served_model_name=")
        .or_else(|| pick("'served_model_name': "))
        .or_else(|| pick(" model='"))
        .or_else(|| pick("'model': "))
        .or_else(|| pick("model_path='"))
}

/// What the log says about the server.
#[derive(Debug, Default)]
pub struct LogStats {
    engines: BTreeMap<String, (StatLine, Instant)>,
    pub model: Option<String>,
    pub version: Option<String>,
    ready: bool,
    stopped: bool,
    error: Option<String>,
    spec_accept_rate: Option<f64>,
    prompt_tokens_est: f64,
    generated_tokens_est: f64,
    pub access: AccessStats,
    seen_stats: bool,
}

impl LogStats {
    /// Feed one line; `bootstrap` lines (the existing file at start) only update model, version
    /// and server state, never the counters.
    pub fn ingest(&mut self, kind: ObservedEngineKind, raw: &str, now: Instant, bootstrap: bool) {
        let line = raw.rsplit('\r').next().unwrap_or(raw);
        if is_progress_line(line) {
            return;
        }
        let low = line.to_ascii_lowercase();
        if let Some(i) = line.find("vLLM API server version ") {
            self.version = line[i + 24..]
                .split_whitespace()
                .next()
                .map(|v| truncate(v, 100));
        } else if let Some(i) = line.find("LLM engine (v") {
            if self.version.is_none() {
                self.version = line[i + 13..].split(')').next().map(|v| truncate(v, 100));
            }
        }
        if line.contains("served_model_name")
            || line.contains("with config: model=")
            || line.contains("non-default args")
            || line.contains("server_args=ServerArgs(")
        {
            if let Some(m) = parse_model(line) {
                self.model = Some(m);
            }
        }
        if line.contains("Application startup complete")
            || line.contains("Uvicorn running on")
            || line.contains("Listening at: ")
            || line.contains("The server is fired up and ready to roll")
        {
            self.ready = true;
            self.stopped = false;
            self.error = None;
        }
        if line.contains("Application shutdown complete")
            || line.contains("Shutting down")
            || line.contains("Finished server process")
        {
            self.stopped = true;
            self.ready = false;
        }
        if low.contains("enginedeaderror")
            || low.contains("died unexpectedly")
            || low.contains("engine core initialization failed")
            || (line.contains("ERROR") && low.contains("cuda out of memory"))
        {
            self.error = Some(truncate(line.trim(), 300));
        }
        if bootstrap {
            return;
        }
        if let Some(a) = parse_access(line) {
            if !a.is_probe() {
                self.access.record(&a, now);
            }
            return;
        }
        if let Some(v) = after(line, "Avg Draft acceptance rate:")
            .or_else(|| after(line, "Draft acceptance rate:"))
            .and_then(num)
        {
            self.spec_accept_rate = Some(if v > 1.0 { v / 100.0 } else { v });
        }
        let parsed = match kind {
            ObservedEngineKind::Sglang => parse_sglang_stats(line),
            ObservedEngineKind::Vllm => parse_vllm_stats(line).or_else(|| {
                // V0 prints the prefix cache hit rate on its own line.
                after(line, "Prefix cache hit rate: GPU:")
                    .and_then(num)
                    .map(|v| {
                        (
                            "0".to_string(),
                            StatLine {
                                prefix_cache_hit_rate: Some(v / 100.0),
                                ..Default::default()
                            },
                        )
                    })
            }),
            _ => None,
        };
        if let Some((engine, st)) = parsed {
            self.seen_stats = true;
            self.ready = true;
            self.stopped = false;
            let prev = self.engines.get(&engine).cloned();
            let hit_rate_only = st.running.is_none() && st.prompt_tps.is_none();
            if !hit_rate_only {
                // Token totals are estimated from the logged throughput over the log interval.
                let dt = prev
                    .as_ref()
                    .map(|(_, at)| now.duration_since(*at).as_secs_f64().clamp(0.0, 30.0))
                    .unwrap_or(10.0);
                self.prompt_tokens_est += st.prompt_tps.unwrap_or(0.0) * dt;
                self.generated_tokens_est += st.generation_tps.unwrap_or(0.0) * dt;
            }
            let merged = match prev {
                Some((mut p, _)) if hit_rate_only => {
                    p.prefix_cache_hit_rate = st.prefix_cache_hit_rate;
                    p
                }
                Some((p, _)) => StatLine {
                    prefix_cache_hit_rate: st.prefix_cache_hit_rate.or(p.prefix_cache_hit_rate),
                    // SGLang prefill lines carry no throughput: keep the last decode figure.
                    generation_tps: st.generation_tps.or(if kind == ObservedEngineKind::Sglang {
                        p.generation_tps
                    } else {
                        None
                    }),
                    kv_cache_used_tokens: st.kv_cache_used_tokens.or(p.kv_cache_used_tokens),
                    ..st
                },
                None => st,
            };
            self.engines.insert(engine, (merged, now));
        }
    }

    /// Advance windows (call once per cycle).
    pub fn tick(&mut self, now: Instant) {
        self.access.tick(now);
    }

    /// Health from the log alone.
    pub fn health(&self) -> (InstanceHealth, Option<String>) {
        if let Some(e) = &self.error {
            return (
                InstanceHealth::Unhealthy,
                Some(format!("log: {}", redact(e))),
            );
        }
        if self.stopped {
            return (
                InstanceHealth::Unhealthy,
                Some("log: server shut down".into()),
            );
        }
        if self.ready || self.seen_stats || self.access.total > 0 {
            return (InstanceHealth::Healthy, None);
        }
        (
            InstanceHealth::Unknown,
            Some("log: no startup line seen yet".into()),
        )
    }

    /// Fill what `s` does not have from the log. Returns true when anything came from the log.
    pub fn fill(&self, s: &mut EngineSample, now: Instant) -> bool {
        let mut used = false;
        fn set<T>(slot: &mut Option<T>, v: Option<T>, used: &mut bool) {
            if slot.is_none() && v.is_some() {
                *slot = v;
                *used = true;
            }
        }
        let a = &self.access;
        if a.total > 0 {
            set(&mut s.requests_total, Some(a.total), &mut used);
            set(&mut s.requests_success_total, Some(a.success), &mut used);
            set(&mut s.errors_total, Some(a.server_errors), &mut used);
            let (avg, p50, p95) = a.latency_stats();
            set(&mut s.e2e_ms_avg, avg, &mut used);
            set(&mut s.e2e_ms_p50, p50, &mut used);
            set(&mut s.e2e_ms_p95, p95, &mut used);
            let extra = s.extra.get_or_insert_with(JsonObject::new);
            if let Some(rpm) = a.per_minute(now) {
                extra.insert(
                    "requests_per_minute".into(),
                    json!((rpm * 100.0).round() / 100.0),
                );
            }
            extra.insert("status_counts".into(), json!(a.classes));
            extra.insert("top_endpoints".into(), json!(a.top()));
        }
        if self.seen_stats {
            let fresh: Vec<&StatLine> = self
                .engines
                .values()
                .filter(|(_, at)| now.duration_since(*at) < IDLE_AFTER)
                .map(|(st, _)| st)
                .collect();
            let idle = fresh.is_empty();
            let sum_u = |f: fn(&StatLine) -> Option<u64>| -> Option<u64> {
                if idle {
                    return Some(0);
                }
                let v: Vec<u64> = fresh.iter().filter_map(|x| f(x)).collect();
                (!v.is_empty()).then(|| v.iter().sum())
            };
            let sum_f = |f: fn(&StatLine) -> Option<f64>| -> Option<f64> {
                if idle {
                    return Some(0.0);
                }
                let v: Vec<f64> = fresh.iter().filter_map(|x| f(x)).collect();
                (!v.is_empty()).then(|| v.iter().sum())
            };
            let all: Vec<&StatLine> = self.engines.values().map(|(st, _)| st).collect();
            let mean_f = |f: fn(&StatLine) -> Option<f64>| -> Option<f64> {
                let v: Vec<f64> = all.iter().filter_map(|x| f(x)).collect();
                (!v.is_empty()).then(|| v.iter().sum::<f64>() / v.len() as f64)
            };
            set(&mut s.active_sessions, sum_u(|x| x.running), &mut used);
            set(&mut s.waiting_sessions, sum_u(|x| x.waiting), &mut used);
            set(
                &mut s.prompt_tokens_total,
                Some(self.prompt_tokens_est.round() as u64),
                &mut used,
            );
            set(
                &mut s.generated_tokens_total,
                Some(self.generated_tokens_est.round() as u64),
                &mut used,
            );
            if !idle {
                let v: Vec<u64> = fresh
                    .iter()
                    .filter_map(|x| x.kv_cache_used_tokens)
                    .collect();
                set(
                    &mut s.kv_cache_used_tokens,
                    (!v.is_empty()).then(|| v.iter().sum()),
                    &mut used,
                );
            }
            set(&mut s.prompt_tps, sum_f(|x| x.prompt_tps), &mut used);
            set(
                &mut s.generation_tps,
                sum_f(|x| x.generation_tps),
                &mut used,
            );
            set(
                &mut s.kv_cache_usage_pct,
                mean_f(|x| x.kv_cache_usage_pct),
                &mut used,
            );
            set(
                &mut s.prefix_cache_hit_rate,
                mean_f(|x| x.prefix_cache_hit_rate),
                &mut used,
            );
            set(&mut s.spec_accept_rate, self.spec_accept_rate, &mut used);
            if used {
                s.extra
                    .get_or_insert_with(JsonObject::new)
                    .insert("tokens_estimated_from_log".into(), true.into());
            }
        }
        used
    }
}

/// A line worth shipping to the hub (redacted, truncated) or `None` for progress bars, blank
/// lines and the access lines of probes (the agent's own `GET /metrics` etc.).
pub fn shippable(raw: &str, max_len: usize) -> Option<String> {
    let line = raw.rsplit('\r').next().unwrap_or(raw).trim_end();
    if line.trim().is_empty() || is_progress_line(line) {
        return None;
    }
    if parse_access(line).is_some_and(|a| a.is_probe()) {
        return None;
    }
    let mut out = redact(line);
    if out.chars().count() > max_len {
        out = format!("{}…", truncate(&out, max_len));
    }
    Some(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture(rel: &str) -> String {
        std::fs::read_to_string(
            Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("../../fixtures/engines")
                .join(rel),
        )
        .unwrap()
    }

    #[test]
    fn vllm_stat_lines_across_versions() {
        let mut seen = 0;
        for line in fixture("vllm/server_log.txt").lines() {
            if let Some((_, s)) = parse_vllm_stats(line) {
                seen += 1;
                assert!(
                    s.prompt_tps.is_some() && s.generation_tps.is_some(),
                    "{line}"
                );
                assert!(s.running.is_some() && s.waiting.is_some(), "{line}");
                assert!(s.kv_cache_usage_pct.is_some(), "{line}");
            }
        }
        assert!(seen >= 5, "every version's stats line parses ({seen})");
        let (e, s) = parse_vllm_stats(
            "(APIServer pid=1) INFO 10-01 12:00:00 [loggers.py:123] Engine 001: Avg prompt throughput: 512.3 tokens/s, Avg generation throughput: 40.1 tokens/s, Running: 2 reqs, Waiting: 1 reqs, GPU KV cache usage: 3.4%, Prefix cache hit rate: 22.1%",
        )
        .unwrap();
        assert_eq!(e, "001");
        assert_eq!((s.running, s.waiting), (Some(2), Some(1)));
        assert_eq!(s.prompt_tps, Some(512.3));
        assert_eq!(s.kv_cache_usage_pct, Some(3.4));
        assert!((s.prefix_cache_hit_rate.unwrap() - 0.221).abs() < 1e-9);
        let (_, v0) = parse_vllm_stats(
            "INFO 03-12 10:00:00 metrics.py:455] Avg prompt throughput: 0.0 tokens/s, Avg generation throughput: 12.3 tokens/s, Running: 1 reqs, Swapped: 0 reqs, Pending: 3 reqs, GPU KV cache usage: 0.4%, CPU KV cache usage: 0.0%.",
        )
        .unwrap();
        assert_eq!(v0.waiting, Some(3));
        assert_eq!(v0.generation_tps, Some(12.3));
        assert!(parse_vllm_stats("INFO: 127.0.0.1:5 - \"GET /metrics HTTP/1.1\" 200 OK").is_none());
    }

    #[test]
    fn startup_lines_give_model_and_version() {
        let mut st = LogStats::default();
        let now = Instant::now();
        for line in fixture("vllm/server_log.txt").lines() {
            st.ingest(ObservedEngineKind::Vllm, line, now, true);
        }
        assert_eq!(st.model.as_deref(), Some("Qwen/Qwen3-8B"));
        assert_eq!(st.version.as_deref(), Some("0.34.0"));
        assert_eq!(st.health().0, InstanceHealth::Healthy);
        assert_eq!(
            parse_model("INFO 03-12 llm_engine.py:237] Initializing an LLM engine (v0.6.3) with config: model='meta-llama/Llama-3.1-8B-Instruct', speculative_config=None, served_model_name=None, seed=0").as_deref(),
            Some("meta-llama/Llama-3.1-8B-Instruct")
        );
        assert_eq!(
            parse_model("[2025-01-01] server_args=ServerArgs(model_path='Qwen/Qwen2.5-7B-Instruct', tokenizer_path='Qwen/Qwen2.5-7B-Instruct', served_model_name='qwen')").as_deref(),
            Some("qwen")
        );
        st.ingest(
            ObservedEngineKind::Vllm,
            "INFO:     Shutting down",
            now,
            false,
        );
        assert_eq!(st.health().0, InstanceHealth::Unhealthy);
    }

    #[test]
    fn sglang_lines() {
        let (_, d) = parse_sglang_stats("[2025-05-01 10:00:00] Decode batch. #running-req: 3, #token: 4096, token usage: 0.12, cuda graph: True, gen throughput (token/s): 145.67, #queue-req: 1").unwrap();
        assert_eq!((d.running, d.waiting), (Some(3), Some(1)));
        assert_eq!(d.generation_tps, Some(145.67));
        assert_eq!(d.kv_cache_used_tokens, Some(4096));
        assert!((d.kv_cache_usage_pct.unwrap() - 12.0).abs() < 1e-9);
        let (dp, p) = parse_sglang_stats("[2025-05-01 10:00:01 DP1 TP0] Prefill batch, #new-seq: 1, #new-token: 12, #cached-token: 0, token usage: 0.00, #running-req: 0, #queue-req: 0").unwrap();
        assert_eq!(dp, "1");
        assert_eq!(p.running, Some(0));
        assert_eq!(p.generation_tps, None);
    }

    #[test]
    fn access_lines_from_common_servers() {
        let a =
            parse_access("INFO:     127.0.0.1:52344 - \"POST /v1/embed HTTP/1.1\" 200 OK").unwrap();
        assert_eq!(
            (a.method.as_str(), a.path.as_str(), a.status),
            ("POST", "/v1/embed", 200)
        );
        let g = parse_access("10.0.0.2 - - [23/Nov/2026:10:00:00 +0000] \"POST /edit?token=abc HTTP/1.1\" 500 12 \"-\" \"python-requests/2.31\"").unwrap();
        assert_eq!((g.path.as_str(), g.status), ("/edit", 500));
        let n = parse_access("1.2.3.4 - - [23/Nov/2026:10:00:00 +0000] \"GET /jobs/123e4567e89b12d3a456/result HTTP/2.0\" 404 0 \"-\" \"curl\" rt=0.250").unwrap();
        assert_eq!(n.path, "/jobs/:id/result");
        assert_eq!(n.latency_ms, Some(250.0));
        assert!(parse_access("INFO: \"GET /health HTTP/1.1\" 200 OK")
            .unwrap()
            .is_probe());
        assert!(parse_access(
            "Loading pipeline components...: 100%|██████████| 7/7 [00:03<00:00,  2.10it/s]"
        )
        .is_none());
        assert!(parse_access("no request here").is_none());
    }

    #[test]
    fn generic_log_stats_count_requests_not_probes_or_progress() {
        let mut st = LogStats::default();
        let t0 = Instant::now();
        st.tick(t0);
        for line in fixture("generic/uvicorn_access.log").lines() {
            st.ingest(ObservedEngineKind::Generic, line, t0, false);
        }
        st.tick(t0 + Duration::from_secs(30));
        assert_eq!(
            st.access.total, 6,
            "probes and progress lines are not requests"
        );
        let mut s = EngineSample::default();
        assert!(st.fill(&mut s, t0 + Duration::from_secs(30)));
        assert_eq!(s.requests_total, Some(6));
        assert_eq!(s.requests_success_total, Some(4));
        assert_eq!(s.errors_total, Some(1));
        let extra = s.extra.unwrap();
        assert_eq!(extra["status_counts"]["2xx"], 4);
        assert_eq!(extra["status_counts"]["4xx"], 1);
        assert_eq!(extra["top_endpoints"][0]["path"], "/v1/edit");
        assert_eq!(extra["top_endpoints"][0]["count"], 3);
        assert!(extra["requests_per_minute"].as_f64().unwrap() > 0.0);
        assert_eq!(
            s.active_sessions, None,
            "no engine stats for generic servers"
        );
        assert_eq!(st.health().0, InstanceHealth::Healthy);
    }

    #[test]
    fn vllm_log_fill_and_idle() {
        let mut st = LogStats::default();
        let t0 = Instant::now();
        st.ingest(ObservedEngineKind::Vllm, "INFO 05-20 08:00:00 [loggers.py:111] Engine 000: Avg prompt throughput: 100.0 tokens/s, Avg generation throughput: 20.0 tokens/s, Running: 2 reqs, Waiting: 1 reqs, GPU KV cache usage: 10.0%, Prefix cache hit rate: 50.0%", t0, false);
        st.ingest(
            ObservedEngineKind::Vllm,
            "INFO:     127.0.0.1:50000 - \"POST /v1/chat/completions HTTP/1.1\" 200 OK",
            t0,
            false,
        );
        st.ingest(
            ObservedEngineKind::Vllm,
            "INFO:     127.0.0.1:50001 - \"GET /metrics HTTP/1.1\" 200 OK",
            t0,
            false,
        );
        let mut s = EngineSample::default();
        assert!(st.fill(&mut s, t0));
        assert_eq!((s.active_sessions, s.waiting_sessions), (Some(2), Some(1)));
        assert_eq!(s.generation_tps, Some(20.0));
        assert_eq!(s.kv_cache_usage_pct, Some(10.0));
        assert_eq!(s.prefix_cache_hit_rate, Some(0.5));
        assert_eq!(
            s.requests_total,
            Some(1),
            "the agent's own GET /metrics is not counted"
        );
        assert_eq!(
            s.prompt_tokens_total,
            Some(1000),
            "100 tok/s x 10 s default interval"
        );
        assert_eq!(s.ttft_ms_p95, None, "latency histograms are not in the log");
        let mut s = EngineSample {
            active_sessions: Some(9),
            ..Default::default()
        };
        st.fill(&mut s, t0);
        assert_eq!(
            s.active_sessions,
            Some(9),
            "engine metrics win over the log"
        );
        let mut s = EngineSample::default();
        st.fill(&mut s, t0 + Duration::from_secs(31));
        assert_eq!(
            (s.active_sessions, s.generation_tps),
            (Some(0), Some(0.0)),
            "idle"
        );
    }

    #[test]
    fn redaction() {
        let cases = [
            (
                "Authorization: Bearer abcdef0123456789",
                "Authorization: Bearer [REDACTED]",
            ),
            (
                "headers={'authorization': 'Bearer sk-abcdefghijklmnopqrstu'}",
                "headers={'authorization': 'Bearer [REDACTED]'}",
            ),
            (
                "using HF_TOKEN=hf_abcdefghijklmnopqrstuvwxyz",
                "using HF_TOKEN=[REDACTED]",
            ),
            (
                "key sk-proj-abcdefghijklmnop1234 loaded",
                "key sk-[REDACTED] loaded",
            ),
            (
                "api_key=\"secret-value\" next",
                "api_key=\"[REDACTED]\" next",
            ),
            (
                "{\"password\": \"hunter22\"}",
                "{\"password\": \"[REDACTED]\"}",
            ),
            (
                "gd-0123456789abcdef0123456789abcdef0123456789abcdef",
                "gd-[REDACTED]",
            ),
        ];
        for (input, want) in cases {
            assert_eq!(redact(input), want, "{input}");
        }
        for keep in [
            "Decode batch. #running-req: 3, #token: 4096, token usage: 0.12",
            "max_tokens=512 total_tokens: 900",
            "Avg prompt throughput: 1.0 tokens/s",
        ] {
            assert_eq!(redact(keep), keep);
        }
    }

    #[test]
    fn shipping_filters_and_caps() {
        assert_eq!(shippable("  ", 100), None);
        assert_eq!(
            shippable(" 45%|████▌     | 9/20 [00:03<00:04,  2.51it/s]", 100),
            None
        );
        assert_eq!(
            shippable("x\r 50%|█| 1/2 [00:01<00:01, 1.00it/s]", 100),
            None
        );
        assert_eq!(
            shippable(
                "INFO:     127.0.0.1:1 - \"GET /metrics HTTP/1.1\" 200 OK",
                100
            ),
            None
        );
        assert_eq!(
            shippable("token=abcdefgh", 100).as_deref(),
            Some("token=[REDACTED]")
        );
        assert_eq!(shippable(&"a".repeat(50), 10).unwrap().chars().count(), 11);
    }

    #[test]
    fn tail_follows_rotates_and_truncates_read_only() {
        use std::io::Write;
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("main.log");
        std::fs::write(&path, "INFO vLLM API server version 0.9.1\nold line\n").unwrap();
        let mut t = LogTail::new(path.clone());
        let r = t.poll().unwrap();
        assert!(r.lines.is_empty(), "starts at EOF");
        assert_eq!(r.bootstrap.len(), 2, "startup scan of the existing file");
        let mut f = std::fs::OpenOptions::new()
            .append(true)
            .open(&path)
            .unwrap();
        write!(f, "line one\nline tw").unwrap();
        f.flush().unwrap();
        assert_eq!(t.poll().unwrap().lines, vec!["line one".to_string()]);
        writeln!(f, "o").unwrap();
        f.flush().unwrap();
        assert_eq!(t.poll().unwrap().lines, vec!["line two".to_string()]);
        std::fs::write(&path, "after truncate\n").unwrap();
        assert_eq!(t.poll().unwrap().lines, vec!["after truncate".to_string()]);
        std::fs::rename(&path, dir.path().join("main.log.1")).unwrap();
        std::fs::write(&path, "fresh file\n").unwrap();
        assert_eq!(t.poll().unwrap().lines, vec!["fresh file".to_string()]);
        let before = std::fs::read(&path).unwrap();
        let _ = t.poll();
        assert_eq!(std::fs::read(&path).unwrap(), before, "never modified");
        std::fs::remove_file(&path).unwrap();
        assert!(t.poll().is_err(), "missing file is an error, not a panic");
    }
}
