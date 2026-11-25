//! Outbound serving tunnel (`protocol.md` §13), the default serving mode: the GPU machine
//! listens on no port at all. The agent dials every API-layer tunnel endpoint named by the
//! signed security policy (`tunnel.endpoints`, `tunnel.connections` connections each), over TLS
//! 1.3 with its node certificate, and accepts the server only when it chains to the internal
//! CA, matches the API-layer subject and carries a pinned key. On that connection the roles
//! flip: the API layer is the HTTP/2 client and the agent serves the same request handler as
//! the direct-mode proxy (routing table, drain, path allow-list, model rewrite, concurrency
//! caps, in-flight accounting, unbuffered streaming).
//!
//! A tunnel is redialled with exponential backoff (1 s ×2, full jitter, cap 60 s) when it
//! drops, and closed gracefully (in-flight streams finish) when the policy removes its
//! endpoint or when nothing — not even the API layer's keep-alive probe — arrived for
//! `tunnel.idle_timeout_seconds`.

use std::collections::{BTreeMap, HashMap};
use std::convert::Infallible;
use std::net::SocketAddr;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use anyhow::{anyhow, bail, Context, Result};
use hyper_util::rt::{TokioExecutor, TokioIo, TokioTimer};
use rustls::pki_types::ServerName;
use tokio_util::sync::CancellationToken;
use tracing::{debug, info, warn};

use crate::agent::Agent;
use crate::security;

/// Live tunnel state (Prometheus, `status`, `security audit`).
#[derive(Default)]
pub struct TunnelStatus {
    /// Open, authenticated tunnel connections.
    pub connected: AtomicU64,
    /// Requests served over tunnels.
    pub requests: AtomicU64,
    /// Per endpoint: open connections and the last error.
    endpoints: Mutex<BTreeMap<String, EndpointState>>,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct EndpointState {
    pub connected: u64,
    pub last_error: Option<String>,
}

impl TunnelStatus {
    pub fn endpoints(&self) -> BTreeMap<String, EndpointState> {
        self.endpoints
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .clone()
    }

    fn update(&self, endpoint: &str, f: impl FnOnce(&mut EndpointState)) {
        let mut g = self.endpoints.lock().unwrap_or_else(|e| e.into_inner());
        f(g.entry(endpoint.to_string()).or_default());
    }

