//! `gpudeck-agent security audit` and the start-up self-check: list the host's listening TCP
//! sockets and flag anything of GPU Deck's that is reachable from the network.
//!
//! Rules (protocol.md §13):
//! - the metrics port and every engine port (engine port range, ports of the persisted
//!   engines) must be loopback-only — anything else is EXPOSED;
//! - tunnel mode: nothing may listen on the proxy port; direct mode: the proxy should listen on
//!   a private address (all interfaces or a public address is a WARNING);
//! - SSH-instance forwarders should listen on a private address;
//! - other listeners are reported for information (not GPU Deck's).

use std::net::IpAddr;

use gpudeck_protocol::ServingMode;

use crate::config::AgentConfig;
use crate::state::StatePaths;

/// One listening TCP socket.
#[derive(Debug, Clone, PartialEq)]
pub struct Listener {
    pub addr: IpAddr,
    pub port: u16,
    pub pid: Option<u32>,
    pub process: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Level {
    Ok,
    Info,
    Warning,
    Exposed,
}

impl Level {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Ok => "ok",
            Self::Info => "info",
            Self::Warning => "WARNING",
            Self::Exposed => "EXPOSED",
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Finding {
    pub level: Level,
    pub listener: Listener,
    pub note: String,
}

/// Listening TCP sockets of this host (Linux: `/proc/net/tcp{,6}` + `/proc/<pid>/fd` for the
/// owner, which needs root for other users' processes; macOS: `lsof`).
pub fn listeners() -> Vec<Listener> {
    #[cfg(target_os = "linux")]
    {
        linux::listeners()
    }
    #[cfg(not(target_os = "linux"))]
    {
        lsof_listeners()
    }
}

#[cfg(not(target_os = "linux"))]
fn lsof_listeners() -> Vec<Listener> {
    let Ok(out) = std::process::Command::new("lsof")
        .args(["-nP", "-iTCP", "-sTCP:LISTEN", "-F", "pcn"])
        .output()
    else {
        return vec![];
    };
    parse_lsof(&String::from_utf8_lossy(&out.stdout))
}

/// `lsof -F pcn` output: `p<pid>`, `c<command>`, `n<addr>:<port>` lines.
pub fn parse_lsof(text: &str) -> Vec<Listener> {
    let mut out = vec![];
    let (mut pid, mut cmd) = (None, None);
    for line in text.lines() {
        let (tag, rest) = line.split_at(line.len().min(1));
        match tag {
            "p" => {
                pid = rest.parse().ok();
                cmd = None;
            }
            "c" => cmd = Some(rest.to_string()),
            "n" => {
                let Some((host, port)) = rest.rsplit_once(':') else {
                    continue;
                };
                let Ok(port) = port.parse() else { continue };
                let host = host.trim_start_matches('[').trim_end_matches(']');
                let addr = if host == "*" {
                    IpAddr::from([0, 0, 0, 0])
                } else if let Ok(a) = host.parse() {
                    a
                } else {
                    continue;
                };
                let l = Listener {
                    addr,
                    port,
                    pid,
                    process: cmd.clone(),
                };
                if !out.contains(&l) {
                    out.push(l);
                }
            }
            _ => {}
        }
    }
    out
}

#[cfg(target_os = "linux")]
mod linux {
    use super::Listener;
    use std::collections::HashMap;
    use std::net::{IpAddr, Ipv4Addr, Ipv6Addr};

    /// Parse `/proc/net/tcp{,6}` lines in LISTEN state (`0A`): `(addr, port, inode)`.
    pub fn parse_proc_net(text: &str, v6: bool) -> Vec<(IpAddr, u16, u64)> {
        let mut out = vec![];
        for line in text.lines().skip(1) {
            let f: Vec<&str> = line.split_whitespace().collect();
            if f.len() < 10 || f[3] != "0A" {
                continue;
            }
            let Some((a, p)) = f[1].split_once(':') else {
                continue;
            };
            let Ok(port) = u16::from_str_radix(p, 16) else {
                continue;
            };
            let Ok(inode) = f[9].parse() else { continue };
            let addr = if v6 {
                if a.len() != 32 {
                    continue;
                }
                let mut bytes = [0u8; 16];
                for (i, chunk) in (0..4).map(|i| &a[i * 8..i * 8 + 8]).enumerate() {
                    let Ok(w) = u32::from_str_radix(chunk, 16) else {
                        continue;
                    };
                    bytes[i * 4..i * 4 + 4].copy_from_slice(&w.to_le_bytes());
                }
                IpAddr::V6(Ipv6Addr::from(bytes))
            } else {
                let Ok(w) = u32::from_str_radix(a, 16) else {
                    continue;
                };
                IpAddr::V4(Ipv4Addr::from(w.to_le_bytes()))
            };
            out.push((addr, port, inode));
        }
        out
    }

