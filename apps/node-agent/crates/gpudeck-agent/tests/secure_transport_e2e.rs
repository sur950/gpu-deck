//! End-to-end: serving modes and the node security policy (`protocol.md` §13) with the real
//! agent against the in-process fake hub.
//!
//! - Tunnel mode (default): the agent opens no listener, dials the tunnel endpoints of the
//!   CA-signed policy, accepts only the pinned API-layer key (an endpoint presenting another
//!   valid services certificate is refused), serves streamed requests unbuffered with in-flight
//!   accounting and drain over the tunnel; forged and rolled-back policies are rejected.
//! - Direct mode: sources outside the allow-list are reset before TLS, every client identity
//!   except the pinned API layer fails inside the TLS handshake (no HTTP byte comes back),
//!   handshakes are rate limited, and the rejected attempts arrive at the hub as one critical
//!   `ingress_rejected` event.
//! - Latency: tunnel vs direct over a link with a simulated round trip (a delaying TCP relay
//!   between the API-layer stand-in and the agent), warm connections and a cold first request.

use std::convert::Infallible;
use std::net::SocketAddr;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use async_trait::async_trait;
use bytes::Bytes;
use gpudeck_agent::backend::{DeviceReading, DeviceSource, GenericCatalog};
use gpudeck_agent::fakehub::FakeHub;
use gpudeck_agent::join::{join, JoinOptions};
use gpudeck_agent::testpki::Issued;
use gpudeck_agent::{Agent, AgentConfig, RunOptions};
use gpudeck_protocol::*;
use http_body_util::{BodyExt, Full};
use hyper_util::rt::{TokioExecutor, TokioIo};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::sync::mpsc;
use uuid::Uuid;

const T: Duration = Duration::from_secs(30);

struct NoDevices;

#[async_trait]
impl DeviceSource for NoDevices {
    async fn detect(&self) -> Vec<Device> {
        vec![]
    }
    async fn sample(&self) -> Vec<DeviceReading> {
        vec![]
    }
    fn backends(&self) -> Vec<String> {
        vec![]
    }
}

/// Loopback engine: `POST /v1/chat/completions` streams `n` SSE chunks `gap` apart.
async fn engine(gap: Duration) -> u16 {
    let l = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = l.local_addr().unwrap().port();
    tokio::spawn(async move {
        while let Ok((tcp, _)) = l.accept().await {
            tokio::spawn(async move {
                let svc = hyper::service::service_fn(
                    move |req: http::Request<hyper::body::Incoming>| async move {
                        let body = req.into_body().collect().await.unwrap().to_bytes();
                        let v: serde_json::Value =
                            serde_json::from_slice(&body).unwrap_or_default();
                        let (tx, rx) =
                            mpsc::channel::<Result<hyper::body::Frame<Bytes>, Infallible>>(8);
                        let model = v["model"].as_str().unwrap_or("").to_string();
                        tokio::spawn(async move {
                            for i in 0..3 {
                                let chunk =
                                    format!("data: {{\"model\":\"{model}\",\"i\":{i}}}\n\n");
                                if tx
                                    .send(Ok(hyper::body::Frame::data(Bytes::from(chunk))))
                                    .await
                                    .is_err()
                                {
                                    return;
                                }
                                tokio::time::sleep(gap).await;
                            }
                            let _ = tx
                                .send(Ok(hyper::body::Frame::data(Bytes::from_static(
                                    b"data: [DONE]\n\n",
                                ))))
                                .await;
                        });
                        let mut r = http::Response::new(http_body_util::StreamBody::new(
                            tokio_stream_from(rx),
                        ));
                        r.headers_mut()
                            .insert("content-type", "text/event-stream".parse().unwrap());
                        Ok::<_, Infallible>(r)
                    },
                );
                let _ = hyper::server::conn::http1::Builder::new()
                    .serve_connection(TokioIo::new(tcp), svc)
                    .await;
            });
        }
    });
    port
}

fn tokio_stream_from(
    mut rx: mpsc::Receiver<Result<hyper::body::Frame<Bytes>, Infallible>>,
) -> impl futures::Stream<Item = Result<hyper::body::Frame<Bytes>, Infallible>> {
    futures::stream::poll_fn(move |cx| rx.poll_recv(cx))
}

/// What a tunnel endpoint saw.
struct TunnelServer {
    addr: SocketAddr,
    conns: mpsc::UnboundedReceiver<(hyper::client::conn::http2::SendRequest<Full<Bytes>>, String)>,
    failed: Arc<AtomicU64>,
}

