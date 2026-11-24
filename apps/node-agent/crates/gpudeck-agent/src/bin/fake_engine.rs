//! `gpudeck-fake-engine`: a tiny OpenAI-compatible engine used by the agent's tests.
//!
//! `--port P [--host H] [--models-preset INI]`. Serves `/health`, `/v1/models`,
//! `/v1/chat/completions` (JSON or SSE with a final `usage` chunk; a body field `sleep_ms` holds
//! the request) and, with `--models-preset`, the llama.cpp router admin API (`/models`,
//! `/models/load`, `/models/unload`; the model list is read once at start, like llama-server).
//! `GET /fake/stats` counts the chat / embedding requests served (warm-up tests).

use std::collections::BTreeMap;
use std::convert::Infallible;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use bytes::Bytes;
use http::{Request, Response, StatusCode};
use http_body_util::{BodyExt, Full, StreamBody};
use hyper::body::Frame;
use hyper_util::rt::TokioIo;

type Body = http_body_util::combinators::BoxBody<Bytes, Infallible>;

fn json(status: StatusCode, v: serde_json::Value) -> Response<Body> {
    let mut r = Response::new(Full::new(Bytes::from(v.to_string())).boxed());
    *r.status_mut() = status;
    r.headers_mut()
        .insert("content-type", "application/json".parse().expect("hv"));
    r
}

struct State {
    router: bool,
    models: Mutex<BTreeMap<String, String>>,
    chat: std::sync::atomic::AtomicU64,
    embeddings: std::sync::atomic::AtomicU64,
}

fn arg(name: &str) -> Option<String> {
    let args: Vec<String> = std::env::args().collect();
    args.iter()
        .position(|a| a == name)
        .and_then(|i| args.get(i + 1).cloned())
        .or_else(|| {
            args.iter()
                .find_map(|a| a.strip_prefix(&format!("{name}=")).map(str::to_owned))
        })
}

#[tokio::main]
async fn main() {
    let port: u16 = arg("--port").and_then(|p| p.parse().ok()).expect("--port");
    let host = arg("--host").unwrap_or_else(|| "127.0.0.1".into());
    let preset = arg("--models-preset");
    let mut models = BTreeMap::new();
    if let Some(p) = &preset {
        for line in std::fs::read_to_string(p).unwrap_or_default().lines() {
            if let Some(name) = line
                .trim()
                .strip_prefix('[')
                .and_then(|l| l.strip_suffix(']'))
            {
                models.insert(name.to_string(), "unloaded".to_string());
            }
        }
    }
    println!(
        "fake engine listening on {host}:{port} (router: {}, models: {:?})",
        preset.is_some(),
        models.keys().collect::<Vec<_>>()
    );
    eprintln!("warning: this is a fake engine");
    let state = Arc::new(State {
        router: preset.is_some(),
        models: Mutex::new(models),
        chat: Default::default(),
        embeddings: Default::default(),
    });
    let listener = tokio::net::TcpListener::bind((host.as_str(), port))
        .await
        .expect("bind");
    loop {
        let Ok((tcp, _)) = listener.accept().await else {
            continue;
        };
        let st = state.clone();
        tokio::spawn(async move {
            let svc = hyper::service::service_fn(move |req| {
                let st = st.clone();
                async move { Ok::<_, Infallible>(handle(st, req).await) }
            });
            let _ = hyper::server::conn::http1::Builder::new()
                .serve_connection(TokioIo::new(tcp), svc)
                .await;
        });
    }
}

