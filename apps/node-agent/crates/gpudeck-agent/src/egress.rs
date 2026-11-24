//! Egress lock: outbound traffic only to the hub, model sources (`config_push.model_sources` +
//! `egress_allowlist`), DNS resolvers and loopback; inbound only the proxy port (and SSH).
//! Rendered as a pf anchor (macOS, loaded into `com.apple/gpudeck` which the stock
//! `/etc/pf.conf` already evaluates) or an nftables table (Linux). Rules are only programmed
//! with `gpudeck-agent run --apply-egress-lock` (needs root / CAP_NET_ADMIN); otherwise they are
//! rendered to `<state_dir>/egress.{pf,nft}` for review.

use std::net::IpAddr;

use gpudeck_protocol::EgressLockKind;

pub const PF_ANCHOR: &str = "com.apple/gpudeck";
pub const NFT_TABLE: &str = "gpudeck";

/// One allow-list entry after DNS resolution.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Allow {
    /// IP or CIDR.
    pub net: String,
    pub port: Option<u16>,
    /// Original entry (hostname) for comments.
    pub source: String,
}

impl Allow {
    fn is_v6(&self) -> bool {
        self.net.contains(':')
    }
}

/// `auto` → pf on macOS, nftables on Linux (when `nft` exists), else none.
pub fn detect_kind(setting: &str) -> EgressLockKind {
    match setting {
        "pf" => EgressLockKind::Pf,
        "nftables" => EgressLockKind::Nftables,
        "none" => EgressLockKind::None,
        _ => {
            if cfg!(target_os = "macos") && std::path::Path::new("/sbin/pfctl").exists() {
                EgressLockKind::Pf
            } else if cfg!(target_os = "linux") && crate::system::which("nft").is_some() {
                EgressLockKind::Nftables
            } else {
                EgressLockKind::None
            }
        }
    }
}

/// Split `host`, `host:port`, `cidr`, `cidr:port`, `[v6]:port`.
pub fn parse_entry(e: &str) -> (String, Option<u16>) {
    let e = e.trim();
    if let Some(rest) = e.strip_prefix('[') {
        if let Some((h, p)) = rest.split_once("]:") {
            return (h.to_string(), p.parse().ok());
        }
        return (rest.trim_end_matches(']').to_string(), None);
    }
    if e.matches(':').count() == 1 {
        if let Some((h, p)) = e.rsplit_once(':') {
            if let Ok(port) = p.parse() {
                return (h.to_string(), Some(port));
            }
        }
    }
    (e.to_string(), None)
}

/// `ip` or `ip/prefix` with a prefix that fits the address family — nothing else, so an entry
/// can never smuggle extra pf / nftables syntax into the rendered ruleset.
fn is_net(s: &str) -> bool {
    let (ip, prefix) = match s.split_once('/') {
        Some((ip, p)) => (ip, Some(p)),
        None => (s, None),
    };
    let Ok(addr) = ip.parse::<IpAddr>() else {
        return false;
    };
    match prefix {
        None => true,
        Some(p) => {
            let max = if addr.is_ipv4() { 32 } else { 128 };
            !p.is_empty()
                && p.chars().all(|c| c.is_ascii_digit())
                && p.parse::<u8>().is_ok_and(|n| n <= max)
        }
    }
}

/// DNS name for an allow-list entry (letters, digits, `-`, `.`, `_`), at most 253 chars.
fn is_hostname(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 253
        && !s.starts_with('-')
        && s.chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '.' | '_'))
}

/// Comment text for a rule: printable ASCII without quotes / comment / rule separators.
fn comment_safe(s: &str) -> String {
    s.chars()
        .filter(|c| c.is_ascii_graphic() && !matches!(c, '"' | '#' | ';' | '{' | '}' | '\\'))
        .take(120)
        .collect()
}

/// Resolve entries (hostnames → every A/AAAA record).
pub async fn resolve(entries: &[String]) -> Vec<Allow> {
    resolve_with(entries, true).await
}

