//! A minimal agent hub for tests and local runs (never used by the production agent): its own
//! test CA, `POST /agent/v1/join` (one-time token, CSR signing as the real hub does) and the
//! mTLS WebSocket `/agent/v1/ws` (client certificate required, CN must equal
//! `envelope.node_id`, `hello_ack`, optional initial `config_push`, command replay on
//! reconnect). Every frame received from the agent is kept for assertions.

use std::collections::{HashMap, HashSet};
use std::net::SocketAddr;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use anyhow::{Context, Result};
use bytes::Bytes;
use futures::{SinkExt, StreamExt};
use gpudeck_protocol::{
    Command, CommandResult, ConfigPush, Envelope, HelloAck, Intervals, JoinRequest, JoinResponse,
    Message, HUB_JOIN_PATH, HUB_WS_PATH,
};
use http::{header, Request, Response, StatusCode};
use http_body_util::{BodyExt, Full};
use hyper::body::Incoming;
use hyper_util::rt::TokioIo;
use tokio::sync::{mpsc, Notify};
use tokio_tungstenite::tungstenite::protocol::Role;
use tokio_tungstenite::tungstenite::Message as Ws;
use uuid::Uuid;

use crate::pki::{inspect_cert, NODE_ORG};
use crate::testpki::{Issued, TestCa};

pub struct HubState {
    pub ca: TestCa,
    pub tokens: Mutex<HashSet<String>>,
    pub nodes: Mutex<HashMap<Uuid, String>>,
    /// Every frame received from agents (parsed) and the raw JSON text.
    pub received: Mutex<Vec<(Envelope, String)>>,
    pub notify: Notify,
    pub to_agent: Mutex<Option<mpsc::UnboundedSender<Ws>>>,
    pub pending: Mutex<Vec<Envelope>>,
    pub initial_config: Mutex<Option<ConfigPush>>,
    pub intervals: Mutex<Intervals>,
    pub connections: AtomicU64,
    pub reject_reason: Mutex<Option<String>>,
}

pub struct FakeHub {
    pub addr: SocketAddr,
    pub state: Arc<HubState>,
    shutdown: tokio_util::sync::CancellationToken,
}

impl Drop for FakeHub {
    fn drop(&mut self) {
        self.shutdown.cancel();
    }
}

type Body = http_body_util::combinators::BoxBody<Bytes, std::convert::Infallible>;

fn resp(status: StatusCode, v: serde_json::Value) -> Response<Body> {
    let mut r = Response::new(Full::new(Bytes::from(v.to_string())).boxed());
    *r.status_mut() = status;
    r.headers_mut().insert(
        header::CONTENT_TYPE,
        "application/json".parse().expect("hv"),
    );
    r
}

impl FakeHub {
    /// Start on `addr` (use `127.0.0.1:0` for a random port) accepting the join `token`.
    pub async fn start(addr: SocketAddr, token: &str) -> Result<Self> {
        crate::tls::init_crypto();
        let ca = TestCa::new("GPU Deck Fake Hub CA");
        let server = ca.issue_server("agent-hub", &["localhost", "127.0.0.1"]);
        let mut roots = rustls::RootCertStore::empty();
        roots.add(rustls::pki_types::CertificateDer::from(
            ca.cert.der().to_vec(),
        ))?;
        let provider = rustls::crypto::CryptoProvider::get_default()
            .cloned()
            .expect("provider");
        let verifier = rustls::server::WebPkiClientVerifier::builder_with_provider(
            Arc::new(roots),
            provider.clone(),
        )
        .allow_unauthenticated()
        .build()?;
        let mut cfg = rustls::ServerConfig::builder_with_provider(provider)
            .with_protocol_versions(&[&rustls::version::TLS13])?
            .with_client_cert_verifier(verifier)
            .with_single_cert(
                crate::pki::parse_cert_bundle(&server.cert_pem)?,
                crate::tls::parse_private_key(server.key_pem.as_deref().expect("key"))?,
            )?;
        cfg.alpn_protocols = vec![b"http/1.1".to_vec()];
        let acceptor = tokio_rustls::TlsAcceptor::from(Arc::new(cfg));
        let listener = tokio::net::TcpListener::bind(addr)
            .await
            .with_context(|| format!("bind {addr}"))?;
        let addr = listener.local_addr()?;
        let state = Arc::new(HubState {
            ca,
            tokens: Mutex::new([token.to_string()].into_iter().collect()),
            nodes: Mutex::new(HashMap::new()),
            received: Mutex::new(vec![]),
            notify: Notify::new(),
            to_agent: Mutex::new(None),
            pending: Mutex::new(vec![]),
            initial_config: Mutex::new(None),
            intervals: Mutex::new(Intervals {
                heartbeat_seconds: 2,
                metrics_seconds: 1.0,
                inventory_seconds: 10,
                command_ack_timeout_seconds: 10,
            }),
            connections: AtomicU64::new(0),
            reject_reason: Mutex::new(None),
        });
        let shutdown = tokio_util::sync::CancellationToken::new();
        let (st, sd) = (state.clone(), shutdown.clone());
        tokio::spawn(async move {
            loop {
                let (tcp, _) = tokio::select! {
                    r = listener.accept() => match r { Ok(x) => x, Err(_) => continue },
                    _ = sd.cancelled() => return,
                };
                let acceptor = acceptor.clone();
                let st = st.clone();
                let sd = sd.clone();
                tokio::spawn(async move {
                    let Ok(tls) = acceptor.accept(tcp).await else {
                        return;
                    };
                    let peer_cn = tls
                        .get_ref()
                        .1
                        .peer_certificates()
                        .and_then(|c| c.first())
                        .and_then(|c| inspect_cert(c.as_ref()).ok())
                        .and_then(|i| i.common_name);
                    let svc = hyper::service::service_fn(move |req| {
                        let st = st.clone();
                        let cn = peer_cn.clone();
                        let sd = sd.clone();
                        async move { Ok::<_, std::convert::Infallible>(route(st, req, cn, sd).await) }
                    });
                    let _ = hyper::server::conn::http1::Builder::new()
                        .serve_connection(TokioIo::new(tls), svc)
                        .with_upgrades()
                        .await;
                });
            }
        });
        Ok(Self {
            addr,
            state,
            shutdown,
        })
    }

