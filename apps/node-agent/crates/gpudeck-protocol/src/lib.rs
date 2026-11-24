//! serde types for the GPU Deck agent protocol v1 (`libs/contracts/agent-protocol`).
//!
//! One WebSocket text frame = one [`Envelope`]. The payload is selected by `type`
//! ([`Message`]). Every struct mirrors its JSON Schema: required fields are plain, nullable
//! fields are `Option`, defaulted fields carry `#[serde(default)]`. Field names are the wire
//! names, so the Postgres ingest on the hub side is a straight copy.

// Wire enums mirror the schemas; boxing variants would only complicate (de)serialisation.
#![allow(clippy::large_enum_variant)]

pub mod command;
pub mod common;
pub mod messages;

use chrono::{DateTime, SubsecRound, Utc};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

pub use command::*;
pub use common::*;
pub use messages::*;

/// Protocol major carried in `envelope.v` and `hello.protocol_version`.
pub const PROTOCOL_VERSION: u32 = 1;

/// Max WebSocket frame accepted by the hub.
pub const MAX_FRAME_BYTES: usize = 4 * 1024 * 1024;

/// Target size the agent keeps `metrics_batch` / `event(log)` frames under.
pub const SPLIT_FRAME_BYTES: usize = 1024 * 1024;

/// Hub endpoint paths (same listener, `:8443`).
pub const HUB_WS_PATH: &str = "/agent/v1/ws";
pub const HUB_JOIN_PATH: &str = "/agent/v1/join";

/// Close codes used by the hub.
pub mod close_code {
    pub const IDENTITY_MISMATCH: u16 = 4001;
    pub const PROTOCOL_ERROR: u16 = 4002;
    pub const REJECTED: u16 = 4003;
    pub const SUPERSEDED: u16 = 4004;
    pub const HUB_RESTARTING: u16 = 1012;
}

/// Wall clock with millisecond precision (what the schema recommends).
pub fn now() -> DateTime<Utc> {
    Utc::now().trunc_subsecs(3)
}

/// Payload of an envelope, tagged by `type`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", content = "payload", rename_all = "snake_case")]
pub enum Message {
    Hello(Hello),
    HelloAck(HelloAck),
    Inventory(Inventory),
    MetricsBatch(MetricsBatch),
    Event(Event),
    Command(Command),
    CommandResult(CommandResult),
    ConfigPush(ConfigPush),
    Heartbeat(Heartbeat),
}

impl Message {
    pub fn type_name(&self) -> &'static str {
        match self {
            Self::Hello(_) => "hello",
            Self::HelloAck(_) => "hello_ack",
            Self::Inventory(_) => "inventory",
            Self::MetricsBatch(_) => "metrics_batch",
            Self::Event(_) => "event",
            Self::Command(_) => "command",
            Self::CommandResult(_) => "command_result",
            Self::ConfigPush(_) => "config_push",
            Self::Heartbeat(_) => "heartbeat",
        }
    }
}

/// One WebSocket text frame.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Envelope {
    pub v: u32,
    pub id: Uuid,
    pub ts: DateTime<Utc>,
    pub node_id: Option<Uuid>,
    pub correlation_id: Option<Uuid>,
    #[serde(flatten)]
    pub message: Message,
}

impl Envelope {
    pub fn new(node_id: Option<Uuid>, message: Message) -> Self {
        Self {
            v: PROTOCOL_VERSION,
            id: Uuid::new_v4(),
            ts: now(),
            node_id,
            correlation_id: None,
            message,
        }
    }

    pub fn with_correlation(mut self, id: Uuid) -> Self {
        self.correlation_id = Some(id);
        self
    }

    pub fn to_json(&self) -> serde_json::Result<String> {
        serde_json::to_string(self)
    }

    pub fn from_json(text: &str) -> serde_json::Result<Self> {
        serde_json::from_str(text)
    }
}

// ---------------------------------------------------------------------------
// Join flow (`protocol.md` §2) — plain HTTPS, not an envelope.
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct JoinRequest {
    pub join_token: String,
    pub csr_pem: String,
    pub hostname: String,
    pub name: String,
    pub os: NodeOs,
    pub arch: NodeArch,
    pub agent_version: String,
    pub protocol_version: u32,
    pub proxy_port: u16,
    #[serde(default)]
    pub labels: Labels,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct JoinResponse {
    pub node_id: Uuid,
    pub node_name: String,
    #[serde(default)]
    pub cluster_id: Option<Uuid>,
    pub certificate_pem: String,
    pub ca_bundle_pem: String,
    pub serial: String,
    pub not_after: DateTime<Utc>,
    pub hub_ws_url: String,
    #[serde(default)]
    pub intervals: Intervals,
}

/// Admin envelope error shape (`{success:false, error:{code,message}}`) returned by `/join`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AdminError {
    pub code: String,
    pub message: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AdminEnvelope<T> {
    pub success: bool,
    #[serde(default)]
    pub data: Option<T>,
    #[serde(default)]
    pub error: Option<AdminError>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn envelope_round_trip() {
        let hb = Heartbeat {
            uptime_seconds: 1,
            config_version: 0,
            instances: vec![],
            running_commands: vec![],
            load: None,
        };
        let env = Envelope::new(Some(Uuid::new_v4()), Message::Heartbeat(hb));
        let json = env.to_json().unwrap();
        let v: serde_json::Value = serde_json::from_str(&json).unwrap();
        assert_eq!(v["type"], "heartbeat");
        assert_eq!(v["v"], 1);
        assert!(v["payload"].is_object());
        let back = Envelope::from_json(&json).unwrap();
        assert_eq!(back, env);
    }

    #[test]
    fn command_flatten() {
        let json = r#"{"command":"cancel","args":{"target_command_id":"6f1c2b2e-0b2b-4d8e-9e0e-1b2c3d4e5f60"},"timeout_seconds":30}"#;
        let c: Command = serde_json::from_str(json).unwrap();
        assert_eq!(c.timeout_seconds, 30);
        assert!(matches!(c.kind, CommandKind::Cancel(_)));
        assert_eq!(c.kind.name().to_string(), "cancel");
    }
}
