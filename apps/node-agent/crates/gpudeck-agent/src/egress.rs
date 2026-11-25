//! Host firewall rendering (`firewall.rs` builds the plan and programs it): a pf anchor (macOS,
//! `com.apple/gpudeck`, which the stock `/etc/pf.conf` already evaluates) or an nftables table
//! (Linux, `inet gpudeck`, replaced atomically).
//!
//! Two parts:
//! - the **guard** (always): inbound to the agent's own ports only — the proxy port (nobody in
//!   tunnel mode, the API layer's addresses in direct mode), the SSH-instance range (the
//!   instances' source CIDRs), metrics and engine ports (never from the network) — with the
//!   operator's SSH ports accepted first;
//! - the optional **egress lock**: outbound only to the hub, model sources
//!   (`config_push.model_sources` + `egress_allowlist`), DNS resolvers and loopback; inbound
//!   default deny.

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

/// Inbound guard for the agent's own ports (always part of the host firewall). Rules are scoped
/// to GPU Deck ports, so other services on the machine (and the operator's SSH) are untouched;
/// `preserve_ports` are accepted before anything else in case a GPU Deck range overlaps them.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Guard {
    /// Always accepted (the operator's sshd ports).
    pub preserve_ports: Vec<u16>,
    pub proxy_port: u16,
    /// `None`: tunnel mode, nothing may reach the proxy port. `Some`: direct mode, only these
    /// sources (CIDRs / addresses).
    pub proxy_sources: Option<Vec<String>>,
    /// SSH-instance forwarder range and the sources allowed to it (empty = nobody).
    pub ssh_ports: Option<[u16; 2]>,
    pub ssh_sources: Vec<String>,
    /// Ports that must never be reached from the network (metrics, engine range).
    pub closed: Vec<[u16; 2]>,
    /// Cross-node group peers (`protocol.md` §9): allowed in (any TCP port) and out.
    pub peers: Vec<String>,
}

/// Optional egress lock: outbound only to these destinations, inbound default deny.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct EgressPlan {
    pub allow: Vec<Allow>,
    pub dns: Vec<String>,
}

/// Everything the agent programs into pf / nftables.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Plan {
    pub guard: Guard,
    pub egress: Option<EgressPlan>,
}

impl Plan {
    /// Short content hash, written into the ruleset so `firewall status` can tell whether the
    /// loaded rules are the current plan.
    pub fn hash(&self) -> String {
        use sha2::{Digest, Sha256};
        let text = format!("{self:?}");
        hex::encode(&Sha256::digest(text.as_bytes())[..6])
    }
}

/// Keep only literal IPs / CIDRs (never anything that could inject firewall syntax).
fn nets(v: &[String]) -> Vec<String> {
    v.iter()
        .map(|s| s.trim().to_string())
        .filter(|s| is_net(s))
        .collect()
}

fn split_family(v: &[String]) -> (Vec<String>, Vec<String>) {
    v.iter().cloned().partition(|n| !n.contains(':'))
}

fn port_set(ports: &[u16]) -> String {
    let mut p: Vec<u16> = ports.to_vec();
    p.sort_unstable();
    p.dedup();
    p.iter().map(u16::to_string).collect::<Vec<_>>().join(", ")
}