/// Warn about an unresolvable allow-list entry at most once an hour (CDN names come and go;
/// the rules are re-rendered on every config change and every 15 minutes).
fn warn_unresolved(entry: &str, err: &dyn std::fmt::Display) {
    use std::collections::HashMap;
    use std::sync::Mutex;
    use std::time::{Duration, Instant};
    static SEEN: Mutex<Option<HashMap<String, Instant>>> = Mutex::new(None);
    let mut g = SEEN.lock().unwrap_or_else(|e| e.into_inner());
    let map = g.get_or_insert_with(HashMap::new);
    let due = map
        .get(entry)
        .is_none_or(|t| t.elapsed() >= Duration::from_secs(3600));
    if due {
        map.insert(entry.to_string(), Instant::now());
        tracing::warn!(entry = %entry, "egress allow-list entry did not resolve: {err}");
    } else {
        tracing::debug!(entry = %entry, "egress allow-list entry did not resolve: {err}");
    }
}

/// Like [`resolve`]; with `dns = false` host names are skipped (no lookup) and only IP / CIDR
/// literals are returned — the rules are rendered for inspection while the lock is not applied.
pub async fn resolve_with(entries: &[String], dns: bool) -> Vec<Allow> {
    let mut out = vec![];
    for e in entries {
        let (host, port) = parse_entry(e);
        if host.is_empty() {
            continue;
        }
        if is_net(&host) {
            out.push(Allow {
                net: host,
                port,
                source: comment_safe(e),
            });
            continue;
        }
        if !is_hostname(&host) {
            tracing::warn!(entry = %e.escape_debug(), "egress allow-list entry ignored: not an IP, CIDR or host name");
            continue;
        }
        if !dns {
            continue;
        }
        match tokio::net::lookup_host(format!("{host}:{}", port.unwrap_or(443))).await {
            Ok(addrs) => {
                for a in addrs {
                    let allow = Allow {
                        net: a.ip().to_string(),
                        port,
                        source: comment_safe(e),
                    };
                    if !out.contains(&allow) {
                        out.push(allow);
                    }
                }
            }
            Err(err) => warn_unresolved(e, &err),
        }
    }
    out
}

/// System DNS resolvers from `/etc/resolv.conf`.
pub fn resolvers() -> Vec<String> {
    std::fs::read_to_string("/etc/resolv.conf")
        .unwrap_or_default()
        .lines()
        .filter_map(|l| {
            l.trim()
                .strip_prefix("nameserver")
                .map(|r| r.trim().to_string())
        })
        .filter(|r| r.parse::<IpAddr>().is_ok())
        .collect()
}

/// Inbound openings besides loopback and established traffic.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Inbound {
    pub proxy_port: u16,
    /// SSH-instance forwarder range (`protocol.md` §8).
    pub ssh_ports: Option<[u16; 2]>,
    /// Cross-node group peers (`protocol.md` §9): allowed in (any TCP port) and out.
    pub peers: Vec<String>,
}

impl Inbound {
    pub fn new(proxy_port: u16) -> Self {
        Self {
            proxy_port,
            ssh_ports: None,
            peers: vec![],
        }
    }
}

/// pf anchor rules.
pub fn render_pf(allow: &[Allow], dns: &[String], inbound: &Inbound) -> String {
    let proxy_port = inbound.proxy_port;
    let mut o = String::from("# GPU Deck egress lock (managed by gpudeck-agent; do not edit)\n");
    let any: Vec<&Allow> = allow.iter().filter(|a| a.port.is_none()).collect();
    o.push_str(&format!(
        "table <gpudeck_allow> const {{ {} }}\n",
        any.iter()
            .map(|a| a.net.as_str())
            .collect::<Vec<_>>()
            .join(", ")
    ));
    o.push_str(&format!(
        "table <gpudeck_dns> const {{ {} }}\n",
        dns.join(", ")
    ));
    o.push_str("pass quick on lo0 all\n");
    o.push_str(&format!(
        "pass in quick proto tcp to any port {proxy_port} keep state\n"
    ));
    o.push_str("pass in quick proto tcp to any port 22 keep state\n");
    if let Some([a, b]) = inbound.ssh_ports {
        o.push_str(&format!(
            "pass in quick proto tcp to any port {a}:{b} keep state # ssh instances\n"
        ));
    }
    if !inbound.peers.is_empty() {
        let peers = inbound.peers.join(", ");
        o.push_str(&format!(
            "pass in quick proto tcp from {{ {peers} }} keep state # distributed group peers\n"
        ));
        o.push_str(&format!("pass out quick proto {{ tcp udp }} to {{ {peers} }} keep state # distributed group peers\n"));
    }
    o.push_str("pass out quick proto { udp tcp } to <gpudeck_dns> port 53 keep state\n");
    o.push_str("pass out quick proto { icmp icmp6 } all keep state\n");
    if !any.is_empty() {
        o.push_str("pass out quick proto { tcp udp } to <gpudeck_allow> keep state\n");
    }
    for a in allow.iter().filter(|a| a.port.is_some()) {
        let fam = if a.is_v6() { "inet6" } else { "inet" };
        o.push_str(&format!(
            "pass out quick {fam} proto tcp to {} port {} keep state # {}\n",
            a.net,
            a.port.unwrap_or(0),
            a.source
        ));
    }
    o.push_str("block return out log quick all\n");
    o.push_str("block drop in log quick all\n");
    o
}

