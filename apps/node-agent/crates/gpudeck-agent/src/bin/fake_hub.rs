//! `gpudeck-fake-hub`: a local stand-in for the control plane's agent hub, for running the agent
//! on a dev machine without the Python stack. Prints the join command, then accepts one-line
//! JSON commands on stdin (`{"command":"load_model","args":{...}}`) or a `config_push` object
//! (`{"config_push":{...}}`), and logs every frame the agent sends.

use std::net::SocketAddr;

use anyhow::Result;
use gpudeck_agent::fakehub::FakeHub;
use gpudeck_protocol::{Command, ConfigPush, Message};
use tokio::io::{AsyncBufReadExt, BufReader};

#[tokio::main]
async fn main() -> Result<()> {
    gpudeck_agent::logging::init("info", false);
    let addr: SocketAddr = std::env::args()
        .nth(1)
        .unwrap_or_else(|| "127.0.0.1:0".into())
        .parse()?;
    let token = format!("gdj_{}", hex::encode(rand::random::<[u8; 20]>()));
    let hub = FakeHub::start(addr, &token).await?;
    println!("fake hub on {}", hub.url());
    println!(
        "join: gpudeck-agent join --url {} --token {} --ca-fingerprint {}",
        hub.url(),
        token,
        hub.ca_fingerprint()
    );
    let client = hub.issue_client("api-layer", "gpudeck-services");
    let dir = std::env::temp_dir().join("gpudeck-fake-hub");
    std::fs::create_dir_all(&dir)?;
    std::fs::write(dir.join("api-layer.crt"), &client.cert_pem)?;
    std::fs::write(
        dir.join("api-layer.key"),
        client.key_pem.as_deref().unwrap_or_default(),
    )?;
    std::fs::write(dir.join("ca.pem"), hub.ca_pem())?;
    println!(
        "API-layer client cert/key + CA written to {}",
        dir.display()
    );
    let mut seen = 0;
    let mut lines = BufReader::new(tokio::io::stdin()).lines();
    loop {
        tokio::select! {
            line = lines.next_line() => {
                let Ok(Some(line)) = line else { break };
                let node = hub.received().iter().find_map(|e| e.node_id);
                let Some(node) = node else { eprintln!("no agent connected yet"); continue };
                let v: serde_json::Value = match serde_json::from_str(&line) { Ok(v) => v, Err(e) => { eprintln!("bad json: {e}"); continue } };
                if let Some(cp) = v.get("config_push") {
                    match serde_json::from_value::<ConfigPush>(cp.clone()) { Ok(c) => { hub.send(Some(node), Message::ConfigPush(c)); } Err(e) => eprintln!("bad config_push: {e}") }
                } else {
                    match serde_json::from_value::<Command>(v) { Ok(c) => { let id = hub.command(node, c); println!("sent command {id}"); } Err(e) => eprintln!("bad command: {e}") }
                }
            }
            _ = tokio::time::sleep(std::time::Duration::from_millis(500)) => {
                let raw = hub.received_raw();
                for r in &raw[seen..] {
                    let short: String = r.chars().take(400).collect();
                    println!("<- {short}");
                }
                seen = raw.len();
            }
        }
    }
    Ok(())
}
