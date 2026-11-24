//! Saved servers: URL validation and the persisted server list.
//!
//! Pure model code (no Tauri types) so it is unit-tested directly. The list is
//! persisted as one JSON value in the app config dir by `store.rs`.
//!
//! URL policy: a server is an origin (`scheme://host[:port]`). `https` is the
//! default when no scheme is typed. Plain `http` is accepted only for loopback,
//! private (RFC 1918 / ULA), link-local and `.local` hosts, and is flagged
//! `insecure` so the UI can show a warning. Userinfo (`user:pass@`) is always
//! rejected: credentials are never stored by the desktop app.

use std::collections::HashMap;
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr};

use serde::{Deserialize, Serialize};
use url::{Host, Url};

/// Upper bounds keep the store and the tray/menu text small.
pub const MAX_SERVERS: usize = 50;
pub const MAX_NAME_LEN: usize = 64;
pub const MAX_URL_LEN: usize = 2048;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ServerError {
    Empty,
    TooLong,
    Invalid(String),
    Scheme(String),
    Credentials,
    PathNotAllowed,
    InsecureHttp(String),
    NameEmpty,
    NameTooLong,
    NameInvalid,
    DuplicateName(String),
    DuplicateUrl(String),
    NotFound,
    TooMany,
}

impl std::fmt::Display for ServerError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Empty => write!(f, "Enter the server URL."),
            Self::TooLong => write!(f, "The URL is too long."),
            Self::Invalid(why) => write!(f, "Not a valid URL ({why})."),
            Self::Scheme(s) => write!(f, "Only https:// (or http:// for local addresses) is allowed, not {s}://."),
            Self::Credentials => write!(f, "Do not put a username or password in the URL; sign in inside the app."),
            Self::PathNotAllowed => write!(f, "Use the server's base address only (no path, query or fragment)."),
            Self::InsecureHttp(host) => write!(
                f,
                "http:// is only allowed for localhost and private network addresses; use https://{host}."
            ),
            Self::NameEmpty => write!(f, "Give the server a name."),
            Self::NameTooLong => write!(f, "Names are limited to {MAX_NAME_LEN} characters."),
            Self::NameInvalid => write!(f, "Names cannot contain control characters."),
            Self::DuplicateName(n) => write!(f, "A server named \"{n}\" already exists."),
            Self::DuplicateUrl(n) => write!(f, "This URL is already saved as \"{n}\"."),
            Self::NotFound => write!(f, "Server not found."),
            Self::TooMany => write!(f, "At most {MAX_SERVERS} servers can be saved."),
        }
    }
}

impl std::error::Error for ServerError {}

/// A validated server origin.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct NormalizedUrl {
    /// `scheme://host[:port]` without a trailing slash.
    pub origin: String,
    /// `true` for plain http (only possible for local/private hosts).
    pub insecure: bool,
}

/// True for hosts where plain http is tolerated: loopback, RFC 1918, CGNAT,
/// link-local, IPv6 ULA / link-local, `localhost`, `*.localhost` and mDNS `*.local`.
pub fn is_local_or_private_host(host: &Host<&str>) -> bool {
    match host {
        Host::Domain(d) => {
            let d = d.trim_end_matches('.').to_ascii_lowercase();
            d == "localhost" || d.ends_with(".localhost") || d.ends_with(".local")
        }
        Host::Ipv4(ip) => is_private_v4(ip),
        Host::Ipv6(ip) => is_private_v6(ip),
    }
}

fn is_private_v4(ip: &Ipv4Addr) -> bool {
    let o = ip.octets();
    ip.is_loopback()
        || ip.is_private()
        || ip.is_link_local()
        // 100.64.0.0/10 (CGNAT, used by tailnets / VPNs)
        || (o[0] == 100 && (o[1] & 0b1100_0000) == 64)
}