/// nftables ruleset (atomic replace of table `inet gpudeck`).
pub fn render_nft(allow: &[Allow], dns: &[String], inbound: &Inbound) -> String {
    let proxy_port = inbound.proxy_port;
    let peer_rules = |dir: &str| -> String {
        inbound
            .peers
            .iter()
            .map(|p| {
                format!(
                    "    {} {dir} {p} accept comment \"distributed peer\"\n",
                    if p.contains(':') { "ip6" } else { "ip" }
                )
            })
            .collect()
    };
    let ssh_rule = inbound
        .ssh_ports
        .map(|[a, b]| format!("    tcp dport {a}-{b} accept comment \"ssh instances\"\n"))
        .unwrap_or_default();
    let set = |v6: bool| {
        allow
            .iter()
            .filter(|a| a.port.is_none() && a.is_v6() == v6)
            .map(|a| a.net.clone())
            .collect::<Vec<_>>()
    };
    let (v4, v6) = (set(false), set(true));
    let mut o = format!("# GPU Deck egress lock (managed by gpudeck-agent; do not edit)\ntable inet {NFT_TABLE}\ndelete table inet {NFT_TABLE}\ntable inet {NFT_TABLE} {{\n");
    o.push_str("  set allow4 {\n    type ipv4_addr\n    flags interval\n");
    if !v4.is_empty() {
        o.push_str(&format!("    elements = {{ {} }}\n", v4.join(", ")));
    }
    o.push_str("  }\n  set allow6 {\n    type ipv6_addr\n    flags interval\n");
    if !v6.is_empty() {
        o.push_str(&format!("    elements = {{ {} }}\n", v6.join(", ")));
    }
    o.push_str("  }\n  chain output {\n    type filter hook output priority 0; policy accept;\n    oif \"lo\" accept\n    ct state established,related accept\n");
    for d in dns {
        let fam = if d.contains(':') { "ip6" } else { "ip" };
        o.push_str(&format!(
            "    {fam} daddr {d} meta l4proto {{ tcp, udp }} th dport 53 accept\n"
        ));
    }
    o.push_str("    meta l4proto { icmp, ipv6-icmp } accept\n    ip daddr @allow4 accept\n    ip6 daddr @allow6 accept\n");
    o.push_str(&peer_rules("daddr"));
    for a in allow.iter().filter(|a| a.port.is_some()) {
        let fam = if a.is_v6() { "ip6" } else { "ip" };
        o.push_str(&format!(
            "    {fam} daddr {} tcp dport {} accept comment \"{}\"\n",
            a.net,
            a.port.unwrap_or(0),
            a.source.replace('"', "")
        ));
    }
    o.push_str("    log prefix \"gpudeck-egress-blocked \" counter reject\n  }\n");
    o.push_str(&format!(
        "  chain input {{\n    type filter hook input priority 0; policy accept;\n    iif \"lo\" accept\n    ct state established,related accept\n    meta l4proto {{ icmp, ipv6-icmp }} accept\n    tcp dport {{ 22, {proxy_port} }} accept\n{ssh_rule}{}    log prefix \"gpudeck-ingress-blocked \" counter drop\n  }}\n}}\n",
        peer_rules("saddr")
    ));
    o
}