    fn inode_owners() -> HashMap<u64, (u32, String)> {
        let mut map = HashMap::new();
        let Ok(rd) = std::fs::read_dir("/proc") else {
            return map;
        };
        for e in rd.flatten() {
            let Ok(pid) = e.file_name().to_string_lossy().parse::<u32>() else {
                continue;
            };
            let Ok(fds) = std::fs::read_dir(e.path().join("fd")) else {
                continue;
            };
            let comm = std::fs::read_to_string(e.path().join("comm"))
                .unwrap_or_default()
                .trim()
                .to_string();
            for fd in fds.flatten() {
                if let Ok(link) = std::fs::read_link(fd.path()) {
                    let s = link.to_string_lossy();
                    if let Some(n) = s.strip_prefix("socket:[").and_then(|r| r.strip_suffix(']')) {
                        if let Ok(inode) = n.parse() {
                            map.insert(inode, (pid, comm.clone()));
                        }
                    }
                }
            }
        }
        map
    }

    pub fn listeners() -> Vec<Listener> {
        let mut socks = vec![];
        for (f, v6) in [("/proc/net/tcp", false), ("/proc/net/tcp6", true)] {
            if let Ok(t) = std::fs::read_to_string(f) {
                socks.extend(parse_proc_net(&t, v6));
            }
        }
        let owners = inode_owners();
        let mut out: Vec<Listener> = socks
            .into_iter()
            .map(|(addr, port, inode)| {
                let o = owners.get(&inode);
                Listener {
                    addr,
                    port,
                    pid: o.map(|x| x.0),
                    process: o.map(|x| x.1.clone()),
                }
            })
            .collect();
        out.dedup();
        out
    }