fn is_private_v6(ip: &Ipv6Addr) -> bool {
    if ip.is_loopback() {
        return true;
    }
    if let Some(v4) = ip.to_ipv4_mapped() {
        return is_private_v4(&v4);
    }
    let first = ip.segments()[0];
    (first & 0xfe00) == 0xfc00 // fc00::/7 unique local
        || (first & 0xffc0) == 0xfe80 // fe80::/10 link local
}

/// Validate and normalise a server URL typed by the user (or carried by a deep link).
pub fn normalize_server_url(input: &str) -> Result<NormalizedUrl, ServerError> {
    let raw = input.trim();
    if raw.is_empty() {
        return Err(ServerError::Empty);
    }
    if raw.len() > MAX_URL_LEN {
        return Err(ServerError::TooLong);
    }
    if raw.chars().any(|c| c.is_control() || c.is_whitespace()) {
        return Err(ServerError::Invalid(
            "contains spaces or control characters".into(),
        ));
    }
    let with_scheme = if raw.contains("://") {
        raw.to_string()
    } else {
        format!("https://{raw}")
    };
    let url = Url::parse(&with_scheme).map_err(|e| ServerError::Invalid(e.to_string()))?;
    let scheme = url.scheme().to_ascii_lowercase();
    if scheme != "https" && scheme != "http" {
        return Err(ServerError::Scheme(scheme));
    }
    if !url.username().is_empty() || url.password().is_some() {
        return Err(ServerError::Credentials);
    }
    let host = url
        .host()
        .ok_or_else(|| ServerError::Invalid("missing host".into()))?;
    if url.path() != "/" && !url.path().is_empty() {
        return Err(ServerError::PathNotAllowed);
    }
    if url.query().is_some() || url.fragment().is_some() {
        return Err(ServerError::PathNotAllowed);
    }
    let insecure = scheme == "http";
    if insecure && !is_local_or_private_host(&host) {
        return Err(ServerError::InsecureHttp(host.to_string()));
    }
    let origin = url.origin().ascii_serialization();
    Ok(NormalizedUrl { origin, insecure })
}

/// Origin of an arbitrary URL (`None` for opaque origins such as `data:`).
pub fn origin_of(url: &Url) -> Option<String> {
    let origin = url.origin();
    origin.is_tuple().then(|| origin.ascii_serialization())
}

/// True when `host` is an IP literal (used only for display / tests).
pub fn is_ip_literal(host: &str) -> bool {
    host.trim_matches(|c| c == '[' || c == ']')
        .parse::<IpAddr>()
        .is_ok()
}