/// Hugging Face hosts a download touches (API, LFS CDN, Xet storage), checked 2026-11-23; the
/// legacy `cdn-lfs.huggingface.co` names no longer resolve.
pub const HF_HOSTS: &[&str] = &[
    "huggingface.co",
    "hf.co",
    "cdn-lfs.hf.co",
    "cdn-lfs-us-1.hf.co",
    "cdn-lfs-eu-1.hf.co",
    "cas-bridge.xethub.hf.co",
    "cas-server.xethub.hf.co",
    "transfer.xethub.hf.co",
];
pub const MODELSCOPE_HOSTS: &[&str] = &["modelscope.cn", "www.modelscope.cn"];

/// Default destinations: the hub, HF/ModelScope (or their mirrors) and the allow-list.
/// Hosts taken from URLs are opened on the URL's port only (scheme default when absent) and
/// the built-in model hubs on 443 only; a port-less entry would open every port of their IPs.
/// Allow-list entries keep their own form (`host` = any port, `host:port` = that port).
pub fn destinations(
    hub_url: Option<&str>,
    allowlist: &[String],
    hf: Option<&str>,
    ms: Option<&str>,
) -> Vec<String> {
    let host_of = |u: &str| -> Option<String> {
        let (scheme, rest) = match u.split_once("://") {
            Some((s, r)) => (s.to_ascii_lowercase(), r),
            None => ("https".to_string(), u),
        };
        let hostport = rest.split('/').next().filter(|h| !h.is_empty())?;
        let has_port = match hostport.rsplit_once(']') {
            Some((_, tail)) => tail.starts_with(':'),
            None => hostport.contains(':'),
        };
        if has_port {
            return Some(hostport.to_string());
        }
        let port = if scheme == "http" || scheme == "ws" {
            80
        } else {
            443
        };
        Some(format!("{hostport}:{port}"))
    };
    let mut out = vec![];
    if let Some(h) = hub_url.and_then(host_of) {
        out.push(h);
    }
    match hf.and_then(host_of) {
        Some(m) => out.push(m),
        None => out.extend(HF_HOSTS.iter().map(|h| format!("{h}:443"))),
    }
    match ms.and_then(host_of) {
        Some(m) => out.push(m),
        None => out.extend(MODELSCOPE_HOSTS.iter().map(|h| format!("{h}:443"))),
    }
    out.extend(allowlist.iter().cloned());
    out.dedup();
    out
}

impl crate::agent::Agent {
    /// Addresses of every peer of the cross-node groups this node is part of.
    pub fn distributed_peers(&self) -> Vec<String> {
        let mut v: Vec<String> = self
            .instances
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .values()
            .filter(|i| i.state != gpudeck_protocol::InstanceState::Stopped)
            .filter_map(|i| i.args.launch.distributed.as_ref())
            .flat_map(crate::distributed::peer_addresses)
            .collect();
        v.sort();
        v.dedup();
        v
    }

