//! The agent proxy on :9443 (`protocol.md` §6): TLS 1.3 with a required client certificate
//! chained to the internal CA, subject allow-list + CRL enforced inside the handshake,
//! `/instances/{id}/{engine_path}` routing to `127.0.0.1:{port}` from `config_push.routing`,
//! draining (503 + Retry-After), engine admin paths blocked, model-name rewrite, per-instance
//! concurrency cap with a bounded queue, unbuffered streaming, and per-request accounting that
//! feeds the proxy-derived metrics.

pub mod body;
pub mod stats;

use std::collections::{HashMap, HashSet};
use std::convert::Infallible;
use std::net::SocketAddr;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, RwLock};
use std::time::{Duration, Instant};

use bytes::Bytes;
use gpudeck_protocol::{EngineName, InstanceHealth, InstanceState, ProxySettings, RoutingEntry};
use http::{header, HeaderMap, HeaderValue, Method, Request, Response, StatusCode};
use http_body_util::{BodyExt, Full, Limited};
use hyper::body::Incoming;
use hyper_util::client::legacy::Client;
use hyper_util::rt::{TokioExecutor, TokioIo};
use rustls::server::danger::{ClientCertVerified, ClientCertVerifier};
use rustls::ServerConfig;
use tokio::sync::Semaphore;
use uuid::Uuid;

use crate::agent::Agent;
use crate::backend::ProxySnapshot;
use crate::tls::ClientPolicy;
use stats::{InstanceStats, RequestRecord};

pub type BoxError = Box<dyn std::error::Error + Send + Sync>;
pub type ProxyBody = http_body_util::combinators::BoxBody<Bytes, BoxError>;

const HOP_BY_HOP: &[&str] = &[
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "proxy-connection",
];

#[derive(Debug, Clone)]
pub struct ProxyCfg {
    pub queue_timeout: Duration,
    pub request_timeout: Duration,
    pub idle_timeout: Duration,
    pub max_body: u64,
}

impl Default for ProxyCfg {
    fn default() -> Self {
        Self {
            queue_timeout: Duration::from_secs(30),
            request_timeout: Duration::from_secs(600),
            idle_timeout: Duration::from_secs(120),
            max_body: 64 * 1024 * 1024,
        }
    }
}

/// What the instance manager tells the proxy about a local instance.
#[derive(Debug, Clone)]
pub struct LocalRoute {
    pub port: u16,
    pub engine: EngineName,
    pub served_model_name: String,
    pub max_concurrency: Option<u32>,
}

pub struct ProxyState {
    pub node_id: Uuid,
    routes: RwLock<HashMap<Uuid, RoutingEntry>>,
    local: RwLock<HashMap<Uuid, LocalRoute>>,
    draining: RwLock<HashSet<Uuid>>,
    stats: Mutex<HashMap<Uuid, InstanceStats>>,
    limiters: Mutex<HashMap<Uuid, (u32, Arc<Semaphore>)>>,
    pub policy: Arc<RwLock<ClientPolicy>>,
    cfg: RwLock<ProxyCfg>,
    pub requests_total: AtomicU64,
    pub errors_total: AtomicU64,
    pub active_connections: AtomicU64,
    tls: RwLock<Option<Arc<ServerConfig>>>,
    client: Client<hyper_util::client::legacy::connect::HttpConnector, Full<Bytes>>,
}

fn error_body(code: &str, message: &str) -> Bytes {
    Bytes::from(serde_json::json!({"error": {"code": code, "message": message}}).to_string())
}

pub fn full(b: impl Into<Bytes>) -> ProxyBody {
    Full::new(b.into())
        .map_err(|never: Infallible| match never {})
        .boxed()
}

fn json_response(status: StatusCode, body: Bytes) -> Response<ProxyBody> {
    let mut r = Response::new(full(body));
    *r.status_mut() = status;
    r.headers_mut().insert(
        header::CONTENT_TYPE,
        HeaderValue::from_static("application/json"),
    );
    r
}