/// A stand-in for the API layer's tunnel listener: TLS 1.3 with `server` as certificate, node
/// client certificates from `ca_pem` required, ALPN h2, then an HTTP/2 *client* on the socket.
async fn tunnel_server(ca_pem: &str, server: &Issued) -> TunnelServer {
    gpudeck_agent::tls::init_crypto();
    let mut roots = rustls::RootCertStore::empty();
    for c in gpudeck_agent::pki::parse_cert_bundle(ca_pem).unwrap() {
        roots.add(c).unwrap();
    }
    let provider = rustls::crypto::CryptoProvider::get_default()
        .cloned()
        .unwrap();
    let verifier = rustls::server::WebPkiClientVerifier::builder_with_provider(
        Arc::new(roots),
        provider.clone(),
    )
    .build()
    .unwrap();
    let mut cfg = rustls::ServerConfig::builder_with_provider(provider)
        .with_protocol_versions(&[&rustls::version::TLS13])
        .unwrap()
        .with_client_cert_verifier(verifier)
        .with_single_cert(
            gpudeck_agent::pki::parse_cert_bundle(&server.cert_pem).unwrap(),
            gpudeck_agent::tls::parse_private_key(server.key_pem.as_deref().unwrap()).unwrap(),
        )
        .unwrap();
    cfg.alpn_protocols = vec![b"h2".to_vec()];
    let acceptor = tokio_rustls::TlsAcceptor::from(Arc::new(cfg));
    let l = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = l.local_addr().unwrap();
    let (tx, rx) = mpsc::unbounded_channel();
    let failed = Arc::new(AtomicU64::new(0));
    let f2 = failed.clone();
    tokio::spawn(async move {
        while let Ok((tcp, _)) = l.accept().await {
            let acceptor = acceptor.clone();
            let tx = tx.clone();
            let failed = f2.clone();
            // Like the API layer's endpoint (asyncio sets TCP_NODELAY): without it Linux holds
            // small HTTP/2 writes for a delayed ACK (~40 ms per round trip).
            let _ = tcp.set_nodelay(true);
            tokio::spawn(async move {
                let tls = match acceptor.accept(tcp).await {
                    Ok(t) => t,
                    Err(_) => {
                        failed.fetch_add(1, Ordering::SeqCst);
                        return;
                    }
                };
                let cn = tls
                    .get_ref()
                    .1
                    .peer_certificates()
                    .and_then(|c| c.first())
                    .and_then(|c| gpudeck_agent::pki::inspect_cert(c.as_ref()).ok())
                    .and_then(|i| i.common_name)
                    .unwrap_or_default();
                let Ok((send, conn)) =
                    hyper::client::conn::http2::handshake(TokioExecutor::new(), TokioIo::new(tls))
                        .await
                else {
                    failed.fetch_add(1, Ordering::SeqCst);
                    return;
                };
                tokio::spawn(conn);
                let _ = tx.send((send, cn));
            });
        }
    });
    TunnelServer {
        addr,
        conns: rx,
        failed,
    }
}

fn spki(i: &Issued) -> String {
    gpudeck_agent::pki::spki_sha256(&gpudeck_agent::pki::parse_cert_bundle(&i.cert_pem).unwrap()[0])
        .unwrap()
}

fn route(id: Uuid, port: u16, accepting: bool) -> RoutingEntry {
    RoutingEntry {
        instance_id: id,
        deployment_id: Uuid::new_v4(),
        model_name: Some("tiny".into()),
        model_type: Some(ModelType::Chat),
        engine: Some(EngineName::Custom),
        port,
        served_model_name: "tiny-q4".into(),
        rewrite_model: true,
        allowed_path_prefixes: vec!["/v1/chat/completions".into(), "/health".into()],
        accepting,
        max_concurrent: None,
    }
}

fn push(
    version: u64,
    routing: Option<Vec<RoutingEntry>>,
    security: Option<SignedPolicy>,
) -> Message {
    Message::ConfigPush(ConfigPush {
        config_version: version,
        full: false,
        routing,
        egress_allowlist: None,
        engine_catalog: None,
        crl: None,
        ca_bundle_pem: None,
        settings: None,
        model_sources: None,
        security,
    })
}

