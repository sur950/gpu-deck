//! Streaming response body that forwards every frame as soon as it arrives (no buffering) while
//! measuring TTFT, counting SSE content chunks and parsing `usage` / `timings` from the final
//! JSON or SSE frames. Enforces the idle-stream and total timeouts. The request is recorded
//! exactly once: at end of stream, on upstream error, or when the client goes away (drop).

use std::future::Future;
use std::pin::Pin;
use std::task::{Context, Poll};
use std::time::{Duration, Instant};

use bytes::Bytes;
use http_body::{Body, Frame};
use hyper::body::Incoming;
use tokio::sync::OwnedSemaphorePermit;

use super::stats::RequestRecord;
use super::{BoxError, StatsSink};

const MAX_CAPTURE: usize = 4 * 1024 * 1024;

pub struct Observed {
    inner: Incoming,
    sink: StatsSink,
    rec: RequestRecord,
    started: Instant,
    sse: bool,
    idle: Duration,
    deadline: tokio::time::Instant,
    timer: Pin<Box<tokio::time::Sleep>>,
    line: Vec<u8>,
    capture: Vec<u8>,
    chunks: u64,
    first: bool,
    done: bool,
    _permit: Option<OwnedSemaphorePermit>,
}

impl Observed {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        inner: Incoming,
        sink: StatsSink,
        rec: RequestRecord,
        started: Instant,
        sse: bool,
        idle: Duration,
        deadline: Instant,
        permit: Option<OwnedSemaphorePermit>,
    ) -> Self {
        let deadline = tokio::time::Instant::from_std(deadline);
        let first_wake = (tokio::time::Instant::now() + idle).min(deadline);
        Self {
            inner,
            sink,
            rec,
            started,
            sse,
            idle,
            deadline,
            timer: Box::pin(tokio::time::sleep_until(first_wake)),
            line: vec![],
            capture: vec![],
            chunks: 0,
            first: false,
            done: false,
            _permit: permit,
        }
    }

    pub fn boxed(self) -> super::ProxyBody {
        http_body_util::BodyExt::boxed(self)
    }

    fn mark_first(&mut self) {
        if !self.first {
            self.first = true;
            self.rec.ttft_ms = Some(self.started.elapsed().as_secs_f64() * 1000.0);
            self.sink.first_byte();
        }
    }

    fn observe(&mut self, data: &Bytes) {
        self.rec.bytes_out += data.len() as u64;
        if !self.sse {
            self.mark_first();
            if self.capture.len() + data.len() <= MAX_CAPTURE {
                self.capture.extend_from_slice(data);
            }
            return;
        }
        self.line.extend_from_slice(data);
        while let Some(pos) = self.line.iter().position(|b| *b == b'\n') {
            let raw: Vec<u8> = self.line.drain(..=pos).collect();
            self.sse_line(&raw);
        }
        if self.line.len() > MAX_CAPTURE {
            self.line.clear();
        }
    }

    fn sse_line(&mut self, raw: &[u8]) {
        let text = String::from_utf8_lossy(raw);
        let Some(payload) = text.trim_end().strip_prefix("data:") else {
            return;
        };
        let payload = payload.trim();
        if payload.is_empty() || payload == "[DONE]" {
            return;
        }
        let Ok(v) = serde_json::from_str::<serde_json::Value>(payload) else {
            return;
        };
        if has_content(&v) {
            self.mark_first();
            self.chunks += 1;
            self.sink.chunk();
        }
        self.rec.absorb(&v);
    }

    fn finish(&mut self, transport_error: bool, client_gone: bool) {
        if self.done {
            return;
        }
        self.done = true;
        if !self.line.is_empty() {
            let rest = std::mem::take(&mut self.line);
            self.sse_line(&rest);
        }
        if !self.sse && !self.capture.is_empty() {
            if let Ok(v) = serde_json::from_slice::<serde_json::Value>(&self.capture) {
                self.rec.absorb(&v);
            }
        }
        if self.sse && self.rec.completion_tokens.is_none() && self.chunks > 0 {
            self.rec.completion_tokens = Some(self.chunks);
        }
        if transport_error {
            self.rec.transport_error = true;
        }
        if client_gone && self.rec.status < 400 {
            // Client disconnected mid-stream: counted, not an engine error.
            self.rec.status = 499;
        }
        self.rec.e2e_ms = self.started.elapsed().as_secs_f64() * 1000.0;
        self.sink.finish(std::mem::take(&mut self.rec));
    }
}

/// True when an SSE event carries generated content (OpenAI chat/completions/responses, Anthropic).
pub fn has_content(v: &serde_json::Value) -> bool {
    let nonempty = |p: &str| {
        v.pointer(p)
            .and_then(|x| x.as_str())
            .is_some_and(|s| !s.is_empty())
    };
    nonempty("/choices/0/delta/content")
        || nonempty("/choices/0/delta/reasoning_content")
        || nonempty("/choices/0/text")
        || v.pointer("/choices/0/delta/tool_calls")
            .is_some_and(|t| t.as_array().is_some_and(|a| !a.is_empty()))
        || v.get("type")
            .and_then(|t| t.as_str())
            .is_some_and(|t| t == "content_block_delta" || t == "response.output_text.delta")
        || nonempty("/content")
}

impl Body for Observed {
    type Data = Bytes;
    type Error = BoxError;

    fn poll_frame(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
    ) -> Poll<Option<Result<Frame<Bytes>, BoxError>>> {
        let this = &mut *self;
        if this.done {
            return Poll::Ready(None);
        }
        match Pin::new(&mut this.inner).poll_frame(cx) {
            Poll::Ready(None) => {
                this.finish(false, false);
                Poll::Ready(None)
            }
            Poll::Ready(Some(Err(e))) => {
                this.finish(true, false);
                Poll::Ready(Some(Err(Box::new(e))))
            }
            Poll::Ready(Some(Ok(frame))) => {
                if let Some(d) = frame.data_ref() {
                    let d = d.clone();
                    this.observe(&d);
                }
                let next = (tokio::time::Instant::now() + this.idle).min(this.deadline);
                this.timer.as_mut().reset(next);
                Poll::Ready(Some(Ok(frame)))
            }
            Poll::Pending => {
                if this.timer.as_mut().poll(cx).is_ready() {
                    this.rec.status = 504;
                    this.finish(true, false);
                    return Poll::Ready(Some(Err("engine stream idle/total timeout".into())));
                }
                Poll::Pending
            }
        }
    }

    fn is_end_stream(&self) -> bool {
        self.done || self.inner.is_end_stream()
    }

    fn size_hint(&self) -> http_body::SizeHint {
        self.inner.size_hint()
    }
}

impl Drop for Observed {
    fn drop(&mut self) {
        if !self.done {
            self.finish(false, true);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn content_detection() {
        assert!(has_content(
            &serde_json::json!({"choices":[{"delta":{"content":"hi"}}]})
        ));
        assert!(has_content(
            &serde_json::json!({"choices":[{"delta":{"reasoning_content":"Okay"}}]})
        ));
        assert!(!has_content(
            &serde_json::json!({"choices":[{"delta":{"role":"assistant","content":null}}]})
        ));
        assert!(!has_content(
            &serde_json::json!({"choices":[],"usage":{"prompt_tokens":1}})
        ));
        assert!(has_content(
            &serde_json::json!({"type":"content_block_delta","delta":{"text":"x"}})
        ));
    }
}