fn err(status: StatusCode, code: &str, message: &str) -> Response<ProxyBody> {
    json_response(status, error_body(code, message))
}

impl ProxyState {
    pub fn new(node_id: Uuid) -> Self {
        let mut http = hyper_util::client::legacy::connect::HttpConnector::new();
        http.set_nodelay(true);
        http.set_connect_timeout(Some(Duration::from_secs(5)));
        Self {
            node_id,
            routes: RwLock::new(HashMap::new()),
            local: RwLock::new(HashMap::new()),
            draining: RwLock::new(HashSet::new()),
            stats: Mutex::new(HashMap::new()),
            limiters: Mutex::new(HashMap::new()),
            policy: Arc::new(RwLock::new(ClientPolicy::default())),
            cfg: RwLock::new(ProxyCfg::default()),
            requests_total: AtomicU64::new(0),
            errors_total: AtomicU64::new(0),
            active_connections: AtomicU64::new(0),
            tls: RwLock::new(None),
            client: Client::builder(TokioExecutor::new())
                .pool_idle_timeout(Duration::from_secs(30))
                .build(http),
        }
    }

    pub fn apply_settings(&self, p: &ProxySettings) {
        let mut c = self.cfg.write().unwrap_or_else(|e| e.into_inner());
        if let Some(q) = p.queue_timeout_seconds {
            c.queue_timeout = Duration::from_secs_f64(q.max(0.0));
        }
        if let Some(t) = p.request_timeout_seconds {
            c.request_timeout = Duration::from_secs_f64(t.max(1.0));
        }
        if let Some(m) = p.max_body_bytes {
            c.max_body = m;
        }
        if let Some(subjects) = &p.allowed_client_subjects {
            self.policy
                .write()
                .unwrap_or_else(|e| e.into_inner())
                .allowed_subjects = subjects.clone();
        }
    }

    pub fn cfg(&self) -> ProxyCfg {
        self.cfg.read().unwrap_or_else(|e| e.into_inner()).clone()
    }

    pub fn set_crl(&self, serials: &[String], fingerprints: &[String]) {
        self.policy
            .write()
            .unwrap_or_else(|e| e.into_inner())
            .set_crl(serials, fingerprints);
    }

    /// Replace (full) or merge routing from `config_push.routing`.
    pub fn set_routing(&self, entries: &[RoutingEntry]) {
        let mut r = self.routes.write().unwrap_or_else(|e| e.into_inner());
        r.clear();
        for e in entries {
            r.insert(e.instance_id, e.clone());
        }
    }

    pub fn routing(&self) -> Vec<RoutingEntry> {
        self.routes
            .read()
            .unwrap_or_else(|e| e.into_inner())
            .values()
            .cloned()
            .collect()
    }

    pub fn route(&self, id: &Uuid) -> Option<RoutingEntry> {
        self.routes
            .read()
            .unwrap_or_else(|e| e.into_inner())
            .get(id)
            .cloned()
    }

    pub fn set_local(&self, id: Uuid, r: LocalRoute) {
        self.local
            .write()
            .unwrap_or_else(|e| e.into_inner())
            .insert(id, r);
    }

    pub fn remove_local(&self, id: &Uuid) {
        self.local
            .write()
            .unwrap_or_else(|e| e.into_inner())
            .remove(id);
        self.draining
            .write()
            .unwrap_or_else(|e| e.into_inner())
            .remove(id);
    }

    pub fn local(&self, id: &Uuid) -> Option<LocalRoute> {
        self.local
            .read()
            .unwrap_or_else(|e| e.into_inner())
            .get(id)
            .cloned()
    }

    pub fn set_draining(&self, id: Uuid, draining: bool) {
        let mut d = self.draining.write().unwrap_or_else(|e| e.into_inner());
        if draining {
            d.insert(id);
        } else {
            d.remove(&id);
        }
    }