async fn joined_agent(
    hub: &FakeHub,
    dir: &std::path::Path,
    opts: RunOptions,
) -> (Arc<Agent>, Uuid) {
    let token = "gdj_testtoken0123456789";
    let cfg_path = dir.join("agent.toml");
    let mut cfg = AgentConfig {
        state_dir: dir.join("state"),
        prom_port: 0,
        firewall: "off".into(),
        ..Default::default()
    };
    let joined = join(
        &mut cfg,
        &cfg_path,
        JoinOptions {
            hub_url: hub.url(),
            token: token.into(),
            name: Some("secure-node".into()),
            ca_fingerprint: Some(hub.ca_fingerprint()),
            proxy_port: 9443,
            labels: Default::default(),
        },
    )
    .await
    .unwrap();
    assert!(
        cfg.state_dir.join("security_policy.json").exists(),
        "the signed policy from the join response is stored"
    );
    let agent = Agent::new(
        cfg,
        cfg_path,
        Arc::new(NoDevices),
        Arc::new(GenericCatalog),
        RunOptions {
            no_prom: true,
            ..opts
        },
    )
    .unwrap();
    let a2 = agent.clone();
    tokio::spawn(async move { a2.run().await });
    (agent, joined.node_id)
}

async fn config_event(hub: &FakeHub, from: usize, version: u64, kind: EventKind) -> Event {
    let env = hub
        .wait_for(from, T, |e| {
            matches!(&e.message, Message::Event(ev) if ev.kind == kind
                && ev.details.as_ref().and_then(|d| d.get("config_version")).and_then(|v| v.as_u64()) == Some(version))
        })
        .await
        .unwrap_or_else(|| panic!("no {kind:?} for config v{version}"));
    match env.message {
        Message::Event(ev) => ev,
        _ => unreachable!(),
    }
}