    /// Render (always) and program (only with `--apply-egress-lock`) the egress rules.
    pub fn apply_egress(self: &std::sync::Arc<Self>) {
        let a = self.clone();
        tokio::spawn(async move {
            let cfg = a.applied.lock().unwrap_or_else(|e| e.into_inner()).clone();
            let allowlist = cfg
                .as_ref()
                .and_then(|c| c.egress_allowlist.clone())
                .unwrap_or_default();
            let ms = cfg
                .as_ref()
                .and_then(|c| c.model_sources.clone())
                .unwrap_or_default();
            let dests = destinations(
                a.cfg.hub_url.as_deref(),
                &allowlist,
                ms.huggingface_endpoint.as_deref(),
                ms.modelscope_endpoint.as_deref(),
            );
            // Host names are resolved only when the rules are programmed; otherwise they are
            // rendered for inspection with the literal entries only (no DNS, no warnings).
            let lookup = a.options.apply_egress_lock;
            let allow = resolve_with(&dests, lookup).await;
            let dns = resolvers();
            let peers: Vec<String> = resolve_with(&a.distributed_peers(), lookup)
                .await
                .into_iter()
                .map(|x| x.net)
                .collect();
            let inbound = Inbound {
                proxy_port: a.cfg.proxy_port,
                ssh_ports: a.container_runtime.as_ref().map(|_| a.cfg.ssh_port_range),
                peers,
            };
            let (file, text, cmd): (_, _, Vec<String>) = match a.egress_kind {
                EgressLockKind::Pf => {
                    let f = a.paths.root.join("egress.pf");
                    (
                        f.clone(),
                        render_pf(&allow, &dns, &inbound),
                        vec![
                            "/sbin/pfctl".into(),
                            "-a".into(),
                            PF_ANCHOR.into(),
                            "-f".into(),
                            f.display().to_string(),
                        ],
                    )
                }
                EgressLockKind::Nftables => {
                    let f = a.paths.root.join("egress.nft");
                    (
                        f.clone(),
                        render_nft(&allow, &dns, &inbound),
                        vec!["nft".into(), "-f".into(), f.display().to_string()],
                    )
                }
                _ => {
                    tracing::warn!("egress lock requested but no firewall backend is available (egress_lock = none)");
                    return;
                }
            };
            if let Err(e) = crate::state::write_private(&file, text.as_bytes(), 0o600) {
                tracing::warn!("write {}: {e}", file.display());
                return;
            }
            if !a.options.apply_egress_lock {
                return;
            }
            let out = tokio::process::Command::new(&cmd[0])
                .args(&cmd[1..])
                .output()
                .await;
            let ok = matches!(&out, Ok(o) if o.status.success());
            if a.egress_kind == EgressLockKind::Pf && ok {
                let _ = tokio::process::Command::new("/sbin/pfctl")
                    .arg("-E")
                    .output()
                    .await;
            }
            let mut d = gpudeck_protocol::JsonObject::new();
            d.insert("backend".into(), a.egress_kind.as_str().into());
            d.insert("destinations".into(), dests.clone().into());
            if ok {
                tracing::info!(backend = %a.egress_kind, "egress lock applied ({} destinations)", allow.len());
                a.event(
                    gpudeck_protocol::Event::info(
                        gpudeck_protocol::EventKind::ConfigApplied,
                        "egress lock applied",
                    )
                    .details(d),
                );
            } else {
                let err = out
                    .map(|o| String::from_utf8_lossy(&o.stderr).to_string())
                    .unwrap_or_else(|e| e.to_string());
                d.insert("error".into(), err.clone().into());
                tracing::error!("egress lock failed: {err}");
                a.event(
                    gpudeck_protocol::Event::critical(
                        gpudeck_protocol::EventKind::EgressBlocked,
                        "egress lock could not be applied",
                    )
                    .details(d),
                );
            }
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn allow() -> Vec<Allow> {
        vec![
            Allow {
                net: "10.0.0.5".into(),
                port: Some(8443),
                source: "hub.local:8443".into(),
            },
            Allow {
                net: "18.0.0.0/8".into(),
                port: None,
                source: "18.0.0.0/8".into(),
            },
            Allow {
                net: "2600::1".into(),
                port: None,
                source: "hf".into(),
            },
        ]
    }

    #[test]
    fn entries() {
        assert_eq!(parse_entry("hub:8443"), ("hub".into(), Some(8443)));
        assert_eq!(parse_entry("10.0.0.0/8"), ("10.0.0.0/8".into(), None));
        assert_eq!(
            parse_entry("10.0.0.0/8:443"),
            ("10.0.0.0/8".into(), Some(443))
        );
        assert_eq!(parse_entry("[2600::1]:443"), ("2600::1".into(), Some(443)));
        assert_eq!(parse_entry("2600::1"), ("2600::1".into(), None));
        let d = destinations(
            Some("https://hub.local:8443"),
            &["mirror.local".into()],
            Some("https://hf-mirror.local/"),
            None,
        );
        assert_eq!(
            d,
            [
                "hub.local:8443",
                "hf-mirror.local:443",
                "modelscope.cn:443",
                "www.modelscope.cn:443",
                "mirror.local"
            ]
        );
        // built-in model hubs: HTTPS only, never every port of their addresses
        let d = destinations(
            Some("https://hub.local"),
            &[],
            None,
            Some("http://ms.local"),
        );
        assert_eq!(d[0], "hub.local:443");
        let n = HF_HOSTS.len();
        assert!(d[1..=n].iter().all(|h| h.ends_with(":443")), "{d:?}");
        assert!(
            d.contains(&"cdn-lfs.hf.co:443".to_string())
                && !d.iter().any(|h| h.contains("cdn-lfs.huggingface.co"))
        );
        assert_eq!(d[n + 1], "ms.local:80");
        assert_eq!(
            destinations(Some("https://[2600::1]:8443/x"), &[], None, None)[0],
            "[2600::1]:8443"
        );
    }

    #[test]
    fn pf_rules() {
        let r = render_pf(&allow(), &["192.168.1.1".into()], &Inbound::new(9443));
        assert!(!r.contains("ssh instances"));
        let full = render_pf(
            &allow(),
            &[],
            &Inbound {
                proxy_port: 9443,
                ssh_ports: Some([2200, 2299]),
                peers: vec!["10.0.0.6".into()],
            },
        );
        assert!(full.contains("pass in quick proto tcp to any port 2200:2299 keep state"));
        assert!(full.contains("pass in quick proto tcp from { 10.0.0.6 } keep state"));
        assert!(full.contains("pass out quick proto { tcp udp } to { 10.0.0.6 } keep state"));
        assert!(r.contains("table <gpudeck_allow> const { 18.0.0.0/8, 2600::1 }"));
        assert!(r.contains("pass in quick proto tcp to any port 9443 keep state"));
        assert!(r.contains("pass out quick inet proto tcp to 10.0.0.5 port 8443 keep state"));
        assert!(r.contains("table <gpudeck_dns> const { 192.168.1.1 }"));
        assert!(r.trim_end().ends_with("block drop in log quick all"));
        assert!(r.contains("pass quick on lo0 all"));
    }

    #[test]
    fn nft_rules() {
        let full = render_nft(
            &allow(),
            &[],
            &Inbound {
                proxy_port: 9443,
                ssh_ports: Some([2200, 2299]),
                peers: vec!["10.0.0.6".into()],
            },
        );
        assert!(full.contains("tcp dport 2200-2299 accept"));
        assert!(
            full.contains("ip saddr 10.0.0.6 accept") && full.contains("ip daddr 10.0.0.6 accept")
        );
        let r = render_nft(&allow(), &["1.1.1.1".into()], &Inbound::new(9443));
        assert!(r.starts_with("# GPU Deck"));
        assert!(r.contains("table inet gpudeck\ndelete table inet gpudeck\n"));
        assert!(r.contains("elements = { 18.0.0.0/8 }"));
        assert!(r.contains("elements = { 2600::1 }"));
        assert!(r.contains("ip daddr 10.0.0.5 tcp dport 8443 accept"));
        assert!(r.contains("ip daddr 1.1.1.1 meta l4proto { tcp, udp } th dport 53 accept"));
        assert!(r.contains("tcp dport { 22, 9443 } accept"));
        assert!(r.contains("log prefix \"gpudeck-egress-blocked \" counter reject"));
    }

    #[tokio::test]
    async fn no_lookup_while_the_lock_is_not_applied() {
        // Rendering without the lock never resolves (no DNS traffic, no warnings).
        let r = resolve_with(
            &["does-not-exist.invalid:443".into(), "10.9.8.7:443".into()],
            false,
        )
        .await;
        assert_eq!(r.len(), 1);
        assert_eq!(r[0].net, "10.9.8.7");
    }

    #[tokio::test]
    async fn resolves_literals_without_dns() {
        let r = resolve(&["10.1.2.3:443".into(), "172.16.0.0/12".into()]).await;
        assert_eq!(
            r[0],
            Allow {
                net: "10.1.2.3".into(),
                port: Some(443),
                source: "10.1.2.3:443".into()
            }
        );
        assert_eq!(r[1].net, "172.16.0.0/12");
    }

    #[tokio::test]
    async fn entries_cannot_inject_firewall_syntax() {
        let r = resolve(&[
            "1.2.3.4/32\npass out quick all".into(),
            "10.0.0.0/8 }\npass out all {".into(),
            "1.2.3.4/99".into(),
            "evil.example\npass out all".into(),
            "-x".into(),
            "192.168.1.0/24".into(),
        ])
        .await;
        assert_eq!(r.len(), 1, "{r:?}");
        assert_eq!(r[0].net, "192.168.1.0/24");
        let rules = render_pf(
            &r,
            &[],
            &Inbound {
                proxy_port: 9443,
                ssh_ports: None,
                peers: vec![],
            },
        );
        assert!(!rules.contains("pass out quick all") && !rules.contains("pass out all"));
        assert_eq!(comment_safe("a\"b # c\nd"), "abcd");
    }
}