    pub fn is_draining(&self, id: &Uuid) -> bool {
        self.draining
            .read()
            .unwrap_or_else(|e| e.into_inner())
            .contains(id)
    }

    fn with_stats<T>(&self, id: Uuid, f: impl FnOnce(&mut InstanceStats) -> T) -> T {
        let mut g = self.stats.lock().unwrap_or_else(|e| e.into_inner());
        f(g.entry(id).or_default())
    }

    /// (active, waiting) requests for an instance.
    pub fn counts(&self, id: &Uuid) -> (u64, u64) {
        let g = self.stats.lock().unwrap_or_else(|e| e.into_inner());
        g.get(id)
            .map(|s| (s.active(), s.waiting()))
            .unwrap_or((0, 0))
    }

    pub fn snapshot(&self, id: Uuid, max_concurrent: Option<u64>) -> ProxySnapshot {
        self.with_stats(id, |s| s.take_snapshot(max_concurrent))
    }

    pub fn forget_stats(&self, id: &Uuid) {
        self.stats
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .remove(id);
    }

    fn limiter(&self, id: Uuid, max: Option<u32>) -> Option<Arc<Semaphore>> {
        let max = max.filter(|m| *m > 0)?;
        let mut g = self.limiters.lock().unwrap_or_else(|e| e.into_inner());
        let entry = g
            .entry(id)
            .or_insert_with(|| (max, Arc::new(Semaphore::new(max as usize))));
        if entry.0 != max {
            *entry = (max, Arc::new(Semaphore::new(max as usize)));
        }
        Some(entry.1.clone())
    }

    pub fn invalidate_tls(&self) {
        *self.tls.write().unwrap_or_else(|e| e.into_inner()) = None;
    }

    fn tls_config(&self, agent: &Agent) -> anyhow::Result<Arc<ServerConfig>> {
        if let Some(c) = self.tls.read().unwrap_or_else(|e| e.into_inner()).clone() {
            return Ok(c);
        }
        let cfg = Arc::new(server_config(&agent.identity(), self.policy.clone())?);
        *self.tls.write().unwrap_or_else(|e| e.into_inner()) = Some(cfg.clone());
        Ok(cfg)
    }
}

/// Client-certificate verifier: WebPKI chain to the internal CA, then subject allow-list + CRL.
#[derive(Debug)]
struct PolicyVerifier {
    inner: Arc<dyn ClientCertVerifier>,
    policy: Arc<RwLock<ClientPolicy>>,
}