fn received(hub: &FakeHub) -> usize {
    hub.state.received.lock().unwrap().len()
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn tunnel_mode_serves_over_an_outbound_connection_to_the_pinned_api_layer() {
    gpudeck_agent::tls::init_crypto();
    let hub = FakeHub::start("127.0.0.1:0".parse().unwrap(), "gdj_testtoken0123456789")
        .await
        .unwrap();
    let api = hub.issue_client("api-layer", "gpudeck-services");
    // Valid certificate from the same CA with the same subject, but not the pinned key.
    let impostor = hub.issue_client("api-layer", "gpudeck-services");
    let mut good = tunnel_server(&hub.ca_pem(), &api).await;
    let bad = tunnel_server(&hub.ca_pem(), &impostor).await;
    *hub.state.policy_template.lock().unwrap() = Some(SecurityPolicy {
        api_layer: Some(IdentityPins {
            spki_sha256: vec![spki(&api)],
            subjects: vec![],
        }),
        tunnel: Some(TunnelPolicy {
            endpoints: vec![good.addr.to_string(), bad.addr.to_string()],
            connections: Some(1),
            idle_timeout_seconds: Some(30),
        }),
        ..Default::default()
    });
    let dir = tempfile::tempdir().unwrap();
    let (agent, node) = joined_agent(&hub, dir.path(), RunOptions::default()).await;

    let (mut send, cn) = tokio::time::timeout(T, good.conns.recv())
        .await
        .unwrap()
        .unwrap();
    assert_eq!(
        cn,
        node.to_string(),
        "the tunnel authenticates the node certificate"
    );
    assert!(
        agent.proxy_addr.lock().unwrap().is_none(),
        "tunnel mode opens no listener"
    );
    let hello = hub
        .wait_for(0, T, |e| matches!(e.message, Message::Hello(_)))
        .await
        .unwrap();
    let Message::Hello(h) = hello.message else {
        unreachable!()
    };
    assert_eq!(h.proxy.mode, Some(ServingMode::Tunnel));
    assert_eq!(h.proxy.bind, None);

    // The impostor endpoint is refused by the agent inside the TLS handshake.
    let deadline = Instant::now() + T;
    loop {
        let err = agent
            .tunnels
            .endpoints()
            .get(&bad.addr.to_string())
            .and_then(|s| s.last_error.clone());
        if let Some(e) = err {
            assert!(e.contains("not pinned"), "{e}");
            break;
        }
        assert!(
            Instant::now() < deadline,
            "impostor endpoint was not refused"
        );
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    assert!(bad.failed.load(Ordering::SeqCst) >= 1);

    // Routing, then a streamed request through the tunnel.
    let port = engine(Duration::from_millis(300)).await;
    let id = Uuid::new_v4();
    let from = received(&hub);
    hub.send(Some(node), push(2, Some(vec![route(id, port, true)]), None));
    config_event(&hub, from, 2, EventKind::ConfigApplied).await;
    let req = http::Request::post(format!("https://tunnel/instances/{id}/v1/chat/completions"))
        .header("content-type", "application/json")
        .header("x-request-id", "4b1e3c7a-0000-4000-8000-000000000001")
        .body(Full::new(Bytes::from_static(
            br#"{"model":"tiny","stream":true}"#,
        )))
        .unwrap();
    let started = Instant::now();
    let resp = send.send_request(req).await.unwrap();
    assert_eq!(resp.status(), 200);
    assert_eq!(resp.headers()["x-gpudeck-node"], node.to_string());
    let mut body = resp.into_body();
    let first = loop {
        match body.frame().await.unwrap().unwrap().into_data() {
            Ok(d) if !d.is_empty() => break d,
            _ => continue,
        }
    };
    let ttfb = started.elapsed();
    assert!(
        String::from_utf8_lossy(&first).contains("tiny-q4"),
        "model rewritten"
    );
    assert!(
        ttfb < Duration::from_millis(250),
        "first chunk streamed unbuffered ({ttfb:?})"
    );
    assert_eq!(agent.proxy.counts(&id).0, 1, "in flight while streaming");
    let mut rest = Vec::new();
    while let Some(f) = body.frame().await {
        if let Ok(d) = f.unwrap().into_data() {
            rest.extend_from_slice(&d);
        }
    }
    assert!(String::from_utf8_lossy(&rest).contains("[DONE]"));
    tokio::time::sleep(Duration::from_millis(100)).await;
    assert_eq!(agent.proxy.counts(&id).0, 0, "in-flight entry closed");

    // Drain over the tunnel: 503 + Retry-After.
    let from = received(&hub);
    hub.send(
        Some(node),
        push(3, Some(vec![route(id, port, false)]), None),
    );
    config_event(&hub, from, 3, EventKind::ConfigApplied).await;
    let req = http::Request::post(format!("https://tunnel/instances/{id}/v1/chat/completions"))
        .body(Full::new(Bytes::from_static(b"{}")))
        .unwrap();
    let resp = send.send_request(req).await.unwrap();
    assert_eq!(resp.status(), 503);
    assert_eq!(resp.headers()["retry-after"], "2");

    // A policy signed by another CA is refused (whole push), nothing changes.
    let held = agent.policy().unwrap();
    let forged_ca = gpudeck_agent::testpki::TestCa::new("attacker CA");
    let mut evil = held.clone();
    evil.serial += 1;
    evil.tunnel = Some(TunnelPolicy {
        endpoints: vec!["203.0.113.66:8444".into()],
        ..Default::default()
    });
    let from = received(&hub);
    hub.send(
        Some(node),
        push(4, None, Some(forged_ca.sign_policy(&evil))),
    );
    let ev = config_event(&hub, from, 4, EventKind::ConfigRejected).await;
    assert!(format!("{:?}", ev.details).contains("security policy"));
    // Rollback: a correctly signed but older policy is refused too.
    let mut old = held.clone();
    old.serial -= 1;
    let from = received(&hub);
    hub.send(
        Some(node),
        push(5, None, Some(hub.state.sign_policy_for(node, old))),
    );
    config_event(&hub, from, 5, EventKind::ConfigRejected).await;
    assert_eq!(agent.policy().unwrap(), held, "policy unchanged");
    // A policy for another node is refused.
    let mut other = held.clone();
    other.serial += 10;
    let foreign = hub.state.sign_policy_for(Uuid::new_v4(), other);
    let from = received(&hub);
    hub.send(Some(node), push(6, None, Some(foreign)));
    config_event(&hub, from, 6, EventKind::ConfigRejected).await;
    agent.shutdown.cancel();
}

/// Connect to the direct-mode proxy with `client` (or none); `Ok(status)` when an HTTP response
/// came back, `Err(..)` when the connection failed before any HTTP byte.
async fn try_direct(addr: SocketAddr, cfg: rustls::ClientConfig) -> Result<u16, String> {
    let tcp = tokio::net::TcpStream::connect(addr)
        .await
        .map_err(|e| e.to_string())?;
    let name = rustls::pki_types::ServerName::try_from("node").unwrap();
    let mut tls = tokio_rustls::TlsConnector::from(Arc::new(cfg))
        .connect(name, tcp)
        .await
        .map_err(|e| format!("handshake: {e}"))?;
    tls.write_all(b"GET /_health HTTP/1.1\r\nhost: node\r\nconnection: close\r\n\r\n")
        .await
        .map_err(|e| format!("write: {e}"))?;
    let mut buf = Vec::new();
    match tokio::time::timeout(Duration::from_secs(5), tls.read_to_end(&mut buf)).await {
        Ok(Ok(_)) if buf.starts_with(b"HTTP/1.1 ") => {
            Ok(String::from_utf8_lossy(&buf[9..12]).parse().unwrap_or(0))
        }
        Ok(Ok(_)) => Err(format!("no HTTP response ({} bytes)", buf.len())),
        Ok(Err(e)) => Err(format!("read: {e}")),
        Err(_) => Err("timeout".into()),
    }
}

fn client(ca_pem: &str, node: Uuid, ident: Option<(&str, &str)>) -> rustls::ClientConfig {
    match ident {
        Some((c, k)) => {
            gpudeck_agent::tls::node_proxy_client_config(ca_pem, c, k, &node.to_string()).unwrap()
        }
        // Accepts the node's certificate, offers none of its own.
        None => gpudeck_agent::tls::join_client_config(None).unwrap(),
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn direct_mode_rejects_everyone_but_the_pinned_api_layer() {
    gpudeck_agent::tls::init_crypto();
    let hub = FakeHub::start("127.0.0.1:0".parse().unwrap(), "gdj_testtoken0123456789")
        .await
        .unwrap();
    let api = hub.issue_client("api-layer", "gpudeck-services");
    let template = |sources: &[&str], per_minute: u32, burst: u32| SecurityPolicy {
        api_layer: Some(IdentityPins {
            spki_sha256: vec![spki(&api)],
            subjects: vec![],
        }),
        direct: Some(DirectPolicy {
            allowed_source_cidrs: sources.iter().map(|s| s.to_string()).collect(),
            handshakes_per_minute: Some(per_minute),
            handshake_burst: Some(burst),
            max_pending_handshakes: Some(16),
        }),
        ingress_report: Some(IngressReportPolicy {
            interval_seconds: Some(10),
            burst_threshold: Some(3),
        }),
        ..Default::default()
    };
    *hub.state.policy_template.lock().unwrap() = Some(template(&["10.255.0.0/16"], 600, 50));
    let dir = tempfile::tempdir().unwrap();
    let (agent, node) = joined_agent(
        &hub,
        dir.path(),
        RunOptions {
            proxy_listen: Some("127.0.0.1:0".parse().unwrap()),
            ..Default::default()
        },
    )
    .await;
    let addr = loop {
        if let Some(a) = *agent.proxy_addr.lock().unwrap() {
            break a;
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    };
    let hello = hub
        .wait_for(0, T, |e| matches!(e.message, Message::Hello(_)))
        .await
        .unwrap();
    let Message::Hello(h) = hello.message else {
        unreachable!()
    };
    assert_eq!(h.proxy.mode, Some(ServingMode::Direct));
    assert_eq!(h.proxy.bind.as_deref(), Some("127.0.0.1"));
    let ca = hub.ca_pem();
    let good = || {
        client(
            &ca,
            node,
            Some((&api.cert_pem, api.key_pem.as_deref().unwrap())),
        )
    };

    // 1. Source not allowed (policy lists 10.255/16 only): reset before TLS.
    let r = try_direct(addr, good()).await;
    assert!(
        r.is_err(),
        "non-allowlisted source must not get through: {r:?}"
    );

    // 2. Allow loopback; only the pinned API-layer key gets an HTTP answer.
    let from = received(&hub);
    hub.send(
        Some(node),
        push(
            2,
            None,
            Some(
                hub.state
                    .sign_policy_for(node, template(&["127.0.0.1/32"], 600, 50)),
            ),
        ),
    );
    config_event(&hub, from, 2, EventKind::ConfigApplied).await;
    assert_eq!(try_direct(addr, good()).await, Ok(200));
    let other_ca = gpudeck_agent::testpki::TestCa::new("other CA");
    let foreign = other_ca.issue_client("api-layer", "gpudeck-services");
    let unpinned = hub.issue_client("api-layer", "gpudeck-services");
    let node_cert = hub.issue_client(&Uuid::new_v4().to_string(), "gpudeck-nodes");
    let selfsigned = rcgen::generate_simple_self_signed(vec!["api-layer".to_string()]).unwrap();
    let ss_cert = selfsigned.cert.pem();
    let ss_key = selfsigned.signing_key.serialize_pem();
    for (what, cfg) in [
        ("no client certificate", client(&ca, node, None)),
        (
            "certificate from another CA",
            client(
                &ca,
                node,
                Some((&foreign.cert_pem, foreign.key_pem.as_deref().unwrap())),
            ),
        ),
        (
            "self-signed certificate",
            client(&ca, node, Some((&ss_cert, &ss_key))),
        ),
        (
            "valid services cert, unpinned key",
            client(
                &ca,
                node,
                Some((&unpinned.cert_pem, unpinned.key_pem.as_deref().unwrap())),
            ),
        ),
        (
            "node certificate",
            client(
                &ca,
                node,
                Some((&node_cert.cert_pem, node_cert.key_pem.as_deref().unwrap())),
            ),
        ),
    ] {
        let r = try_direct(addr, cfg).await;
        assert!(
            r.is_err(),
            "{what}: must fail in the TLS handshake, got {r:?}"
        );
    }

    // 3. Handshake rate limit (1/min, burst 1): the second quick connection is reset.
    let from = received(&hub);
    hub.send(
        Some(node),
        push(
            3,
            None,
            Some(
                hub.state
                    .sign_policy_for(node, template(&["127.0.0.1/32"], 1, 1)),
            ),
        ),
    );
    config_event(&hub, from, 3, EventKind::ConfigApplied).await;
    assert_eq!(try_direct(addr, good()).await, Ok(200));
    assert!(
        try_direct(addr, good()).await.is_err(),
        "rate limited before TLS"
    );

    // 4. The rejected attempts reach the hub as ingress_rejected events (one per interval;
    //    critical at the burst threshold) with reasons and sources.
    let want = [
        "source_not_allowed",
        "client_identity_rejected",
        "untrusted_client_certificate",
        "no_client_certificate",
        "rate_limited",
    ];
    let deadline = Instant::now() + Duration::from_secs(40);
    let (mut seen, mut total, mut critical) = (std::collections::BTreeSet::new(), 0u64, false);
    while !want.iter().all(|w| seen.contains(*w)) {
        assert!(
            Instant::now() < deadline,
            "ingress_rejected reasons seen: {seen:?}"
        );
        tokio::time::sleep(Duration::from_millis(500)).await;
        (seen, total, critical) = (Default::default(), 0, false);
        for (env, _) in hub.state.received.lock().unwrap().iter() {
            if let Message::Event(ev) = &env.message {
                if ev.kind == EventKind::IngressRejected {
                    let d = ev.details.as_ref().unwrap();
                    seen.extend(d["reasons"].as_object().unwrap().keys().cloned());
                    total += d["total"].as_u64().unwrap();
                    critical |= ev.severity == Severity::Critical;
                    assert_eq!(d["sources"][0]["ip"], "127.0.0.1");
                }
            }
        }
    }
    assert!(critical, "a burst is reported as critical");
    assert!(total >= 7, "total {total}");
    agent.shutdown.cancel();
}

// --------------------------------------------------------------------------------- latency

/// TCP relay to `target` that delays every chunk by `one_way` in each direction (RTT =
/// 2 × one_way), preserving order and pipelining.
async fn delay_proxy(target: SocketAddr, one_way: Duration) -> SocketAddr {
    let l = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = l.local_addr().unwrap();
    tokio::spawn(async move {
        while let Ok((a, _)) = l.accept().await {
            let Ok(b) = tokio::net::TcpStream::connect(target).await else {
                continue;
            };
            let _ = a.set_nodelay(true);
            let _ = b.set_nodelay(true);
            let (ar, aw) = a.into_split();
            let (br, bw) = b.into_split();
            tokio::spawn(delayed_pipe(ar, bw, one_way));
            tokio::spawn(delayed_pipe(br, aw, one_way));
        }
    });
    addr
}

async fn delayed_pipe(
    mut r: tokio::net::tcp::OwnedReadHalf,
    mut w: tokio::net::tcp::OwnedWriteHalf,
    d: Duration,
) {
    let (tx, mut rx) = mpsc::unbounded_channel::<(tokio::time::Instant, Option<Vec<u8>>)>();
    tokio::spawn(async move {
        let mut buf = vec![0u8; 64 * 1024];
        loop {
            let n = r.read(&mut buf).await.unwrap_or(0);
            let at = tokio::time::Instant::now() + d;
            if n == 0 {
                let _ = tx.send((at, None));
                return;
            }
            if tx.send((at, Some(buf[..n].to_vec()))).is_err() {
                return;
            }
        }
    });
    while let Some((at, chunk)) = rx.recv().await {
        tokio::time::sleep_until(at).await;
        match chunk {
            Some(c) => {
                if w.write_all(&c).await.is_err() {
                    return;
                }
            }
            None => {
                let _ = w.shutdown().await;
                return;
            }
        }
    }
}

/// One streamed request: (time to first body byte, time to end of body).
async fn timed(
    send: &mut hyper::client::conn::http2::SendRequest<Full<Bytes>>,
    id: Uuid,
) -> (Duration, Duration) {
    send.ready().await.unwrap();
    let req = http::Request::post(format!("https://node/instances/{id}/v1/chat/completions"))
        .header("content-type", "application/json")
        .body(Full::new(Bytes::from_static(
            br#"{"model":"tiny","stream":true}"#,
        )))
        .unwrap();
    let t0 = Instant::now();
    let resp = send.send_request(req).await.unwrap();
    assert_eq!(resp.status(), 200);
    let mut body = resp.into_body();
    let mut ttfb = None;
    while let Some(f) = body.frame().await {
        if let Ok(d) = f.unwrap().into_data() {
            if !d.is_empty() && ttfb.is_none() {
                ttfb = Some(t0.elapsed());
            }
        }
    }
    (ttfb.unwrap(), t0.elapsed())
}

/// HTTP/2 over mTLS to a direct-mode proxy (what the API layer does in direct mode).
async fn direct_conn(
    addr: SocketAddr,
    cfg: &rustls::ClientConfig,
) -> hyper::client::conn::http2::SendRequest<Full<Bytes>> {
    let mut cfg = cfg.clone();
    cfg.alpn_protocols = vec![b"h2".to_vec()];
    let tcp = tokio::net::TcpStream::connect(addr).await.unwrap();
    let _ = tcp.set_nodelay(true);
    let tls = tokio_rustls::TlsConnector::from(Arc::new(cfg))
        .connect(
            rustls::pki_types::ServerName::try_from("node").unwrap(),
            tcp,
        )
        .await
        .unwrap();
    let (send, conn) =
        hyper::client::conn::http2::handshake(TokioExecutor::new(), TokioIo::new(tls))
            .await
            .unwrap();
    tokio::spawn(conn);
    send
}

fn pct(v: &mut [Duration], p: f64) -> f64 {
    v.sort();
    let i = ((v.len() as f64 - 1.0) * p).round() as usize;
    v[i].as_secs_f64() * 1000.0
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn tunnel_vs_direct_latency_with_simulated_rtt() {
    gpudeck_agent::tls::init_crypto();
    const ONE_WAY: Duration = Duration::from_millis(10); // 20 ms RTT
    const N: usize = 60;
    // The fake hub holds one agent connection at a time: one hub (and CA) per agent.
    let thub = FakeHub::start("127.0.0.1:0".parse().unwrap(), "gdj_testtoken0123456789")
        .await
        .unwrap();
    let dhub = FakeHub::start("127.0.0.1:0".parse().unwrap(), "gdj_testtoken0123456789")
        .await
        .unwrap();
    let tapi = thub.issue_client("api-layer", "gpudeck-services");
    let dapi = dhub.issue_client("api-layer", "gpudeck-services");
    let mut tunnel_api = tunnel_server(&thub.ca_pem(), &tapi).await;
    // The agent dials the tunnel endpoint through the delaying relay.
    let tunnel_via = delay_proxy(tunnel_api.addr, ONE_WAY).await;
    *thub.state.policy_template.lock().unwrap() = Some(SecurityPolicy {
        api_layer: Some(IdentityPins {
            spki_sha256: vec![spki(&tapi)],
            subjects: vec![],
        }),
        tunnel: Some(TunnelPolicy {
            endpoints: vec![tunnel_via.to_string()],
            connections: Some(1),
            idle_timeout_seconds: Some(60),
        }),
        ..Default::default()
    });
    *dhub.state.policy_template.lock().unwrap() = Some(SecurityPolicy {
        api_layer: Some(IdentityPins {
            spki_sha256: vec![spki(&dapi)],
            subjects: vec![],
        }),
        ..Default::default()
    });
    let d1 = tempfile::tempdir().unwrap();
    let d2 = tempfile::tempdir().unwrap();
    let (tunnel_agent, tnode) = joined_agent(&thub, d1.path(), RunOptions::default()).await;
    let (direct_agent, dnode) = joined_agent(
        &dhub,
        d2.path(),
        RunOptions {
            proxy_listen: Some("127.0.0.1:0".parse().unwrap()),
            ..Default::default()
        },
    )
    .await;
    let port = engine(Duration::from_millis(1)).await;
    let id = Uuid::new_v4();
    for (hub, node) in [(&thub, tnode), (&dhub, dnode)] {
        hub.wait_for(0, T, |e| matches!(e.message, Message::Inventory(_)))
            .await
            .expect("agent connected");
        let from = received(hub);
        hub.send(Some(node), push(2, Some(vec![route(id, port, true)]), None));
        config_event(hub, from, 2, EventKind::ConfigApplied).await;
    }
    let (mut tsend, _) = tokio::time::timeout(T, tunnel_api.conns.recv())
        .await
        .unwrap()
        .unwrap();
    let paddr = loop {
        if let Some(a) = *direct_agent.proxy_addr.lock().unwrap() {
            break a;
        }
        tokio::time::sleep(Duration::from_millis(20)).await;
    };
    let direct_via = delay_proxy(paddr, ONE_WAY).await;
    let ccfg = client(
        &dhub.ca_pem(),
        dnode,
        Some((&dapi.cert_pem, dapi.key_pem.as_deref().unwrap())),
    );

    // Cold first request: direct pays TCP + TLS before the request; the tunnel is already open.
    let t0 = Instant::now();
    let mut dsend = direct_conn(direct_via, &ccfg).await;
    let (_, dcold_total) = timed(&mut dsend, id).await;
    let dcold = t0.elapsed().max(dcold_total);
    let (_, tcold) = timed(&mut tsend, id).await;

    for _ in 0..10 {
        timed(&mut tsend, id).await;
        timed(&mut dsend, id).await;
    }
    let (mut t_ttfb, mut t_total, mut d_ttfb, mut d_total) = (vec![], vec![], vec![], vec![]);
    for _ in 0..N {
        let (a, b) = timed(&mut tsend, id).await;
        t_ttfb.push(a);
        t_total.push(b);
        let (a, b) = timed(&mut dsend, id).await;
        d_ttfb.push(a);
        d_total.push(b);
    }
    let rtt = (ONE_WAY * 2).as_secs_f64() * 1000.0;
    let (tt50, tt95, dt50, dt95) = (
        pct(&mut t_ttfb, 0.5),
        pct(&mut t_ttfb, 0.95),
        pct(&mut d_ttfb, 0.5),
        pct(&mut d_ttfb, 0.95),
    );
    let (te50, de50) = (pct(&mut t_total, 0.5), pct(&mut d_total, 0.5));
    println!("simulated RTT {rtt:.0} ms, {N} warm streamed requests each (interleaved)");
    println!("mode     ttfb p50  ttfb p95  total p50  cold first request");
    println!(
        "direct   {dt50:7.2}   {dt95:7.2}   {de50:8.2}   {:8.2} ms",
        dcold.as_secs_f64() * 1000.0
    );
    println!(
        "tunnel   {tt50:7.2}   {tt95:7.2}   {te50:8.2}   {:8.2} ms",
        tcold.as_secs_f64() * 1000.0
    );
    println!(
        "tunnel - direct: ttfb p50 {:+.2} ms, total p50 {:+.2} ms",
        tt50 - dt50,
        te50 - de50
    );
    // Both modes cost one round trip per request on a warm connection.
    assert!(
        tt50 >= rtt && dt50 >= rtt,
        "the relay adds the simulated RTT"
    );
    assert!(
        tt50 - dt50 < 3.0,
        "tunnel adds < 3 ms at p50 (got {:+.2} ms)",
        tt50 - dt50
    );
    assert!(
        tcold < dcold,
        "the tunnel is pre-connected: no handshake on the first request"
    );
    assert!(
        tunnel_agent.tunnels.connected.load(Ordering::Relaxed) == 1,
        "one tunnel connection"
    );
    tunnel_agent.shutdown.cancel();
    direct_agent.shutdown.cancel();
}
