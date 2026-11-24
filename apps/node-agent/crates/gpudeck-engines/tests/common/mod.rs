//! Test helpers: fixture access and a tiny scripted HTTP/1.1 server standing in for an engine.
#![allow(dead_code)]

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;

pub fn fixture_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../fixtures/engines")
}

pub fn read(rel: &str) -> String {
    std::fs::read_to_string(fixture_dir().join(rel)).unwrap_or_else(|e| panic!("{rel}: {e}"))
}

pub fn json(rel: &str) -> serde_json::Value {
    serde_json::from_str(&read(rel)).unwrap_or_else(|e| panic!("{rel}: {e}"))
}

type Scripted = Arc<Mutex<HashMap<String, Vec<(u16, String)>>>>;

/// Responses per `"METHOD /path?query"`; a route with several entries serves them in order and
/// then repeats the last one.
#[derive(Clone, Default)]
pub struct Routes {
    inner: Scripted,
    pub log: Arc<Mutex<Vec<(String, String)>>>,
}

impl Routes {
    pub fn on(&self, key: &str, status: u16, body: impl Into<String>) -> &Self {
        self.inner
            .lock()
            .unwrap()
            .entry(key.to_string())
            .or_default()
            .push((status, body.into()));
        self
    }

    fn take(&self, key: &str) -> (u16, String) {
        let mut m = self.inner.lock().unwrap();
        match m.get_mut(key) {
            Some(v) if v.len() > 1 => v.remove(0),
            Some(v) if !v.is_empty() => v[0].clone(),
            _ => (404, "{\"error\":\"not found\"}".into()),
        }
    }

    pub fn requests(&self) -> Vec<(String, String)> {
        self.log.lock().unwrap().clone()
    }
}

/// Start the fake engine; returns its base URL.
pub async fn serve(routes: Routes) -> String {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        loop {
            let Ok((mut sock, _)) = listener.accept().await else {
                break;
            };
            let routes = routes.clone();
            tokio::spawn(async move {
                let mut buf = Vec::new();
                let mut tmp = [0u8; 4096];
                let header_end;
                loop {
                    let n = match sock.read(&mut tmp).await {
                        Ok(0) | Err(_) => return,
                        Ok(n) => n,
                    };
                    buf.extend_from_slice(&tmp[..n]);
                    if let Some(i) = find(&buf, b"\r\n\r\n") {
                        header_end = i + 4;
                        break;
                    }
                }
                let head = String::from_utf8_lossy(&buf[..header_end]).to_string();
                let len = head
                    .lines()
                    .find_map(|l| {
                        l.to_ascii_lowercase()
                            .strip_prefix("content-length:")
                            .map(|v| v.trim().parse::<usize>().unwrap_or(0))
                    })
                    .unwrap_or(0);
                while buf.len() < header_end + len {
                    let n = match sock.read(&mut tmp).await {
                        Ok(0) | Err(_) => break,
                        Ok(n) => n,
                    };
                    buf.extend_from_slice(&tmp[..n]);
                }
                let body = String::from_utf8_lossy(&buf[header_end..]).to_string();
                let first = head.lines().next().unwrap_or_default();
                let mut parts = first.split_whitespace();
                let key = format!(
                    "{} {}",
                    parts.next().unwrap_or(""),
                    parts.next().unwrap_or("")
                );
                routes.log.lock().unwrap().push((key.clone(), body));
                let (status, resp) = routes.take(&key);
                let out = format!(
                    "HTTP/1.1 {status} X\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{resp}",
                    resp.len()
                );
                let _ = sock.write_all(out.as_bytes()).await;
                let _ = sock.shutdown().await;
            });
        }
    });
    format!("http://{addr}")
}

fn find(h: &[u8], n: &[u8]) -> Option<usize> {
    h.windows(n.len()).position(|w| w == n)
}