impl ClientCertVerifier for PolicyVerifier {
    fn root_hint_subjects(&self) -> &[rustls::DistinguishedName] {
        self.inner.root_hint_subjects()
    }
    fn verify_client_cert(
        &self,
        end_entity: &rustls::pki_types::CertificateDer<'_>,
        intermediates: &[rustls::pki_types::CertificateDer<'_>],
        now: rustls::pki_types::UnixTime,
    ) -> Result<ClientCertVerified, rustls::Error> {
        self.inner
            .verify_client_cert(end_entity, intermediates, now)?;
        let policy = self.policy.read().unwrap_or_else(|e| e.into_inner());
        policy.check(end_entity.as_ref()).map_err(|reason| {
            tracing::warn!(%reason, "proxy refused client certificate");
            rustls::Error::General(reason)
        })?;
        Ok(ClientCertVerified::assertion())
    }
    fn verify_tls12_signature(
        &self,
        m: &[u8],
        c: &rustls::pki_types::CertificateDer<'_>,
        d: &rustls::DigitallySignedStruct,
    ) -> Result<rustls::client::danger::HandshakeSignatureValid, rustls::Error> {
        self.inner.verify_tls12_signature(m, c, d)
    }
    fn verify_tls13_signature(
        &self,
        m: &[u8],
        c: &rustls::pki_types::CertificateDer<'_>,
        d: &rustls::DigitallySignedStruct,
    ) -> Result<rustls::client::danger::HandshakeSignatureValid, rustls::Error> {
        self.inner.verify_tls13_signature(m, c, d)
    }
    fn supported_verify_schemes(&self) -> Vec<rustls::SignatureScheme> {
        self.inner.supported_verify_schemes()
    }
    fn client_auth_mandatory(&self) -> bool {
        true
    }
}

pub fn server_config(
    id: &crate::tls::Identity,
    policy: Arc<RwLock<ClientPolicy>>,
) -> anyhow::Result<ServerConfig> {
    crate::tls::init_crypto();
    let provider = rustls::crypto::CryptoProvider::get_default()
        .cloned()
        .expect("provider");
    let roots = Arc::new(id.root_store()?);
    let inner =
        rustls::server::WebPkiClientVerifier::builder_with_provider(roots, provider.clone())
            .build()?;
    let verifier = Arc::new(PolicyVerifier { inner, policy });
    let mut cfg = ServerConfig::builder_with_provider(provider)
        .with_protocol_versions(&[&rustls::version::TLS13])?
        .with_client_cert_verifier(verifier)
        .with_single_cert(id.cert_chain.clone(), id.key.clone_key())?;
    // HTTP/2 lets the API layer multiplex many requests over one connection (one TLS handshake
    // per node, not one per concurrent request on a high-latency link); HTTP/1.1 keep-alive
    // for clients without h2.
    cfg.alpn_protocols = vec![b"h2".to_vec(), b"http/1.1".to_vec()];
    Ok(cfg)
}

/// Accept loop.
pub async fn serve(agent: Arc<Agent>, listener: tokio::net::TcpListener) {
    loop {
        let (tcp, peer) = tokio::select! {
            r = listener.accept() => match r { Ok(x) => x, Err(e) => { tracing::warn!("proxy accept: {e}"); tokio::time::sleep(Duration::from_millis(100)).await; continue; } },
            _ = agent.shutdown.cancelled() => return,
        };
        let _ = tcp.set_nodelay(true);
        let agent = agent.clone();
        tokio::spawn(async move {
            let cfg = match agent.proxy.tls_config(&agent) {
                Ok(c) => c,
                Err(e) => {
                    tracing::error!("proxy tls config: {e:#}");
                    return;
                }
            };
            let tls = match tokio::time::timeout(
                Duration::from_secs(10),
                tokio_rustls::TlsAcceptor::from(cfg).accept(tcp),
            )
            .await
            {
                Ok(Ok(s)) => s,
                Ok(Err(e)) => {
                    tracing::debug!(%peer, "proxy TLS handshake failed: {e}");
                    return;
                }
                Err(_) => return,
            };
            agent
                .proxy
                .active_connections
                .fetch_add(1, Ordering::Relaxed);
            let a2 = agent.clone();
            let svc = hyper::service::service_fn(move |req| {
                let a = a2.clone();
                async move { Ok::<_, Infallible>(handle(a, req, peer).await) }
            });
            let h2 = tls.get_ref().1.alpn_protocol() == Some(b"h2".as_slice());
            if h2 {
                let _ =
                    hyper::server::conn::http2::Builder::new(hyper_util::rt::TokioExecutor::new())
                        .timer(hyper_util::rt::TokioTimer::new())
                        .keep_alive_interval(Some(Duration::from_secs(30)))
                        .serve_connection(TokioIo::new(tls), svc)
                        .await;
            } else {
                let _ = hyper::server::conn::http1::Builder::new()
                    .keep_alive(true)
                    .serve_connection(TokioIo::new(tls), svc)
                    .await;
            }
            agent
                .proxy
                .active_connections
                .fetch_sub(1, Ordering::Relaxed);
        });
    }
}

async fn handle(
    agent: Arc<Agent>,
    req: Request<Incoming>,
    _peer: SocketAddr,
) -> Response<ProxyBody> {
    let path = req.uri().path().to_string();
    if path == "/_health" {
        return json_response(StatusCode::OK, Bytes::from(serde_json::json!({"status": "ok", "node_id": agent.node_id, "version": crate::join::AGENT_VERSION}).to_string()));
    }
    if path == "/_metrics" {
        let text = agent
            .prom_text
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .clone();
        let mut r = Response::new(full(text));
        r.headers_mut().insert(
            header::CONTENT_TYPE,
            HeaderValue::from_static("text/plain; version=0.0.4"),
        );
        return r;
    }
    let Some(rest) = path.strip_prefix("/instances/") else {
        return err(StatusCode::NOT_FOUND, "not_found", "unknown path");
    };
    let (id_str, engine_path) = match rest.find('/') {
        Some(i) => (&rest[..i], &rest[i..]),
        None => (rest, "/"),
    };
    let Ok(id) = id_str.parse::<Uuid>() else {
        return err(
            StatusCode::NOT_FOUND,
            "instance_not_found",
            "invalid instance id",
        );
    };
    if engine_path == "/_health" {
        return instance_health(&agent, id);
    }
    forward(agent, req, id, engine_path.to_string()).await
}

fn instance_health(agent: &Agent, id: Uuid) -> Response<ProxyBody> {
    let inst = agent
        .instances
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .get(&id)
        .map(|i| (i.state, i.health));
    let Some((state, health)) = inst else {
        return err(
            StatusCode::NOT_FOUND,
            "instance_not_found",
            "instance not on this node",
        );
    };
    let (a, w) = agent.proxy.counts(&id);
    let ok = state == InstanceState::Ready
        && health == InstanceHealth::Healthy
        && !agent.proxy.is_draining(&id);
    let body = serde_json::json!({"state": state, "health": health, "active_sessions": a, "waiting_sessions": w});
    json_response(
        if ok {
            StatusCode::OK
        } else {
            StatusCode::SERVICE_UNAVAILABLE
        },
        Bytes::from(body.to_string()),
    )
}

/// Is `path` forwardable for this route?
pub fn path_allowed(path: &str, allowed: &[String], blocked: &[String]) -> bool {
    let norm = path.split('?').next().unwrap_or(path);
    if norm.contains("/../") || norm.ends_with("/..") {
        return false;
    }
    let blocked_hit =
        |p: &String| norm == p || norm.starts_with(&format!("{}/", p.trim_end_matches('/')));
    if blocked.iter().any(blocked_hit) {
        return false;
    }
    allowed.iter().any(|p| {
        if p.ends_with('/') {
            norm.starts_with(p.as_str()) || norm == p.trim_end_matches('/')
        } else {
            norm == p || norm.starts_with(&format!("{p}/"))
        }
    })
}

/// Replace a top-level JSON `model` with `served`.
pub fn rewrite_model(body: &[u8], served: &str) -> Option<Bytes> {
    let first = body.iter().find(|b| !b.is_ascii_whitespace())?;
    if *first != b'{' {
        return None;
    }
    let mut v: serde_json::Value = serde_json::from_slice(body).ok()?;
    let obj = v.as_object_mut()?;
    match obj.get("model") {
        Some(serde_json::Value::String(m)) if m == served => None,
        Some(_) => {
            obj.insert(
                "model".into(),
                serde_json::Value::String(served.to_string()),
            );
            serde_json::to_vec(&v).ok().map(Bytes::from)
        }
        None => None,
    }
}

fn is_stream_request(body: &[u8]) -> bool {
    serde_json::from_slice::<serde_json::Value>(body)
        .ok()
        .and_then(|v| v.get("stream").and_then(|s| s.as_bool()))
        .unwrap_or(false)
}

fn prompt_estimate(body: &[u8]) -> Option<u64> {
    // Rough in-flight KV estimate before usage arrives: ~4 chars per token of the request text.
    (body.len() > 2).then_some((body.len() as u64) / 4)
}

struct InflightGuard {
    agent: Arc<Agent>,
    id: Uuid,
    conn: u64,
    armed: bool,
}

impl Drop for InflightGuard {
    fn drop(&mut self) {
        if self.armed {
            let conn = self.conn;
            self.agent
                .proxy
                .with_stats(self.id, |s| s.cancel_if_inflight(conn));
        }
    }
}

async fn forward(
    agent: Arc<Agent>,
    req: Request<Incoming>,
    id: Uuid,
    engine_path: String,
) -> Response<ProxyBody> {
    let proxy = &agent.proxy;
    proxy.requests_total.fetch_add(1, Ordering::Relaxed);
    let Some(route) = proxy.route(&id) else {
        return err(
            StatusCode::NOT_FOUND,
            "instance_not_found",
            "instance is not routed on this node",
        );
    };
    if !route.accepting || proxy.is_draining(&id) {
        let mut r = err(
            StatusCode::SERVICE_UNAVAILABLE,
            "instance_draining",
            "instance is draining",
        );
        r.headers_mut()
            .insert(header::RETRY_AFTER, HeaderValue::from_static("2"));
        return r;
    }
    let local = proxy.local(&id);
    let engine = local
        .as_ref()
        .map(|l| l.engine)
        .or(route.engine)
        .unwrap_or(EngineName::Custom);
    let mut blocked: Vec<String> = crate::backend::ALWAYS_BLOCKED_PATHS
        .iter()
        .map(|s| s.to_string())
        .collect();
    blocked.extend(agent.engines.driver(engine).admin_paths());
    if !path_allowed(&engine_path, &route.allowed_path_prefixes, &blocked) {
        return err(
            StatusCode::FORBIDDEN,
            "path_not_allowed",
            "path is not allowed through the proxy",
        );
    }
    let port = local.as_ref().map(|l| l.port).unwrap_or(route.port);
    let served = local
        .as_ref()
        .map(|l| l.served_model_name.clone())
        .unwrap_or_else(|| route.served_model_name.clone());
    let cfg = proxy.cfg();
    let request_id = req
        .headers()
        .get("x-request-id")
        .and_then(|v| v.to_str().ok())
        .map(str::to_owned);

    // Concurrency cap + bounded queue.
    let limiter = proxy.limiter(id, route.max_concurrent);
    let queued = limiter.as_ref().is_some_and(|s| s.available_permits() == 0);
    let conn = proxy.with_stats(id, |s| s.begin(request_id.clone(), queued));
    // hyper drops this future when the client disconnects while we wait for the engine; the
    // guard then closes the in-flight entry (every explicit return below has already done so).
    let mut guard = InflightGuard {
        agent: agent.clone(),
        id,
        conn,
        armed: true,
    };
    let started = Instant::now();
    let permit = match &limiter {
        Some(sem) => {
            match tokio::time::timeout(cfg.queue_timeout, sem.clone().acquire_owned()).await {
                Ok(Ok(p)) => Some(p),
                _ => {
                    proxy.with_stats(id, |s| s.abandon(conn));
                    return err(
                        StatusCode::SERVICE_UNAVAILABLE,
                        "instance_busy",
                        "instance concurrency limit reached",
                    );
                }
            }
        }
        None => None,
    };
    let queue_ms = started.elapsed().as_secs_f64() * 1000.0;
    proxy.with_stats(id, |s| s.dequeued(conn));

    let (parts, body) = req.into_parts();
    if let Some(len) = parts
        .headers
        .get(header::CONTENT_LENGTH)
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.parse::<u64>().ok())
    {
        if len > cfg.max_body {
            proxy.with_stats(id, |s| s.abandon(conn));
            return err(
                StatusCode::PAYLOAD_TOO_LARGE,
                "body_too_large",
                "request body exceeds max_body_bytes",
            );
        }
    }
    let body = match Limited::new(body, cfg.max_body as usize).collect().await {
        Ok(b) => b.to_bytes(),
        Err(_) => {
            proxy.with_stats(id, |s| s.abandon(conn));
            return err(
                StatusCode::PAYLOAD_TOO_LARGE,
                "body_too_large",
                "request body exceeds max_body_bytes or the client aborted",
            );
        }
    };
    let is_json = parts
        .headers
        .get(header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .map(|c| c.contains("json"))
        .unwrap_or(true);
    let body = if route.rewrite_model && is_json {
        rewrite_model(&body, &served).unwrap_or(body)
    } else {
        body
    };
    let streaming = is_json && is_stream_request(&body);
    if let Some(p) = prompt_estimate(&body) {
        proxy.with_stats(id, |s| s.set_prompt_tokens(conn, p));
    }

    let pq = match parts.uri.query() {
        Some(q) => format!("{engine_path}?{q}"),
        None => engine_path.clone(),
    };
    let uri: http::Uri = match format!("http://127.0.0.1:{port}{pq}").parse() {
        Ok(u) => u,
        Err(_) => {
            proxy.with_stats(id, |s| s.abandon(conn));
            return err(StatusCode::BAD_REQUEST, "bad_path", "invalid path");
        }
    };
    let mut up = Request::builder().method(parts.method.clone()).uri(uri);
    let headers = up.headers_mut().expect("builder");
    copy_headers(&parts.headers, headers, true);
    headers.insert(
        header::HOST,
        HeaderValue::from_str(&format!("127.0.0.1:{port}")).expect("host"),
    );
    headers.insert(header::CONTENT_LENGTH, HeaderValue::from(body.len()));
    let up = up.body(Full::new(body)).expect("request");

    let mut rec = RequestRecord {
        request_id: request_id.clone(),
        queue_ms,
        streamed: streaming,
        ..Default::default()
    };
    let resp = tokio::time::timeout(cfg.request_timeout, proxy.client.request(up)).await;
    let resp = match resp {
        Ok(Ok(r)) => r,
        Ok(Err(e)) => {
            rec.transport_error = true;
            rec.status = 502;
            rec.e2e_ms = started.elapsed().as_secs_f64() * 1000.0;
            proxy.errors_total.fetch_add(1, Ordering::Relaxed);
            proxy.with_stats(id, |s| s.finish(conn, rec));
            return err(
                StatusCode::BAD_GATEWAY,
                "engine_unavailable",
                &format!("engine connection failed: {e}"),
            );
        }
        Err(_) => {
            rec.transport_error = true;
            rec.status = 504;
            rec.e2e_ms = started.elapsed().as_secs_f64() * 1000.0;
            proxy.errors_total.fetch_add(1, Ordering::Relaxed);
            proxy.with_stats(id, |s| s.finish(conn, rec));
            return err(
                StatusCode::GATEWAY_TIMEOUT,
                "engine_timeout",
                "engine did not answer in time",
            );
        }
    };
    let (rparts, rbody) = resp.into_parts();
    rec.status = rparts.status.as_u16();
    if rec.status >= 500 {
        proxy.errors_total.fetch_add(1, Ordering::Relaxed);
    }
    let sse = rparts
        .headers
        .get(header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .is_some_and(|c| c.contains("event-stream"));
    let mut out = Response::builder().status(rparts.status);
    let oh = out.headers_mut().expect("builder");
    copy_headers(&rparts.headers, oh, false);
    oh.insert(
        "x-gpudeck-node",
        HeaderValue::from_str(&agent.node_id.to_string()).expect("hv"),
    );
    oh.insert(
        "x-gpudeck-instance",
        HeaderValue::from_str(&id.to_string()).expect("hv"),
    );
    oh.insert("x-accel-buffering", HeaderValue::from_static("no"));
    if let Some(rid) = &request_id {
        if let Ok(v) = HeaderValue::from_str(rid) {
            oh.insert("x-request-id", v);
        }
    }
    guard.armed = false; // the body observer owns the in-flight entry from here on
    let observed = body::Observed::new(
        rbody,
        agent.proxy_stats_sink(id, conn),
        rec,
        started,
        sse,
        cfg.idle_timeout,
        started + cfg.request_timeout,
        permit,
    );
    out.body(observed.boxed()).unwrap_or_else(|_| {
        err(
            StatusCode::BAD_GATEWAY,
            "engine_unavailable",
            "bad upstream response",
        )
    })
}

fn copy_headers(from: &HeaderMap, to: &mut HeaderMap, request: bool) {
    for (k, v) in from {
        let name = k.as_str();
        if HOP_BY_HOP.contains(&name) || name == "host" || name == "content-length" {
            continue;
        }
        if request && name.starts_with("x-gpudeck-") {
            continue;
        }
        to.append(k.clone(), v.clone());
    }
}

/// Callback the streaming body uses to update per-instance stats.
#[derive(Clone)]
pub struct StatsSink {
    agent: std::sync::Weak<Agent>,
    id: Uuid,
    conn: u64,
}

impl StatsSink {
    pub fn first_byte(&self) {
        if let Some(a) = self.agent.upgrade() {
            a.proxy.with_stats(self.id, |s| s.first_byte(self.conn));
        }
    }
    pub fn chunk(&self) {
        if let Some(a) = self.agent.upgrade() {
            a.proxy.with_stats(self.id, |s| s.chunk(self.conn));
        }
    }
    pub fn finish(&self, rec: RequestRecord) {
        if let Some(a) = self.agent.upgrade() {
            if rec.is_error() && rec.status < 500 {
                a.proxy.errors_total.fetch_add(1, Ordering::Relaxed);
            }
            a.proxy.with_stats(self.id, |s| s.finish(self.conn, rec));
        }
    }
}

impl Agent {
    pub fn proxy_stats_sink(self: &Arc<Self>, id: Uuid, conn: u64) -> StatsSink {
        StatsSink {
            agent: Arc::downgrade(self),
            id,
            conn,
        }
    }
}

/// Method helper for tests.
pub fn is_get(m: &Method) -> bool {
    m == Method::GET
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn path_rules() {
        let allowed = vec![
            "/v1/chat/completions".to_string(),
            "/v1/embeddings".into(),
            "/health".into(),
            "/v1/".into(),
        ];
        let blocked: Vec<String> = crate::backend::ALWAYS_BLOCKED_PATHS
            .iter()
            .map(|s| s.to_string())
            .collect();
        assert!(path_allowed("/v1/chat/completions", &allowed, &blocked));
        assert!(path_allowed("/v1/models", &allowed, &blocked));
        assert!(path_allowed("/health", &allowed, &blocked));
        assert!(!path_allowed("/slots", &allowed, &blocked));
        assert!(!path_allowed("/slots/0", &allowed, &blocked));
        assert!(!path_allowed("/models/load", &allowed, &blocked));
        assert!(!path_allowed("/metrics", &allowed, &blocked));
        assert!(!path_allowed("/props", &allowed, &blocked));
        assert!(!path_allowed("/v1/../slots", &allowed, &blocked));
        let narrow = vec!["/v1/chat/completions".to_string()];
        assert!(!path_allowed("/v1/chat/completionsX", &narrow, &blocked));
        assert!(!path_allowed("/v1/embeddings", &narrow, &blocked));
    }

    #[test]
    fn model_rewrite() {
        let b = rewrite_model(br#"{"model":"qwen3-0.6b","messages":[]}"#, "qwen3-0.6b-q4").unwrap();
        let v: serde_json::Value = serde_json::from_slice(&b).unwrap();
        assert_eq!(v["model"], "qwen3-0.6b-q4");
        assert!(rewrite_model(br#"{"model":"x"}"#, "x").is_none());
        assert!(rewrite_model(b"--boundary\r\n", "x").is_none());
        assert!(rewrite_model(br#"{"input":"a"}"#, "x").is_none());
        assert!(is_stream_request(br#"{"stream":true}"#));
    }
}