    pub fn url(&self) -> String {
        format!("https://127.0.0.1:{}", self.addr.port())
    }

    pub fn ca_pem(&self) -> String {
        self.state.ca.ca_pem()
    }

    pub fn ca_fingerprint(&self) -> String {
        self.state.ca.fingerprint()
    }

    /// Client certificate the API layer would present to the agent proxy.
    pub fn issue_client(&self, cn: &str, org: &str) -> Issued {
        self.state.ca.issue_client(cn, org)
    }

    pub fn set_initial_config(&self, c: ConfigPush) {
        *self
            .state
            .initial_config
            .lock()
            .unwrap_or_else(|e| e.into_inner()) = Some(c);
    }

    pub fn set_intervals(&self, i: Intervals) {
        *self
            .state
            .intervals
            .lock()
            .unwrap_or_else(|e| e.into_inner()) = i;
    }

    pub fn connections(&self) -> u64 {
        self.state.connections.load(Ordering::SeqCst)
    }

    pub fn is_connected(&self) -> bool {
        self.state
            .to_agent
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .as_ref()
            .is_some_and(|t| !t.is_closed())
    }

    /// Send a message to the connected agent; returns the envelope id.
    pub fn send(&self, node_id: Option<Uuid>, message: Message) -> Uuid {
        let env = Envelope::new(node_id, message);
        let id = env.id;
        self.send_envelope(env);
        id
    }

    pub fn send_envelope(&self, env: Envelope) {
        let text = env.to_json().expect("json");
        if let Some(tx) = self
            .state
            .to_agent
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .as_ref()
        {
            let _ = tx.send(Ws::Text(text.into()));
        }
    }

    /// Send a command; it is also replayed (same id) on every reconnect until a terminal result.
    pub fn command(&self, node_id: Uuid, cmd: Command) -> Uuid {
        let env = Envelope::new(Some(node_id), Message::Command(cmd));
        let id = env.id;
        self.state
            .pending
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .push(env.clone());
        self.send_envelope(env);
        id
    }

    /// Close the current agent connection (simulates a network drop).
    pub fn kick(&self) {
        if let Some(tx) = self
            .state
            .to_agent
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .take()
        {
            let _ = tx.send(Ws::Close(None));
        }
    }

    pub fn received(&self) -> Vec<Envelope> {
        self.state
            .received
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .iter()
            .map(|(e, _)| e.clone())
            .collect()
    }

    pub fn received_raw(&self) -> Vec<String> {
        self.state
            .received
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .iter()
            .map(|(_, t)| t.clone())
            .collect()
    }

    /// Wait for a received frame matching `pred`, searching frames from index `from`.
    pub async fn wait_for(
        &self,
        from: usize,
        timeout: Duration,
        pred: impl Fn(&Envelope) -> bool,
    ) -> Option<Envelope> {
        let deadline = tokio::time::Instant::now() + timeout;
        loop {
            let notified = self.state.notify.notified();
            if let Some(e) = self
                .state
                .received
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .iter()
                .skip(from)
                .map(|(e, _)| e)
                .find(|e| pred(e))
            {
                return Some(e.clone());
            }
            if tokio::time::timeout_at(deadline, notified).await.is_err() {
                return None;
            }
        }
    }