/// pf anchor rules (`com.apple/gpudeck`; the stock `/etc/pf.conf` evaluates `com.apple/*`).
pub fn render_pf(plan: &Plan) -> String {
    let g = &plan.guard;
    let hash = plan.hash();
    let mut o =
        format!("# GPU Deck host firewall (managed by gpudeck-agent; do not edit) plan {hash}\n");
    let proxy_sources = g.proxy_sources.as_deref().map(nets).unwrap_or_default();
    let ssh_sources = nets(&g.ssh_sources);
    let peers = nets(&g.peers);
    o.push_str(&format!(
        "table <gpudeck_proxy> const {{ {} }}\n",
        proxy_sources.join(", ")
    ));
    o.push_str(&format!(
        "table <gpudeck_ssh> const {{ {} }}\n",
        ssh_sources.join(", ")
    ));
    if let Some(e) = &plan.egress {
        let any: Vec<&str> = e
            .allow
            .iter()
            .filter(|a| a.port.is_none())
            .map(|a| a.net.as_str())
            .collect();
        o.push_str(&format!(
            "table <gpudeck_allow> const {{ {} }}\n",
            any.join(", ")
        ));
        o.push_str(&format!(
            "table <gpudeck_dns> const {{ {} }}\n",
            nets(&e.dns).join(", ")
        ));
    }
    o.push_str(&format!(
        "pass quick on lo0 all label \"gpudeck-plan-{hash}\"\n"
    ));
    if !g.preserve_ports.is_empty() {
        o.push_str(&format!(
            "pass in quick proto tcp to any port {{ {} }} keep state # operator ssh, never blocked\n",
            port_set(&g.preserve_ports)
        ));
    }
    if !peers.is_empty() {
        let p = peers.join(", ");
        o.push_str(&format!(
            "pass in quick proto tcp from {{ {p} }} keep state # distributed group peers\n"
        ));
        if plan.egress.is_some() {
            o.push_str(&format!(
                "pass out quick proto {{ tcp udp }} to {{ {p} }} keep state # distributed group peers\n"
            ));
        }
    }
    let port = g.proxy_port;
    if g.proxy_sources.is_some() {
        o.push_str(&format!(
            "pass in quick proto tcp from <gpudeck_proxy> to any port {port} keep state # direct mode: API layer only\n"
        ));
    }
    o.push_str(&format!(
        "block drop in quick proto tcp to any port {port} # agent proxy\n"
    ));
    if let Some([a, b]) = g.ssh_ports {
        o.push_str(&format!(
            "pass in quick proto tcp from <gpudeck_ssh> to any port {a}:{b} keep state # ssh instances\n"
        ));
        o.push_str(&format!(
            "block drop in quick proto tcp to any port {a}:{b} # ssh instances\n"
        ));
    }
    for [a, b] in &g.closed {
        let r = if a == b {
            a.to_string()
        } else {
            format!("{a}:{b}")
        };
        o.push_str(&format!(
            "block drop in quick proto tcp to any port {r} # loopback-only\n"
        ));
    }
    if let Some(e) = &plan.egress {
        o.push_str("pass out quick proto { udp tcp } to <gpudeck_dns> port 53 keep state\n");
        o.push_str("pass out quick proto { icmp icmp6 } all keep state\n");
        if e.allow.iter().any(|a| a.port.is_none()) {
            o.push_str("pass out quick proto { tcp udp } to <gpudeck_allow> keep state\n");
        }
        for a in e.allow.iter().filter(|a| a.port.is_some()) {
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
    }
    o
}

/// nftables ruleset (atomic replace of table `inet gpudeck`).
pub fn render_nft(plan: &Plan) -> String {
    let g = &plan.guard;
    let hash = plan.hash();
    let set = |name: &str, v6: bool, items: &[String]| {
        let mut s = format!(
            "  set {name} {{\n    type {}\n    flags interval\n    auto-merge\n",
            if v6 { "ipv6_addr" } else { "ipv4_addr" }
        );
        if !items.is_empty() {
            s.push_str(&format!("    elements = {{ {} }}\n", items.join(", ")));
        }
        s.push_str("  }\n");
        s
    };
    let mut o = format!("# GPU Deck host firewall (managed by gpudeck-agent; do not edit) plan {hash}\ntable inet {NFT_TABLE}\ndelete table inet {NFT_TABLE}\ntable inet {NFT_TABLE} {{\n");
    let (p4, p6) = split_family(&g.proxy_sources.as_deref().map(nets).unwrap_or_default());
    let (s4, s6) = split_family(&nets(&g.ssh_sources));
    o.push_str(&set("proxy4", false, &p4));
    o.push_str(&set("proxy6", true, &p6));
    o.push_str(&set("ssh4", false, &s4));
    o.push_str(&set("ssh6", true, &s6));
    if let Some(e) = &plan.egress {
        let any = |v6: bool| {
            e.allow
                .iter()
                .filter(|a| a.port.is_none() && a.is_v6() == v6)
                .map(|a| a.net.clone())
                .collect::<Vec<_>>()
        };
        o.push_str(&set("allow4", false, &any(false)));
        o.push_str(&set("allow6", true, &any(true)));
    }
    let peers = nets(&g.peers);
    let peer_rules = |dir: &str| -> String {
        peers
            .iter()
            .map(|p| {
                format!(
                    "    {} {dir} {p} accept comment \"distributed peer\"\n",
                    if p.contains(':') { "ip6" } else { "ip" }
                )
            })
            .collect()
    };
    o.push_str(&format!(
        "  chain input {{\n    type filter hook input priority -10; policy accept;\n    iif \"lo\" accept comment \"gpudeck plan {hash}\"\n    ct state established,related accept\n"
    ));
    if !g.preserve_ports.is_empty() {
        o.push_str(&format!(
            "    tcp dport {{ {} }} accept comment \"operator ssh, never blocked\"\n",
            port_set(&g.preserve_ports)
        ));
    }
    o.push_str(&peer_rules("saddr"));
    let port = g.proxy_port;
    if g.proxy_sources.is_some() {
        o.push_str(&format!(
            "    tcp dport {port} ip saddr @proxy4 accept comment \"direct mode: API layer\"\n    tcp dport {port} ip6 saddr @proxy6 accept comment \"direct mode: API layer\"\n"
        ));
    }
    o.push_str(&format!(
        "    tcp dport {port} counter drop comment \"gpudeck agent proxy\"\n"
    ));
    if let Some([a, b]) = g.ssh_ports {
        o.push_str(&format!(
            "    tcp dport {a}-{b} ip saddr @ssh4 accept comment \"ssh instances\"\n    tcp dport {a}-{b} ip6 saddr @ssh6 accept comment \"ssh instances\"\n    tcp dport {a}-{b} counter drop comment \"ssh instances\"\n"
        ));
    }
    for [a, b] in &g.closed {
        let r = if a == b {
            a.to_string()
        } else {
            format!("{a}-{b}")
        };
        o.push_str(&format!(
            "    tcp dport {r} counter drop comment \"loopback-only\"\n"
        ));
    }
    if plan.egress.is_some() {
        o.push_str("    meta l4proto { icmp, ipv6-icmp } accept\n    log prefix \"gpudeck-ingress-blocked \" counter drop\n");
    }
    o.push_str("  }\n");
    if let Some(e) = &plan.egress {
        o.push_str("  chain output {\n    type filter hook output priority 0; policy accept;\n    oif \"lo\" accept\n    ct state established,related accept\n");
        for d in nets(&e.dns) {
            let fam = if d.contains(':') { "ip6" } else { "ip" };
            o.push_str(&format!(
                "    {fam} daddr {d} meta l4proto {{ tcp, udp }} th dport 53 accept\n"
            ));
        }
        o.push_str("    meta l4proto { icmp, ipv6-icmp } accept\n    ip daddr @allow4 accept\n    ip6 daddr @allow6 accept\n");
        o.push_str(&peer_rules("daddr"));
        for a in e.allow.iter().filter(|a| a.port.is_some()) {
            let fam = if a.is_v6() { "ip6" } else { "ip" };
            o.push_str(&format!(
                "    {fam} daddr {} tcp dport {} accept comment \"{}\"\n",
                a.net,
                a.port.unwrap_or(0),
                a.source.replace('"', "")
            ));
        }
        o.push_str("    log prefix \"gpudeck-egress-blocked \" counter reject\n  }\n");
    }
    o.push_str("}\n");
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

    fn guard(direct: bool) -> Guard {
        Guard {
            preserve_ports: vec![22, 2222],
            proxy_port: 9443,
            proxy_sources: direct.then(|| vec!["10.0.0.0/8".into(), "2001:db8::/32".into()]),
            ssh_ports: Some([2200, 2299]),
            ssh_sources: vec!["198.51.100.0/24".into(), "bogus; flush ruleset".into()],
            closed: vec![[9100, 9100], [10240, 10340]],
            peers: vec!["10.0.0.6".into()],
        }
    }

    #[test]
    fn pf_rules() {
        let tunnel = render_pf(&Plan {
            guard: guard(false),
            egress: None,
        });
        assert!(tunnel.contains("plan "));
        assert!(tunnel.contains("pass in quick proto tcp to any port { 22, 2222 } keep state"));
        // Tunnel mode: nothing may reach the proxy port; no pass rule for it at all.
        assert!(!tunnel.contains("from <gpudeck_proxy>"));
        assert!(tunnel.contains("block drop in quick proto tcp to any port 9443"));
        assert!(tunnel.contains("pass in quick proto tcp from <gpudeck_ssh> to any port 2200:2299"));
        assert!(tunnel.contains("block drop in quick proto tcp to any port 2200:2299"));
        assert!(tunnel.contains("block drop in quick proto tcp to any port 10240:10340"));
        assert!(tunnel.contains("block drop in quick proto tcp to any port 9100 "));
        assert!(tunnel.contains("table <gpudeck_ssh> const { 198.51.100.0/24 }"));
        assert!(!tunnel.contains("flush"), "entries cannot inject syntax");
        // Operator SSH is accepted before any GPU Deck rule.
        let ssh = tunnel.find("port { 22, 2222 }").unwrap();
        assert!(ssh < tunnel.find("port 2200:2299").unwrap());
        assert!(
            !tunnel.contains("block drop in log quick all"),
            "guard only: no default deny"
        );
        let direct = render_pf(&Plan {
            guard: guard(true),
            egress: Some(EgressPlan {
                allow: allow(),
                dns: vec!["192.168.1.1".into()],
            }),
        });
        assert!(direct.contains("table <gpudeck_proxy> const { 10.0.0.0/8, 2001:db8::/32 }"));
        assert!(direct.contains("pass in quick proto tcp from <gpudeck_proxy> to any port 9443"));
        assert!(direct.contains("table <gpudeck_allow> const { 18.0.0.0/8, 2600::1 }"));
        assert!(direct.contains("pass out quick inet proto tcp to 10.0.0.5 port 8443 keep state"));
        assert!(direct.contains("pass out quick proto { tcp udp } to { 10.0.0.6 } keep state"));
        assert!(direct.trim_end().ends_with("block drop in log quick all"));
        assert_ne!(
            Plan {
                guard: guard(true),
                egress: None
            }
            .hash(),
            Plan {
                guard: guard(false),
                egress: None
            }
            .hash()
        );
    }

    #[test]
    fn nft_rules() {
        let tunnel = render_nft(&Plan {
            guard: guard(false),
            egress: None,
        });
        assert!(tunnel.starts_with("# GPU Deck host firewall"));
        assert!(tunnel.contains("table inet gpudeck\ndelete table inet gpudeck\n"));
        assert!(tunnel.contains("tcp dport { 22, 2222 } accept"));
        assert!(!tunnel.contains("@proxy4 accept"));
        assert!(tunnel.contains("tcp dport 9443 counter drop"));
        assert!(tunnel.contains("tcp dport 2200-2299 ip saddr @ssh4 accept"));
        assert!(tunnel.contains("elements = { 198.51.100.0/24 }"));
        assert!(tunnel.contains("tcp dport 10240-10340 counter drop"));
        assert!(tunnel.contains("ip saddr 10.0.0.6 accept"));
        assert!(
            !tunnel.contains("chain output"),
            "no egress lock unless asked"
        );
        assert!(!tunnel.contains("flush"));
        let direct = render_nft(&Plan {
            guard: guard(true),
            egress: Some(EgressPlan {
                allow: allow(),
                dns: vec!["1.1.1.1".into()],
            }),
        });
        assert!(direct.contains("tcp dport 9443 ip saddr @proxy4 accept"));
        assert!(direct.contains("elements = { 10.0.0.0/8 }"));
        assert!(direct.contains("elements = { 2001:db8::/32 }"));
        assert!(direct.contains("ip daddr 10.0.0.5 tcp dport 8443 accept"));
        assert!(direct.contains("ip daddr 1.1.1.1 meta l4proto { tcp, udp } th dport 53 accept"));
        assert!(direct.contains("log prefix \"gpudeck-egress-blocked \" counter reject"));
        assert!(direct.contains("log prefix \"gpudeck-ingress-blocked \" counter drop"));
        assert!(direct.contains("ip daddr 10.0.0.6 accept"));
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
        let rules = render_pf(&Plan {
            guard: Guard {
                proxy_port: 9443,
                ..Default::default()
            },
            egress: Some(EgressPlan {
                allow: r.clone(),
                dns: vec![],
            }),
        });
        assert!(!rules.contains("pass out quick all") && !rules.contains("pass out all"));
        assert_eq!(comment_safe("a\"b # c\nd"), "abcd");
    }
}
