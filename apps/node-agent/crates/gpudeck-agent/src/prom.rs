//! Loopback-only Prometheus endpoint (`http://127.0.0.1:<prom_port>/metrics`, `protocol.md` §7):
//! agent self metrics plus the last scraped engine / GPU values.

use std::convert::Infallible;
use std::sync::Arc;

use http::{header, HeaderValue, Response, StatusCode};
use hyper_util::rt::TokioIo;

use crate::agent::Agent;
use crate::proxy::full;

pub async fn serve(agent: Arc<Agent>) {
    let addr = std::net::SocketAddr::from(([127, 0, 0, 1], agent.cfg.prom_port));
    let listener = match tokio::net::TcpListener::bind(addr).await {
        Ok(l) => l,
        Err(e) => {
            tracing::warn!("prometheus endpoint {addr} not started: {e}");
            return;
        }
    };
    tracing::info!("prometheus endpoint on http://{addr}/metrics");
    loop {
        let (tcp, _) = tokio::select! {
            r = listener.accept() => match r { Ok(x) => x, Err(_) => continue },
            _ = agent.shutdown.cancelled() => return,
        };
        let a = agent.clone();
        tokio::spawn(async move {
            let svc =
                hyper::service::service_fn(move |req: http::Request<hyper::body::Incoming>| {
                    let a = a.clone();
                    async move {
                        let mut r = if req.uri().path() == "/metrics" {
                            let mut r = Response::new(full(
                                a.prom_text
                                    .lock()
                                    .unwrap_or_else(|e| e.into_inner())
                                    .clone(),
                            ));
                            r.headers_mut().insert(
                                header::CONTENT_TYPE,
                                HeaderValue::from_static("text/plain; version=0.0.4"),
                            );
                            r
                        } else {
                            Response::new(full("not found"))
                        };
                        if req.uri().path() != "/metrics" {
                            *r.status_mut() = StatusCode::NOT_FOUND;
                        }
                        Ok::<_, Infallible>(r)
                    }
                });
            let _ = hyper::server::conn::http1::Builder::new()
                .serve_connection(TokioIo::new(tcp), svc)
                .await;
        });
    }
}