    /// Wait for the terminal result of command `id`.
    pub async fn wait_result(&self, id: Uuid, timeout: Duration) -> Option<CommandResult> {
        let env = self
            .wait_for(0, timeout, |e| {
                e.correlation_id == Some(id)
                    && matches!(&e.message, Message::CommandResult(r) if r.status.is_terminal())
            })
            .await?;
        match env.message {
            Message::CommandResult(r) => Some(r),
            _ => None,
        }
    }
}

async fn route(
    st: Arc<HubState>,
    req: Request<Incoming>,
    cn: Option<String>,
    sd: tokio_util::sync::CancellationToken,
) -> Response<Body> {
    let path = req.uri().path().to_string();
    if path == HUB_JOIN_PATH && req.method() == http::Method::POST {
        let host = req
            .headers()
            .get(header::HOST)
            .and_then(|h| h.to_str().ok())
            .unwrap_or("127.0.0.1:8443")
            .to_string();
        let body = match req.into_body().collect().await {
            Ok(b) => b.to_bytes(),
            Err(_) => {
                return resp(
                    StatusCode::BAD_REQUEST,
                    serde_json::json!({"success": false}),
                )
            }
        };
        let Ok(jr) = serde_json::from_slice::<JoinRequest>(&body) else {
            return resp(
                StatusCode::UNPROCESSABLE_ENTITY,
                serde_json::json!({"success": false, "error": {"code": "CSR_INVALID", "message": "bad request"}}),
            );
        };
        if !st
            .tokens
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .remove(&jr.join_token)
        {
            return resp(
                StatusCode::UNAUTHORIZED,
                serde_json::json!({"success": false, "data": null, "error": {"code": "JOIN_TOKEN_INVALID", "message": "join token is invalid or used"}}),
            );
        }
        let node_id = Uuid::new_v4();
        let issued = match st
            .ca
            .sign_csr(&jr.csr_pem, &node_id.to_string(), NODE_ORG, 365)
        {
            Ok(i) => i,
            Err(e) => {
                return resp(
                    StatusCode::UNPROCESSABLE_ENTITY,
                    serde_json::json!({"success": false, "error": {"code": "CSR_INVALID", "message": e.to_string()}}),
                )
            }
        };
        st.nodes
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .insert(node_id, jr.name.clone());
        let jresp = JoinResponse {
            node_id,
            node_name: jr.name,
            cluster_id: Some(Uuid::new_v4()),
            certificate_pem: issued.cert_pem,
            ca_bundle_pem: st.ca.ca_pem(),
            serial: issued.serial_hex,
            not_after: issued.not_after,
            hub_ws_url: format!("wss://{host}{HUB_WS_PATH}"),
            intervals: st
                .intervals
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .clone(),
        };
        return resp(
            StatusCode::OK,
            serde_json::json!({"success": true, "data": jresp, "error": null}),
        );
    }
    if path == "/agent/v1/ca" {
        return resp(
            StatusCode::OK,
            serde_json::json!({"success": true, "data": {"certificate_pem": st.ca.ca_pem(), "fingerprint_sha256": st.ca.fingerprint()}, "error": null}),
        );
    }
    if path == HUB_WS_PATH {
        let Some(cn) = cn else {
            return resp(
                StatusCode::UNAUTHORIZED,
                serde_json::json!({"error": "client certificate required"}),
            );
        };
        let Some(key) = req.headers().get("sec-websocket-key").cloned() else {
            return resp(
                StatusCode::BAD_REQUEST,
                serde_json::json!({"error": "not a websocket upgrade"}),
            );
        };
        let accept = tokio_tungstenite::tungstenite::handshake::derive_accept_key(key.as_bytes());
        tokio::spawn(async move {
            if let Ok(up) = hyper::upgrade::on(req).await {
                let ws = tokio_tungstenite::WebSocketStream::from_raw_socket(
                    TokioIo::new(up),
                    Role::Server,
                    None,
                )
                .await;
                session(st, ws, cn, sd).await;
            }
        });
        let mut r = Response::new(Full::new(Bytes::new()).boxed());
        *r.status_mut() = StatusCode::SWITCHING_PROTOCOLS;
        r.headers_mut()
            .insert(header::UPGRADE, "websocket".parse().expect("hv"));
        r.headers_mut()
            .insert(header::CONNECTION, "Upgrade".parse().expect("hv"));
        r.headers_mut()
            .insert("sec-websocket-accept", accept.parse().expect("hv"));
        return r;
    }
    resp(
        StatusCode::NOT_FOUND,
        serde_json::json!({"error": "not found"}),
    )
}