fn validate_name(name: &str) -> Result<String, ServerError> {
    let name = name.trim();
    if name.is_empty() {
        return Err(ServerError::NameEmpty);
    }
    if name.chars().count() > MAX_NAME_LEN {
        return Err(ServerError::NameTooLong);
    }
    if name.chars().any(char::is_control) {
        return Err(ServerError::NameInvalid);
    }
    Ok(name.to_string())
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Server {
    pub id: String,
    pub name: String,
    /// Origin of the web UI loaded in the main window (the only origin granted IPC).
    pub url: String,
    /// Optional admin API origin when it differs from the UI origin (dev: :8180 vs :3180).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub api_url: Option<String>,
    /// Unix epoch milliseconds.
    pub created_at: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_used_at: Option<u64>,
}

impl Server {
    pub fn insecure(&self) -> bool {
        self.url.starts_with("http://")
    }

    /// Base URL for the public admin endpoints (`/api/v1/branding`, `/api/v1/health`).
    pub fn api_base(&self) -> &str {
        self.api_url.as_deref().unwrap_or(&self.url)
    }
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ServerInput {
    #[serde(default)]
    pub id: Option<String>,
    pub name: String,
    pub url: String,
    #[serde(default)]
    pub api_url: Option<String>,
}

/// Remembered main-window geometry for one server (physical pixels).
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct WindowState {
    pub x: i32,
    pub y: i32,
    pub width: u32,
    pub height: u32,
    #[serde(default)]
    pub maximized: bool,
    #[serde(default = "default_zoom")]
    pub zoom: f64,
}

fn default_zoom() -> f64 {
    1.0
}

/// Everything the desktop app persists (`servers.json` in the app config dir).
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ServerList {
    #[serde(default)]
    pub servers: Vec<Server>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_server_id: Option<String>,
    #[serde(default)]
    pub window_states: HashMap<String, WindowState>,
}

impl ServerList {
    /// Parse a stored value; anything unreadable or invalid is dropped, never fatal.
    pub fn from_value(value: Option<serde_json::Value>) -> Self {
        let mut list: ServerList = value
            .and_then(|v| serde_json::from_value(v).ok())
            .unwrap_or_default();
        // Re-validate on load: a hand-edited store must not widen the IPC allowlist.
        list.servers.retain(|s| {
            normalize_server_url(&s.url)
                .map(|n| n.origin == s.url)
                .unwrap_or(false)
                && s.api_url
                    .as_deref()
                    .map(|a| normalize_server_url(a).is_ok())
                    .unwrap_or(true)
                && validate_name(&s.name).is_ok()
        });
        list.servers.truncate(MAX_SERVERS);
        let ids: Vec<String> = list.servers.iter().map(|s| s.id.clone()).collect();
        list.window_states.retain(|id, _| ids.contains(id));
        if let Some(last) = &list.last_server_id {
            if !ids.contains(last) {
                list.last_server_id = None;
            }
        }
        list
    }

    pub fn to_value(&self) -> serde_json::Value {
        serde_json::to_value(self).unwrap_or(serde_json::Value::Null)
    }

    pub fn get(&self, id: &str) -> Option<&Server> {
        self.servers.iter().find(|s| s.id == id)
    }

    /// Servers sorted for display: most recently used first, then by name.
    pub fn sorted(&self) -> Vec<Server> {
        let mut out = self.servers.clone();
        out.sort_by(|a, b| {
            b.last_used_at
                .unwrap_or(0)
                .cmp(&a.last_used_at.unwrap_or(0))
                .then_with(|| a.name.to_lowercase().cmp(&b.name.to_lowercase()))
        });
        out
    }

    /// Add (no id) or update (id) a server. Returns the stored server.
    pub fn upsert(
        &mut self,
        input: ServerInput,
        new_id: impl FnOnce() -> String,
        now: u64,
    ) -> Result<Server, ServerError> {
        let name = validate_name(&input.name)?;
        let url = normalize_server_url(&input.url)?.origin;
        let api_url = match input.api_url.as_deref().map(str::trim) {
            None | Some("") => None,
            Some(a) => {
                let api = normalize_server_url(a)?.origin;
                (api != url).then_some(api)
            }
        };
        let editing = input.id.clone();
        for other in &self.servers {
            if Some(&other.id) == editing.as_ref() {
                continue;
            }
            if other.name.to_lowercase() == name.to_lowercase() {
                return Err(ServerError::DuplicateName(other.name.clone()));
            }
            if other.url == url {
                return Err(ServerError::DuplicateUrl(other.name.clone()));
            }
        }
        match editing {
            Some(id) => {
                let server = self
                    .servers
                    .iter_mut()
                    .find(|s| s.id == id)
                    .ok_or(ServerError::NotFound)?;
                if server.url != url {
                    // A different origin is a different site: forget its window geometry.
                    self.window_states.remove(&id);
                }
                server.name = name;
                server.url = url;
                server.api_url = api_url;
                Ok(server.clone())
            }
            None => {
                if self.servers.len() >= MAX_SERVERS {
                    return Err(ServerError::TooMany);
                }
                let server = Server {
                    id: new_id(),
                    name,
                    url,
                    api_url,
                    created_at: now,
                    last_used_at: None,
                };
                self.servers.push(server.clone());
                Ok(server)
            }
        }
    }

    pub fn remove(&mut self, id: &str) -> Result<Server, ServerError> {
        let idx = self
            .servers
            .iter()
            .position(|s| s.id == id)
            .ok_or(ServerError::NotFound)?;
        self.window_states.remove(id);
        if self.last_server_id.as_deref() == Some(id) {
            self.last_server_id = None;
        }
        Ok(self.servers.remove(idx))
    }

    pub fn touch(&mut self, id: &str, now: u64) -> Result<Server, ServerError> {
        let server = self
            .servers
            .iter_mut()
            .find(|s| s.id == id)
            .ok_or(ServerError::NotFound)?;
        server.last_used_at = Some(now);
        self.last_server_id = Some(id.to_string());
        Ok(server.clone())
    }

    /// Resolve a deep-link `server=` value: an exact (case-insensitive) name, or a URL
    /// whose origin matches a saved server. Never matches unsaved origins.
    pub fn resolve(&self, name_or_url: &str) -> Option<&Server> {
        let needle = name_or_url.trim();
        if needle.is_empty() {
            return None;
        }
        if let Some(s) = self
            .servers
            .iter()
            .find(|s| s.name.to_lowercase() == needle.to_lowercase())
        {
            return Some(s);
        }
        let origin = normalize_server_url(needle).ok()?.origin;
        self.servers.iter().find(|s| s.url == origin)
    }

    /// True when `origin` is the UI origin of a saved server.
    pub fn is_saved_origin(&self, origin: &str) -> bool {
        self.servers.iter().any(|s| s.url == origin)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn input(name: &str, url: &str) -> ServerInput {
        ServerInput {
            id: None,
            name: name.into(),
            url: url.into(),
            api_url: None,
        }
    }

    fn ids() -> impl FnMut() -> String {
        let mut n = 0;
        move || {
            n += 1;
            format!("id-{n}")
        }
    }

    #[test]
    fn https_is_default_and_origin_is_normalised() {
        let n = normalize_server_url("GPUDeck.Example.com").unwrap();
        assert_eq!(n.origin, "https://gpudeck.example.com");
        assert!(!n.insecure);
        let n = normalize_server_url("https://gpudeck.example.com:8443/").unwrap();
        assert_eq!(n.origin, "https://gpudeck.example.com:8443");
        let n = normalize_server_url("https://gpudeck.example.com:443").unwrap();
        assert_eq!(n.origin, "https://gpudeck.example.com");
    }

    #[test]
    fn http_only_for_local_and_private_hosts() {
        for ok in [
            "http://localhost:3180",
            "http://app.localhost",
            "http://127.0.0.1:3180",
            "http://10.1.2.3",
            "http://172.16.0.9",
            "http://192.168.1.20:3180",
            "http://169.254.10.1",
            "http://100.100.1.1",
            "http://[::1]:3180",
            "http://[fd12:3456::1]",
            "http://[fe80::1]",
            "http://gpu-box.local",
        ] {
            let n = normalize_server_url(ok).unwrap_or_else(|e| panic!("{ok}: {e}"));
            assert!(n.insecure, "{ok}");
        }
        for bad in [
            "http://gpudeck.example.com",
            "http://8.8.8.8",
            "http://172.32.0.1",
            "http://100.128.0.1",
            "http://[2001:db8::1]",
            "http://localhost.example.com",
        ] {
            assert!(
                matches!(normalize_server_url(bad), Err(ServerError::InsecureHttp(_))),
                "{bad} should be rejected"
            );
        }
    }

    #[test]
    fn rejects_bad_urls() {
        assert_eq!(normalize_server_url("  "), Err(ServerError::Empty));
        assert!(matches!(
            normalize_server_url("ftp://x.example.com"),
            Err(ServerError::Scheme(_))
        ));
        assert!(matches!(
            normalize_server_url("javascript://alert(1)"),
            Err(ServerError::Scheme(_))
        ));
        assert!(matches!(
            normalize_server_url("file:///etc/passwd"),
            Err(ServerError::Scheme(_))
        ));
        assert_eq!(
            normalize_server_url("https://admin:pw@x.example.com"),
            Err(ServerError::Credentials)
        );
        assert_eq!(
            normalize_server_url("https://u@x.example.com"),
            Err(ServerError::Credentials)
        );
        assert_eq!(
            normalize_server_url("https://x.example.com/dashboard"),
            Err(ServerError::PathNotAllowed)
        );
        assert_eq!(
            normalize_server_url("https://x.example.com/?a=1"),
            Err(ServerError::PathNotAllowed)
        );
        assert_eq!(
            normalize_server_url("https://x.example.com/#x"),
            Err(ServerError::PathNotAllowed)
        );
        assert!(matches!(
            normalize_server_url("https://exa mple.com"),
            Err(ServerError::Invalid(_))
        ));
        assert!(matches!(
            normalize_server_url("https://"),
            Err(ServerError::Invalid(_))
        ));
        let long = format!("https://{}.com", "a".repeat(MAX_URL_LEN));
        assert_eq!(normalize_server_url(&long), Err(ServerError::TooLong));
    }

    #[test]
    fn idn_hosts_are_punycoded() {
        let n = normalize_server_url("https://gpü.example").unwrap();
        assert_eq!(n.origin, "https://xn--gp-yka.example");
    }

    #[test]
    fn upsert_add_edit_and_duplicates() {
        let mut list = ServerList::default();
        let mut next = ids();
        // A bare host gets https:// (also for localhost).
        assert_eq!(
            normalize_server_url("localhost:3180").unwrap().origin,
            "https://localhost:3180"
        );
        let lab = list
            .upsert(input("Lab", "http://localhost:3180"), &mut next, 1)
            .unwrap();
        assert_eq!(lab.url, "http://localhost:3180");
        assert!(lab.insecure());
        let prod = list
            .upsert(input("Prod", "gpudeck.example.com"), &mut next, 2)
            .unwrap();
        assert_eq!(prod.url, "https://gpudeck.example.com");
        assert_eq!(
            list.upsert(input("lab", "https://other.example.com"), &mut next, 3),
            Err(ServerError::DuplicateName("Lab".into()))
        );
        assert_eq!(
            list.upsert(input("Other", "https://gpudeck.example.com/"), &mut next, 3),
            Err(ServerError::DuplicateUrl("Prod".into()))
        );
        // editing keeps the id, may keep its own name/url
        let edited = list
            .upsert(
                ServerInput {
                    id: Some(prod.id.clone()),
                    name: "Prod".into(),
                    url: "https://gpudeck.example.com".into(),
                    api_url: Some("https://api.gpudeck.example.com".into()),
                },
                &mut next,
                4,
            )
            .unwrap();
        assert_eq!(edited.id, prod.id);
        assert_eq!(edited.api_base(), "https://api.gpudeck.example.com");
        assert_eq!(list.servers.len(), 2);
        // api_url equal to the UI origin collapses to None
        let same = list
            .upsert(
                ServerInput {
                    id: Some(prod.id.clone()),
                    name: "Prod".into(),
                    url: "https://gpudeck.example.com".into(),
                    api_url: Some("https://gpudeck.example.com/".into()),
                },
                &mut next,
                5,
            )
            .unwrap();
        assert_eq!(same.api_url, None);
        assert_eq!(
            list.upsert(
                ServerInput {
                    id: Some("nope".into()),
                    name: "X".into(),
                    url: "https://x.example.com".into(),
                    api_url: None
                },
                &mut next,
                6
            ),
            Err(ServerError::NotFound)
        );
    }

    #[test]
    fn names_are_validated() {
        let mut list = ServerList::default();
        let mut next = ids();
        assert_eq!(
            list.upsert(input("   ", "https://a.example.com"), &mut next, 1),
            Err(ServerError::NameEmpty)
        );
        assert_eq!(
            list.upsert(
                input(&"n".repeat(65), "https://a.example.com"),
                &mut next,
                1
            ),
            Err(ServerError::NameTooLong)
        );
        assert_eq!(
            list.upsert(input("a\u{7}b", "https://a.example.com"), &mut next, 1),
            Err(ServerError::NameInvalid)
        );
        assert_eq!(
            list.upsert(input("  Lab  ", "https://a.example.com"), &mut next, 1)
                .unwrap()
                .name,
            "Lab"
        );
    }

    #[test]
    fn remove_touch_resolve_and_sort() {
        let mut list = ServerList::default();
        let mut next = ids();
        let a = list
            .upsert(input("Alpha", "https://a.example.com"), &mut next, 1)
            .unwrap();
        let b = list
            .upsert(input("Beta", "http://192.168.1.5:3180"), &mut next, 1)
            .unwrap();
        list.touch(&b.id, 50).unwrap();
        list.window_states.insert(
            b.id.clone(),
            WindowState {
                x: 1,
                y: 2,
                width: 3,
                height: 4,
                maximized: false,
                zoom: 1.0,
            },
        );
        assert_eq!(list.sorted()[0].id, b.id);
        assert_eq!(list.last_server_id.as_deref(), Some(b.id.as_str()));
        assert_eq!(list.resolve("alpha").unwrap().id, a.id);
        assert_eq!(list.resolve("https://a.example.com/").unwrap().id, a.id);
        assert_eq!(list.resolve("a.example.com").unwrap().id, a.id);
        assert!(list.resolve("https://evil.example.com").is_none());
        assert!(list.resolve("").is_none());
        assert!(list.is_saved_origin("http://192.168.1.5:3180"));
        assert!(!list.is_saved_origin("http://192.168.1.5:8180"));
        list.remove(&b.id).unwrap();
        assert!(list.window_states.is_empty());
        assert!(list.last_server_id.is_none());
        assert_eq!(list.remove(&b.id), Err(ServerError::NotFound));
        assert_eq!(list.touch("missing", 1), Err(ServerError::NotFound));
    }

    #[test]
    fn store_round_trip_and_sanitising_load() {
        let mut list = ServerList::default();
        let mut next = ids();
        let a = list
            .upsert(input("Alpha", "https://a.example.com"), &mut next, 1)
            .unwrap();
        list.touch(&a.id, 9).unwrap();
        list.window_states.insert(
            a.id.clone(),
            WindowState {
                x: 10,
                y: 20,
                width: 1280,
                height: 800,
                maximized: true,
                zoom: 1.1,
            },
        );
        let value = list.to_value();
        assert_eq!(value["servers"][0]["lastUsedAt"], 9);
        assert_eq!(ServerList::from_value(Some(value)), list);

        // A tampered store: http to a public host, a path, an orphan window state.
        let tampered = serde_json::json!({
            "servers": [
                {"id": "x", "name": "Evil", "url": "http://evil.example.com", "createdAt": 1},
                {"id": "y", "name": "Path", "url": "https://a.example.com/x", "createdAt": 1},
                {"id": "z", "name": "Ok", "url": "https://ok.example.com", "createdAt": 1}
            ],
            "lastServerId": "x",
            "windowStates": {"x": {"x": 0, "y": 0, "width": 1, "height": 1}}
        });
        let loaded = ServerList::from_value(Some(tampered));
        assert_eq!(loaded.servers.len(), 1);
        assert_eq!(loaded.servers[0].id, "z");
        assert!(loaded.last_server_id.is_none());
        assert!(loaded.window_states.is_empty());
        assert_eq!(
            ServerList::from_value(Some(serde_json::json!("garbage"))),
            ServerList::default()
        );
        assert_eq!(ServerList::from_value(None), ServerList::default());
    }

    #[test]
    fn caps_the_number_of_servers() {
        let mut list = ServerList::default();
        let mut next = ids();
        for i in 0..MAX_SERVERS {
            list.upsert(
                input(&format!("S{i}"), &format!("https://s{i}.example.com")),
                &mut next,
                1,
            )
            .unwrap();
        }
        assert_eq!(
            list.upsert(input("One more", "https://more.example.com"), &mut next, 1),
            Err(ServerError::TooMany)
        );
    }

    #[test]
    fn ip_literal_helper() {
        assert!(is_ip_literal("127.0.0.1"));
        assert!(is_ip_literal("[::1]"));
        assert!(!is_ip_literal("localhost"));
    }
}
