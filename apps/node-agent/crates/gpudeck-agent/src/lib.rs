//! GPU Deck node agent: joins the control plane, keeps an mTLS WebSocket to the
//! agent hub, supervises engine processes, reports devices / engine / session metrics, runs the
//! request handler in front of the engines — over an outbound tunnel to the API layer (default:
//! no inbound port) or the direct-mode mTLS proxy on :9443 — manages the model cache and the
//! host firewall, and enforces the CA-signed node security policy (pins, endpoints, CIDRs).
//!
//! Contracts: `libs/contracts/agent-protocol/` (protocol.md + JSON Schemas, typed in the
//! `gpudeck-protocol` crate), `libs/contracts/metrics.md`, `libs/contracts/env.md` §13.

pub mod agent;
pub mod artifact;
pub mod audit;
pub mod backend;
pub mod benchmark;
pub mod cache;
pub mod certs;
pub mod commands;
pub mod config;
pub mod distributed;
pub mod egress;
pub mod engine_compat;
pub mod engine_env;
pub mod fakehub;
pub mod firewall;
pub mod hub;
pub mod ingress;
pub mod init;
pub mod instances;
pub mod join;
pub mod logging;
pub mod metrics;
pub mod observe;
pub mod outbox;
pub mod pki;
pub mod prom;
pub mod proxy;
pub mod scan;
pub mod security;
pub mod ssh;
pub mod state;
pub mod supervisor;
pub mod system;
pub mod template;
pub mod testpki;
pub mod tls;
pub mod tunnel;
pub mod update;
pub mod warmup;

pub use agent::{Agent, RunOptions};
pub use config::AgentConfig;
