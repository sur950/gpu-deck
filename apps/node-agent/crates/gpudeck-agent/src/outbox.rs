//! Everything the agent sends to the hub goes through the outbox (`protocol.md` §3):
//!
//! - while connected: FIFO to the socket writer;
//! - while disconnected: `event`s are kept in a ring buffer of `event_buffer_max` (log events
//!   are dropped first), `metrics_batch`es in a small ring (`METRICS_BUFFER_MAX`, samples carry
//!   their own `ts`), `heartbeat`/`inventory`/non-terminal `command_result`s are dropped
//!   (a fresh inventory is sent after every hello; terminal results are re-sent from
//!   `commands.json`).

use std::collections::VecDeque;
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
use std::sync::Mutex;

use gpudeck_protocol::{CommandResult, Envelope, Event, EventKind, Message};
use tokio::sync::Notify;
use uuid::Uuid;

/// Metric batches kept while disconnected (2 min at the default 2 s interval).
pub const METRICS_BUFFER_MAX: usize = 60;

#[derive(Default)]
struct Inner {
    connected: bool,
    queue: VecDeque<Envelope>,
}

pub struct Outbox {
    node_id: Option<Uuid>,
    inner: Mutex<Inner>,
    notify: Notify,
    event_max: AtomicUsize,
    pub dropped: AtomicU64,
}

impl Outbox {
    pub fn new(node_id: Option<Uuid>, event_max: usize) -> Self {
        Self {
            node_id,
            inner: Mutex::new(Inner::default()),
            notify: Notify::new(),
            event_max: AtomicUsize::new(event_max.max(1)),
            dropped: AtomicU64::new(0),
        }
    }

    pub fn node_id(&self) -> Option<Uuid> {
        self.node_id
    }

    pub fn set_event_max(&self, n: usize) {
        self.event_max.store(n.max(1), Ordering::Relaxed);
    }

    pub fn envelope(&self, message: Message) -> Envelope {
        Envelope::new(self.node_id, message)
    }

    pub fn event(&self, ev: Event) {
        self.push(self.envelope(Message::Event(ev)));
    }

    pub fn result(&self, command_id: Uuid, res: CommandResult) {
        self.push(
            self.envelope(Message::CommandResult(res))
                .with_correlation(command_id),
        );
    }

    pub fn send(&self, message: Message) {
        self.push(self.envelope(message));
    }

    pub fn is_connected(&self) -> bool {
        self.inner
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .connected
    }

    pub fn len(&self) -> usize {
        self.inner
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .queue
            .len()
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    pub fn push(&self, env: Envelope) {
        let mut g = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        if !g.connected {
            match &env.message {
                Message::Heartbeat(_) | Message::Inventory(_) | Message::Hello(_) => {
                    self.dropped.fetch_add(1, Ordering::Relaxed);
                    return;
                }
                Message::CommandResult(r) if !r.status.is_terminal() => {
                    self.dropped.fetch_add(1, Ordering::Relaxed);
                    return;
                }
                Message::CommandResult(_) => {
                    // Terminal results are re-sent from commands.json after reconnect.
                    self.dropped.fetch_add(1, Ordering::Relaxed);
                    return;
                }
                Message::MetricsBatch(_) => {
                    let n = g
                        .queue
                        .iter()
                        .filter(|e| matches!(e.message, Message::MetricsBatch(_)))
                        .count();
                    if n >= METRICS_BUFFER_MAX {
                        if let Some(pos) = g
                            .queue
                            .iter()
                            .position(|e| matches!(e.message, Message::MetricsBatch(_)))
                        {
                            g.queue.remove(pos);
                            self.dropped.fetch_add(1, Ordering::Relaxed);
                        }
                    }
                }
                Message::Event(_) => {
                    let max = self.event_max.load(Ordering::Relaxed);
                    let n = g
                        .queue
                        .iter()
                        .filter(|e| matches!(e.message, Message::Event(_)))
                        .count();
                    if n >= max {
                        let victim = g
                            .queue
                            .iter()
                            .position(|e| matches!(&e.message, Message::Event(ev) if ev.kind == EventKind::Log))
                            .or_else(|| g.queue.iter().position(|e| matches!(e.message, Message::Event(_))));
                        if let Some(pos) = victim {
                            g.queue.remove(pos);
                            self.dropped.fetch_add(1, Ordering::Relaxed);
                        }
                    }
                }
                _ => {}
            }
        }
        g.queue.push_back(env);
        drop(g);
        self.notify.notify_one();
    }

    /// Put a frame back at the head (write failed).
    pub fn requeue_front(&self, env: Envelope) {
        let mut g = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        g.queue.push_front(env);
    }

    pub fn set_connected(&self, connected: bool) {
        let mut g = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        g.connected = connected;
        if !connected {
            // Apply the disconnected policy to what is still queued.
            let before = g.queue.len();
            g.queue
                .retain(|e| matches!(e.message, Message::Event(_) | Message::MetricsBatch(_)));
            self.dropped
                .fetch_add((before - g.queue.len()) as u64, Ordering::Relaxed);
        }
        drop(g);
        self.notify.notify_one();
    }

    /// Pop the next frame; waits while empty or disconnected.
    pub async fn next(&self) -> Envelope {
        loop {
            let notified = self.notify.notified();
            {
                let mut g = self.inner.lock().unwrap_or_else(|e| e.into_inner());
                if g.connected {
                    if let Some(env) = g.queue.pop_front() {
                        if !g.queue.is_empty() {
                            self.notify.notify_one();
                        }
                        return env;
                    }
                }
            }
            notified.await;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use gpudeck_protocol::{CommandName, Heartbeat, MetricsBatch, Severity};

    fn hb() -> Message {
        Message::Heartbeat(Heartbeat {
            uptime_seconds: 1,
            config_version: 0,
            instances: vec![],
            running_commands: vec![],
            load: None,
        })
    }
    fn mb() -> Message {
        Message::MetricsBatch(MetricsBatch {
            interval_seconds: 2.0,
            sequence: None,
            gpu: vec![],
            engine: vec![],
            sessions: vec![],
            agent: None,
        })
    }

    #[tokio::test]
    async fn disconnected_policy() {
        let o = Outbox::new(Some(Uuid::new_v4()), 3);
        o.send(hb());
        o.result(Uuid::new_v4(), CommandResult::accepted(CommandName::Cancel));
        assert_eq!(o.len(), 0);
        for i in 0..2 {
            o.event(Event::new(
                EventKind::Log,
                Severity::Info,
                format!("log {i}"),
            ));
        }
        o.event(Event::info(EventKind::EngineStarted, "e1"));
        o.event(Event::info(EventKind::EngineStarted, "e2"));
        o.event(Event::info(EventKind::EngineStarted, "e3"));
        // capacity 3: both log events evicted first, then nothing else.
        assert_eq!(o.len(), 3);
        for _ in 0..(METRICS_BUFFER_MAX + 5) {
            o.send(mb());
        }
        assert_eq!(o.len(), 3 + METRICS_BUFFER_MAX);
        o.set_connected(true);
        let first = o.next().await;
        assert!(matches!(first.message, Message::Event(ref e) if e.message == "e1"));
        o.send(hb());
        o.set_connected(false);
        // heartbeat dropped on disconnect, events and metrics kept
        assert_eq!(o.len(), 2 + METRICS_BUFFER_MAX);
    }
}