async fn handle(st: Arc<State>, req: Request<hyper::body::Incoming>) -> Response<Body> {
    let path = req.uri().path().to_string();
    let method = req.method().clone();
    let body = req
        .into_body()
        .collect()
        .await
        .map(|b| b.to_bytes())
        .unwrap_or_default();
    let v: serde_json::Value = serde_json::from_slice(&body).unwrap_or(serde_json::Value::Null);
    match (method.as_str(), path.as_str()) {
        ("GET", "/health") => json(StatusCode::OK, serde_json::json!({"status": "ok"})),
        ("GET", "/props") => json(StatusCode::OK, serde_json::json!({"total_slots": 1})),
        ("GET", "/fake/stats") => {
            use std::sync::atomic::Ordering::Relaxed;
            json(
                StatusCode::OK,
                serde_json::json!({"chat": st.chat.load(Relaxed), "embeddings": st.embeddings.load(Relaxed), "pid": std::process::id()}),
            )
        }
        ("POST", "/v1/embeddings") => {
            st.embeddings
                .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            json(
                StatusCode::OK,
                serde_json::json!({"object": "list", "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}], "usage": {"prompt_tokens": 3, "total_tokens": 3}}),
            )
        }
        ("GET", "/models") | ("GET", "/v1/models") => {
            let models = st.models.lock().expect("lock");
            let data: Vec<_> = models.iter().map(|(id, s)| serde_json::json!({"id": id, "object": "model", "status": {"value": s}})).collect();
            json(
                StatusCode::OK,
                serde_json::json!({"data": data, "object": "list"}),
            )
        }
        ("POST", "/models/load") | ("POST", "/models/unload") => {
            let name = v["model"].as_str().unwrap_or_default().to_string();
            let mut models = st.models.lock().expect("lock");
            match models.get_mut(&name) {
                None => json(
                    StatusCode::NOT_FOUND,
                    serde_json::json!({"error": {"message": "model is not found"}}),
                ),
                Some(s) => {
                    *s = if path.ends_with("load") {
                        "loaded".into()
                    } else {
                        "unloaded".into()
                    };
                    json(StatusCode::OK, serde_json::json!({"success": true}))
                }
            }
        }
        ("GET", "/metrics") => {
            let mut r = Response::new(Full::new(Bytes::from("fake_requests 1\n")).boxed());
            r.headers_mut()
                .insert("content-type", "text/plain".parse().expect("hv"));
            r
        }
        ("POST", "/v1/chat/completions") => {
            let model = v["model"].as_str().unwrap_or("default").to_string();
            if st.router
                && st
                    .models
                    .lock()
                    .expect("lock")
                    .get(&model)
                    .map(String::as_str)
                    != Some("loaded")
            {
                return json(
                    StatusCode::BAD_REQUEST,
                    serde_json::json!({"error": {"message": format!("model {model} not loaded")}}),
                );
            }
            st.chat.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            if let Some(ms) = v["sleep_ms"].as_u64() {
                tokio::time::sleep(Duration::from_millis(ms)).await;
            }
            let usage = serde_json::json!({"prompt_tokens": 7, "completion_tokens": 5, "total_tokens": 12, "prompt_tokens_details": {"cached_tokens": 2}});
            if v["stream"].as_bool() == Some(true) {
                let (tx, rx) = tokio::sync::mpsc::channel::<Result<Frame<Bytes>, Infallible>>(16);
                tokio::spawn(async move {
                    let role = serde_json::json!({"model": model, "choices": [{"index": 0, "delta": {"role": "assistant", "content": null}}]});
                    let _ = tx
                        .send(Ok(Frame::data(Bytes::from(format!("data: {role}\n\n")))))
                        .await;
                    for w in ["hello", " from", " the", " fake", " engine"] {
                        tokio::time::sleep(Duration::from_millis(30)).await;
                        let c = serde_json::json!({"model": model, "choices": [{"index": 0, "delta": {"content": w}}]});
                        let _ = tx
                            .send(Ok(Frame::data(Bytes::from(format!("data: {c}\n\n")))))
                            .await;
                    }
                    let u = serde_json::json!({"model": model, "choices": [], "usage": usage});
                    let _ = tx
                        .send(Ok(Frame::data(Bytes::from(format!(
                            "data: {u}\n\ndata: [DONE]\n\n"
                        )))))
                        .await;
                });
                let mut r = Response::new(StreamBody::new(tokio_stream_from(rx)).boxed());
                r.headers_mut()
                    .insert("content-type", "text/event-stream".parse().expect("hv"));
                return r;
            }
            json(
                StatusCode::OK,
                serde_json::json!({"id": "chatcmpl-fake", "object": "chat.completion", "model": model, "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello from the fake engine"}, "finish_reason": "stop"}], "usage": usage, "timings": {"cache_n": 2, "prompt_n": 5, "predicted_n": 5}}),
            )
        }
        _ => json(
            StatusCode::NOT_FOUND,
            serde_json::json!({"error": "not found"}),
        ),
    }
}

fn tokio_stream_from(
    mut rx: tokio::sync::mpsc::Receiver<Result<Frame<Bytes>, Infallible>>,
) -> impl futures::Stream<Item = Result<Frame<Bytes>, Infallible>> {
    futures::stream::poll_fn(move |cx| rx.poll_recv(cx))
}