    fn retain(&self, keep: &[String]) {
        self.endpoints
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .retain(|k, _| keep.contains(k));
    }
}

/// Supervisor: keeps one task per (endpoint, slot) the policy asks for.
pub async fn run(agent: Arc<Agent>) {
    let mut tasks: HashMap<(String, u32), CancellationToken> = HashMap::new();
    let mut warned_empty = false;
    loop {
        let policy = agent.policy();
        let endpoints = security::tunnel_endpoints(policy.as_ref());
        let per = policy
            .as_ref()
            .and_then(|p| p.tunnel.as_ref())
            .and_then(|t| t.connections)
            .unwrap_or(security::DEFAULT_TUNNEL_CONNECTIONS)
            .clamp(1, 16);
        let wanted: Vec<(String, u32)> = endpoints
            .iter()
            .flat_map(|e| (0..per).map(move |i| (e.clone(), i)))
            .collect();
        tasks.retain(|k, token| {
            let keep = wanted.contains(k);
            if !keep {
                info!(endpoint = %k.0, slot = k.1, "tunnel endpoint removed by the security policy");
                token.cancel();
            }
            keep
        });
        agent.tunnels.retain(&endpoints);
        for key in wanted {
            if tasks.contains_key(&key) {
                continue;
            }
            let token = agent.shutdown.child_token();
            tasks.insert(key.clone(), token.clone());
            tokio::spawn(connection_loop(agent.clone(), key.0, key.1, token));
        }
        if endpoints.is_empty() && !warned_empty {
            warned_empty = true;
            warn!("serving mode is tunnel but the security policy names no tunnel endpoint yet; the node serves nothing until the control plane pushes one");
        } else if !endpoints.is_empty() {
            warned_empty = false;
        }
        tokio::select! {
            _ = agent.policy_changed.notified() => {}
            _ = agent.shutdown.cancelled() => {
                for t in tasks.values() { t.cancel(); }
                return;
            }
        }
    }
}

async fn connection_loop(
    agent: Arc<Agent>,
    endpoint: String,
    slot: u32,
    cancel: CancellationToken,
) {
    let mut attempt = 0u32;
    let mut last_error = String::new();
    loop {
        if cancel.is_cancelled() {
            return;
        }
        let started = Instant::now();
        match connect_and_serve(&agent, &endpoint, &cancel).await {
            Ok(why) => {
                info!(%endpoint, slot, "tunnel closed: {why}");
                agent.tunnels.update(&endpoint, |s| s.last_error = None);
            }
            Err(e) => {
                let msg = format!("{e:#}");
                // A refused identity (pin / CA / subject) is security relevant: logged at warn
                // whenever the reason changes, otherwise every 10th attempt.
                if msg != last_error || attempt.is_multiple_of(10) {
                    warn!(%endpoint, slot, "tunnel failed: {msg}");
                } else {
                    debug!(%endpoint, slot, "tunnel failed: {msg}");
                }
                last_error.clone_from(&msg);
                agent.tunnels.update(&endpoint, |s| {
                    s.last_error = Some(msg.chars().take(300).collect())
                });
            }
        }
        if started.elapsed() >= Duration::from_secs(60) {
            attempt = 0;
        }
        let delay = crate::hub::backoff(attempt);
        attempt = attempt.saturating_add(1);
        tokio::select! {
            _ = tokio::time::sleep(delay) => {}
            _ = cancel.cancelled() => return,
        }
    }
}

fn set_keepalive(tcp: &tokio::net::TcpStream) {
    let ka = socket2::TcpKeepalive::new()
        .with_time(Duration::from_secs(30))
        .with_interval(Duration::from_secs(10));
    let _ = socket2::SockRef::from(tcp).set_tcp_keepalive(&ka);
}

/// Dial, authenticate the API layer, then serve HTTP/2 until the connection ends.
async fn connect_and_serve(
    agent: &Arc<Agent>,
    endpoint: &str,
    cancel: &CancellationToken,
) -> Result<String> {
    let (host, port) = security::split_endpoint(endpoint)
        .ok_or_else(|| anyhow!("invalid tunnel endpoint {endpoint:?}"))?;
    let policy = agent.policy();
    let (pins, subjects) = security::api_layer_identity(policy.as_ref());
    let cfg = agent
        .identity()
        .tunnel_client_config(&pins, &subjects)
        .context("tunnel TLS config")?;
    let tcp = tokio::select! {
        r = tokio::time::timeout(Duration::from_secs(10), tokio::net::TcpStream::connect((host.as_str(), port))) => {
            r.map_err(|_| anyhow!("connect timeout"))?.with_context(|| format!("connect {endpoint}"))?
        }
        _ = cancel.cancelled() => return Ok("cancelled".into()),
    };
    let _ = tcp.set_nodelay(true);
    set_keepalive(&tcp);
    let peer: SocketAddr = tcp.peer_addr().context("peer address")?;
    let name = ServerName::try_from(host.clone()).context("tunnel server name")?;
    let tls = tokio::time::timeout(
        Duration::from_secs(10),
        tokio_rustls::TlsConnector::from(Arc::new(cfg)).connect(name, tcp),
    )
    .await
    .map_err(|_| anyhow!("TLS handshake timeout"))?
    .context("TLS handshake (API layer identity)")?;
    if tls.get_ref().1.alpn_protocol() != Some(b"h2".as_slice()) {
        bail!("tunnel endpoint did not negotiate HTTP/2");
    }
    let idle = Duration::from_secs(
        policy
            .as_ref()
            .and_then(|p| p.tunnel.as_ref())
            .and_then(|t| t.idle_timeout_seconds)
            .unwrap_or(security::DEFAULT_TUNNEL_IDLE_SECONDS)
            .max(10),
    );
    let origin = Instant::now();
    let last = Arc::new(AtomicU64::new(0));
    let a2 = agent.clone();
    let l2 = last.clone();
    let svc = hyper::service::service_fn(move |req| {
        let a = a2.clone();
        l2.store(origin.elapsed().as_millis() as u64, Ordering::Relaxed);
        a.tunnels.requests.fetch_add(1, Ordering::Relaxed);
        async move { Ok::<_, Infallible>(crate::proxy::handle(a, req, peer).await) }
    });
    let conn = hyper::server::conn::http2::Builder::new(TokioExecutor::new())
        .timer(TokioTimer::new())
        .max_concurrent_streams(1024)
        .serve_connection(TokioIo::new(tls), svc);
    tokio::pin!(conn);
    agent.tunnels.connected.fetch_add(1, Ordering::Relaxed);
    agent.tunnels.update(endpoint, |s| {
        s.connected += 1;
        s.last_error = None;
    });
    info!(%endpoint, %peer, "serving tunnel connected");
    let drain = Duration::from_secs(agent.rt().drain_default().clamp(5, 600));
    let why = loop {
        tokio::select! {
            r = &mut conn => break match r {
                Ok(()) => Ok("closed by the API layer".to_string()),
                Err(e) => Err(anyhow!("tunnel connection error: {e}")),
            },
            _ = cancel.cancelled() => {
                conn.as_mut().graceful_shutdown();
                let _ = tokio::time::timeout(drain, &mut conn).await;
                break Ok("endpoint removed or agent stopping".to_string());
            }
            _ = tokio::time::sleep(Duration::from_secs(5)) => {
                let since = origin.elapsed().as_millis() as u64 - last.load(Ordering::Relaxed);
                if Duration::from_millis(since) > idle {
                    conn.as_mut().graceful_shutdown();
                    let _ = tokio::time::timeout(Duration::from_secs(10), &mut conn).await;
                    break Ok(format!("idle for {} s (no request or keep-alive probe)", idle.as_secs()));
                }
            }
        }
    };
    agent.tunnels.connected.fetch_sub(1, Ordering::Relaxed);
    agent
        .tunnels
        .update(endpoint, |s| s.connected = s.connected.saturating_sub(1));
    why
}