async fn session<S>(
    st: Arc<HubState>,
    ws: tokio_tungstenite::WebSocketStream<S>,
    cn: String,
    sd: tokio_util::sync::CancellationToken,
) where
    S: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin + Send + 'static,
{
    let (mut sink, mut stream) = ws.split();
    // hello
    let Some(Ok(Ws::Text(t))) = stream.next().await else {
        return;
    };
    let Ok(hello) = Envelope::from_json(&t) else {
        return;
    };
    let node_id = hello.node_id.unwrap_or_default();
    if node_id.to_string() != cn {
        let _ = sink
            .send(Ws::Close(Some(
                tokio_tungstenite::tungstenite::protocol::CloseFrame {
                    code: 4001.into(),
                    reason: "identity mismatch".into(),
                },
            )))
            .await;
        return;
    }
    st.received
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .push((hello.clone(), t.to_string()));
    st.notify.notify_waiters();
    let reject = st
        .reject_reason
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .clone();
    let ack = HelloAck {
        accepted: reject.is_none(),
        reason: reject.clone(),
        server_time: gpudeck_protocol::now(),
        node_id,
        node_name: st
            .nodes
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .get(&node_id)
            .cloned(),
        cluster_id: None,
        intervals: st
            .intervals
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .clone(),
        config_version: st
            .initial_config
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .as_ref()
            .map(|c| c.config_version)
            .unwrap_or(0),
        min_agent_version: None,
        drain: false,
        features: vec![
            gpudeck_protocol::hub_feature::OBSERVED_ENGINES.into(),
            gpudeck_protocol::hub_feature::GPU_PROCESSES.into(),
        ],
    };
    let ack_env = Envelope::new(Some(node_id), Message::HelloAck(ack)).with_correlation(hello.id);
    if sink
        .send(Ws::Text(ack_env.to_json().expect("json").into()))
        .await
        .is_err()
    {
        return;
    }
    if reject.is_some() {
        let _ = sink
            .send(Ws::Close(Some(
                tokio_tungstenite::tungstenite::protocol::CloseFrame {
                    code: 4003.into(),
                    reason: "rejected".into(),
                },
            )))
            .await;
        return;
    }
    let (tx, mut rx) = mpsc::unbounded_channel::<Ws>();
    if let Some(old) = st
        .to_agent
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .replace(tx.clone())
    {
        let _ = old.send(Ws::Close(Some(
            tokio_tungstenite::tungstenite::protocol::CloseFrame {
                code: 4004.into(),
                reason: "superseded".into(),
            },
        )));
    }
    st.connections.fetch_add(1, Ordering::SeqCst);
    if let Some(c) = st
        .initial_config
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .clone()
    {
        let _ = tx.send(Ws::Text(
            Envelope::new(Some(node_id), Message::ConfigPush(c))
                .to_json()
                .expect("json")
                .into(),
        ));
    }
    // Replay commands without a terminal result (same envelope id).
    let pending: Vec<Envelope> = st.pending.lock().unwrap_or_else(|e| e.into_inner()).clone();
    for env in pending {
        let done = st
            .received
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .iter()
            .any(|(r, _)| {
                r.correlation_id == Some(env.id)
                    && matches!(&r.message, Message::CommandResult(x) if x.status.is_terminal())
            });
        if !done {
            let _ = tx.send(Ws::Text(env.to_json().expect("json").into()));
        }
    }
    st.notify.notify_waiters();
    loop {
        tokio::select! {
            out = rx.recv() => match out {
                Some(Ws::Close(f)) => { let _ = sink.send(Ws::Close(f)).await; break; }
                Some(m) => if sink.send(m).await.is_err() { break },
                None => break,
            },
            inc = stream.next() => match inc {
                Some(Ok(Ws::Text(t))) => {
                    match Envelope::from_json(&t) {
                        Ok(env) => {
                            st.received.lock().unwrap_or_else(|e| e.into_inner()).push((env, t.to_string()));
                            st.notify.notify_waiters();
                        }
                        Err(e) => tracing::error!("fake hub: unparseable agent frame: {e}: {t}"),
                    }
                }
                Some(Ok(Ws::Close(_))) | None | Some(Err(_)) => break,
                Some(Ok(_)) => {}
            },
            _ = sd.cancelled() => break,
        }
    }
    let mut g = st.to_agent.lock().unwrap_or_else(|e| e.into_inner());
    if g.as_ref().is_some_and(|t| t.same_channel(&tx)) {
        *g = None;
    }
}