    #[cfg(test)]
    mod tests {
        #[test]
        fn proc_net_parsing() {
            let v4 = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n   0: 0100007F:238C 00000000:0000 0A 00000000:00000000 00:00000000 00000000   999        0 12345 1 0000000000000000 100 0 0 10 0\n   1: 00000000:0016 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 222 1\n   2: 0100007F:1F90 0100007F:C350 01 00000000:00000000 00:00000000 00000000   999        0 333 1\n";
            let l = super::parse_proc_net(v4, false);
            assert_eq!(l.len(), 2, "established sockets skipped");
            assert_eq!(l[0], ("127.0.0.1".parse().unwrap(), 9100, 12345));
            assert_eq!(l[1], ("0.0.0.0".parse().unwrap(), 22, 222));
            let v6 = "  sl  local_address remote_address st\n   0: 00000000000000000000000001000000:24E3 00000000000000000000000000000000:0000 0A 00000000:00000000 00:00000000 00000000   999        0 4444 1\n";
            let l6 = super::parse_proc_net(v6, true);
            assert_eq!(l6[0], ("::1".parse().unwrap(), 9443, 4444));
        }
    }
}

fn is_public(ip: IpAddr) -> bool {
    crate::agent::is_public_ip(ip)
}

/// Engine ports from the persisted state (instances and engine processes).
pub fn engine_ports(paths: &StatePaths) -> Vec<u16> {
    let mut v: Vec<u16> = crate::state::load_instances(paths)
        .iter()
        .map(|i| i.port)
        .chain(
            crate::state::load_engine_processes(paths)
                .iter()
                .map(|p| p.port),
        )
        .filter(|p| *p > 0)
        .collect();
    v.sort_unstable();
    v.dedup();
    v
}

/// Classify every listener.
pub fn assess(
    cfg: &AgentConfig,
    engine_range: [u16; 2],
    engine_ports: &[u16],
    ls: &[Listener],
) -> Vec<Finding> {
    let mode = cfg.serving_mode();
    let [ssh_lo, ssh_hi] = cfg.ssh_port_range;
    let mut out = vec![];
    for l in ls {
        let loopback = l.addr.is_loopback();
        let all = l.addr.is_unspecified();
        let public = all || is_public(l.addr);
        let in_engines =
            (engine_range[0]..=engine_range[1]).contains(&l.port) || engine_ports.contains(&l.port);
        // Sockets of other users' processes have no owner unless the audit runs as root.
        let ours = l
            .process
            .as_deref()
            .is_some_and(|p| p.starts_with("gpudeck"));
        let (level, note) = if l.port == cfg.prom_port {
            if loopback {
                (Level::Ok, "agent metrics (loopback only)".to_string())
            } else {
                (
                    Level::Exposed,
                    "agent metrics port reachable from the network".to_string(),
                )
            }
        } else if in_engines {
            if loopback {
                (Level::Ok, "engine (loopback only)".to_string())
            } else {
                (
                    Level::Exposed,
                    "engine port listens on a network interface; engines must bind 127.0.0.1"
                        .to_string(),
                )
            }
        } else if l.port == cfg.proxy_port {
            match mode {
                ServingMode::Tunnel if !loopback => (
                    Level::Exposed,
                    "listening on the proxy port in tunnel mode (nothing should)".to_string(),
                ),
                ServingMode::Tunnel => (Level::Info, "loopback listener on the proxy port".to_string()),
                ServingMode::Direct if public => (
                    Level::Warning,
                    format!(
                        "direct-mode proxy on {}: set proxy_bind to the private interface (the source allow-list and firewall still apply)",
                        if all { "every interface" } else { "a public address" }
                    ),
                ),
                ServingMode::Direct => (
                    Level::Ok,
                    "direct-mode proxy (private interface, API layer only)".to_string(),
                ),
            }
        } else if cfg.firewall_preserve_ports.contains(&l.port) && !ours {
            (
                Level::Info,
                "operator SSH port (always accepted by the host firewall)".to_string(),
            )
        } else if (ssh_lo..=ssh_hi).contains(&l.port) && ours {
            if public {
                (
                    Level::Warning,
                    "SSH-instance forwarder on a public / every interface".to_string(),
                )
            } else {
                (
                    Level::Ok,
                    "SSH-instance forwarder (private, source CIDRs)".to_string(),
                )
            }
        } else if loopback {
            continue;
        } else if ours {
            (
                Level::Exposed,
                "unexpected GPU Deck agent listener on a network interface".to_string(),
            )
        } else {
            (Level::Info, "not managed by GPU Deck".to_string())
        };
        out.push(Finding {
            level,
            listener: l.clone(),
            note,
        });
    }
    out.sort_by(|a, b| {
        b.level
            .cmp(&a.level)
            .then(a.listener.port.cmp(&b.listener.port))
    });
    out
}

/// Print the audit; returns the worst level found.
pub fn print_report(findings: &[Finding]) -> Level {
    println!(
        "{:<8} {:<40} {:>5}  {:<20} NOTE",
        "LEVEL", "ADDRESS", "PORT", "PROCESS"
    );
    for f in findings {
        let proc = match (&f.listener.process, f.listener.pid) {
            (Some(p), Some(pid)) => format!("{p} ({pid})"),
            (Some(p), None) => p.clone(),
            _ => "?".into(),
        };
        println!(
            "{:<8} {:<40} {:>5}  {:<20} {}",
            f.level.as_str(),
            f.listener.addr,
            f.listener.port,
            proc,
            f.note
        );
    }
    findings.iter().map(|f| f.level).max().unwrap_or(Level::Ok)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn l(addr: &str, port: u16, p: &str) -> Listener {
        Listener {
            addr: addr.parse().unwrap(),
            port,
            pid: Some(1),
            process: Some(p.into()),
        }
    }

    #[test]
    fn classification() {
        let mut cfg = AgentConfig::default();
        let ls = vec![
            l("127.0.0.1", 9100, "gpudeck-agent"),
            l("0.0.0.0", 10241, "llama-server"),
            l("127.0.0.1", 10242, "llama-server"),
            l("0.0.0.0", 22, "sshd"),
            l("0.0.0.0", 9443, "gpudeck-agent"),
            l("10.0.0.5", 2200, "gpudeck-agent"),
            l("0.0.0.0", 7777, "gpudeck-agent"),
            l("0.0.0.0", 2222, "sshd"),
        ];
        cfg.firewall_preserve_ports = vec![22, 2222];
        let f = assess(&cfg, [10240, 10340], &[], &ls);
        let level = |port: u16| f.iter().find(|x| x.listener.port == port).unwrap().level;
        assert_eq!(level(10241), Level::Exposed);
        assert_eq!(level(10242), Level::Ok);
        assert_eq!(level(9100), Level::Ok);
        assert_eq!(level(22), Level::Info);
        assert_eq!(
            level(9443),
            Level::Exposed,
            "tunnel mode: no proxy listener"
        );
        assert_eq!(level(2200), Level::Ok);
        assert_eq!(level(7777), Level::Exposed);
        assert_eq!(
            level(2222),
            Level::Info,
            "operator sshd inside the SSH-instance range"
        );
        assert_eq!(f[0].level, Level::Exposed, "worst first");
        cfg.serving_mode = "direct".into();
        let f = assess(&cfg, [10240, 10340], &[], &ls);
        assert_eq!(
            f.iter().find(|x| x.listener.port == 9443).unwrap().level,
            Level::Warning
        );
        let private = vec![l("10.0.0.5", 9443, "gpudeck-agent")];
        assert_eq!(
            assess(&cfg, [10240, 10340], &[], &private)[0].level,
            Level::Ok
        );
    }

    #[test]
    fn lsof_parsing() {
        let t = "p123\ncgpudeck-agent\nn127.0.0.1:9100\nn*:9443\np7\ncsshd\nn[::]:22\n";
        let ls = parse_lsof(t);
        assert_eq!(ls.len(), 3);
        assert_eq!(ls[1].addr, "0.0.0.0".parse::<IpAddr>().unwrap());
        assert_eq!(ls[1].port, 9443);
        assert_eq!(ls[2].process.as_deref(), Some("sshd"));
        assert!(ls[2].addr.is_unspecified());
    }
}
