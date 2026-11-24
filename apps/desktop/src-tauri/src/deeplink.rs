//! `gpudeck://` deep links and in-app path validation.
//!
//! Format: `gpudeck://open?server=<saved-server-name-or-url>&path=/deployments/<id>`.
//! A link only ever connects to a server that is already saved; anything else
//! opens the connect screen prefilled (never auto-connects). `path` must be an
//! absolute in-app path; it is resolved against the server origin and rejected
//! when it would leave it.

use serde::Serialize;
use url::Url;

use crate::servers::{normalize_server_url, ServerList};

pub const SCHEME: &str = "gpudeck";
pub const MAX_PATH_LEN: usize = 2048;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DeepLinkError {
    NotGpuDeck,
    UnknownAction(String),
    MissingServer,
    BadPath(&'static str),
    Invalid,
}

impl std::fmt::Display for DeepLinkError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::NotGpuDeck => write!(f, "not a gpudeck:// link"),
            Self::UnknownAction(a) => write!(f, "unknown action {a:?}"),
            Self::MissingServer => write!(f, "missing server parameter"),
            Self::BadPath(why) => write!(f, "invalid path: {why}"),
            Self::Invalid => write!(f, "malformed link"),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DeepLink {
    pub server: String,
    pub path: Option<String>,
}

/// What to do with a parsed link.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DeepLinkAction {
    /// The server is saved: connect (or focus) and open `path`.
    Connect {
        server_id: String,
        path: Option<String>,
    },
    /// Unknown server: open the connect screen with these values, do not connect.
    Prefill(Prefill),
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Prefill {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub url: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub path: Option<String>,
    /// Why the connect screen was opened (shown as a notice).
    pub reason: String,
}

/// Validate an in-app path: absolute, same-origin, no control characters.
/// Returns the normalised `path[?query][#fragment]`.
pub fn validate_app_path(path: &str) -> Result<String, DeepLinkError> {
    if path.is_empty() {
        return Err(DeepLinkError::BadPath("empty"));
    }
    if path.len() > MAX_PATH_LEN {
        return Err(DeepLinkError::BadPath("too long"));
    }
    if !path.starts_with('/') {
        return Err(DeepLinkError::BadPath("must start with /"));
    }
    if path.starts_with("//") || path.contains('\\') {
        return Err(DeepLinkError::BadPath("must not name another host"));
    }
    if path.chars().any(char::is_control) {
        return Err(DeepLinkError::BadPath("control characters"));
    }
    let base = Url::parse("https://gpudeck.invalid/").map_err(|_| DeepLinkError::Invalid)?;
    let joined = base
        .join(path)
        .map_err(|_| DeepLinkError::BadPath("unparseable"))?;
    if joined.origin() != base.origin() {
        return Err(DeepLinkError::BadPath("must not name another host"));
    }
    let mut out = joined.path().to_string();
    if let Some(q) = joined.query() {
        out.push('?');
        out.push_str(q);
    }
    if let Some(f) = joined.fragment() {
        out.push('#');
        out.push_str(f);
    }
    Ok(out)
}

/// Join a validated app path onto a server origin.
pub fn app_url(origin: &str, path: &str) -> Option<Url> {
    let path = validate_app_path(path).ok()?;
    let base = Url::parse(origin).ok()?;
    let url = base.join(&path).ok()?;
    (url.origin() == base.origin()).then_some(url)
}

pub fn parse_deep_link(raw: &str) -> Result<DeepLink, DeepLinkError> {
    let url = Url::parse(raw.trim()).map_err(|_| DeepLinkError::Invalid)?;
    if url.scheme() != SCHEME {
        return Err(DeepLinkError::NotGpuDeck);
    }
    // `gpudeck://open?...` puts "open" in the host; `gpudeck:open?...` / `gpudeck:///open` in the path.
    let action = url
        .host_str()
        .map(str::to_string)
        .filter(|h| !h.is_empty())
        .unwrap_or_else(|| url.path().trim_matches('/').to_string());
    if !action.eq_ignore_ascii_case("open") {
        return Err(DeepLinkError::UnknownAction(action));
    }
    let mut server = None;
    let mut path = None;
    for (k, v) in url.query_pairs() {
        match k.as_ref() {
            "server" if server.is_none() => server = Some(v.trim().to_string()),
            "path" if path.is_none() => path = Some(v.to_string()),
            _ => {}
        }
    }
    let server = server
        .filter(|s| !s.is_empty())
        .ok_or(DeepLinkError::MissingServer)?;
    if server.len() > crate::servers::MAX_URL_LEN {
        return Err(DeepLinkError::Invalid);
    }
    let path = match path.filter(|p| !p.is_empty()) {
        Some(p) => Some(validate_app_path(&p)?),
        None => None,
    };
    Ok(DeepLink { server, path })
}

/// Decide what a link does against the saved servers.
pub fn resolve(list: &ServerList, link: &DeepLink) -> DeepLinkAction {
    if let Some(server) = list.resolve(&link.server) {
        return DeepLinkAction::Connect {
            server_id: server.id.clone(),
            path: link.path.clone(),
        };
    }
    let looks_like_url =
        link.server.contains("://") || link.server.contains('.') || link.server.contains(':');
    let (name, url) = match normalize_server_url(&link.server) {
        Ok(n) if looks_like_url => (None, Some(n.origin)),
        _ if looks_like_url => (None, Some(link.server.chars().take(200).collect())),
        _ => (
            Some(
                link.server
                    .chars()
                    .take(crate::servers::MAX_NAME_LEN)
                    .collect(),
            ),
            None,
        ),
    };
    DeepLinkAction::Prefill(Prefill {
        name,
        url,
        path: link.path.clone(),
        reason: "A link asked to open a server that is not saved. Check the address, save it, then connect."
            .into(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::servers::ServerInput;

    fn list() -> ServerList {
        let mut l = ServerList::default();
        let mut n = 0;
        for (name, url) in [
            ("Lab", "http://localhost:3180"),
            ("Prod", "https://gpudeck.example.com"),
        ] {
            l.upsert(
                ServerInput {
                    id: None,
                    name: name.into(),
                    url: url.into(),
                    api_url: None,
                },
                || {
                    n += 1;
                    format!("id-{n}")
                },
                1,
            )
            .unwrap();
        }
        l
    }

    #[test]
    fn parses_the_documented_form() {
        let l = parse_deep_link("gpudeck://open?server=Prod&path=/deployments/abc").unwrap();
        assert_eq!(
            l,
            DeepLink {
                server: "Prod".into(),
                path: Some("/deployments/abc".into())
            }
        );
        let l = parse_deep_link("gpudeck://open?server=https%3A%2F%2Fgpudeck.example.com").unwrap();
        assert_eq!(l.server, "https://gpudeck.example.com");
        assert_eq!(l.path, None);
        let l = parse_deep_link("gpudeck:open?server=Lab&path=%2Fswaps%2F1%3Ftab%3Dsteps").unwrap();
        assert_eq!(l.path.as_deref(), Some("/swaps/1?tab=steps"));
        let l = parse_deep_link("GPUDECK://OPEN?server=Lab").unwrap();
        assert_eq!(l.server, "Lab");
    }

    #[test]
    fn rejects_bad_links() {
        assert_eq!(
            parse_deep_link("https://open?server=x"),
            Err(DeepLinkError::NotGpuDeck)
        );
        assert_eq!(
            parse_deep_link("gpudeck://delete?server=x"),
            Err(DeepLinkError::UnknownAction("delete".into()))
        );
        assert_eq!(
            parse_deep_link("gpudeck://open?path=/x"),
            Err(DeepLinkError::MissingServer)
        );
        assert_eq!(
            parse_deep_link("gpudeck://open?server=%20"),
            Err(DeepLinkError::MissingServer)
        );
        assert_eq!(parse_deep_link("not a url"), Err(DeepLinkError::Invalid));
        for path in [
            "deployments/1",
            "//evil.example.com/x",
            "/\\evil.example.com",
            "https://evil.example.com/",
            "javascript:alert(1)",
            "/a\nb",
        ] {
            let link = format!("gpudeck://open?server=Lab&path={}", urlencode(path));
            assert!(
                matches!(parse_deep_link(&link), Err(DeepLinkError::BadPath(_))),
                "{path}"
            );
        }
    }

    fn urlencode(s: &str) -> String {
        url::form_urlencoded::byte_serialize(s.as_bytes()).collect()
    }

    #[test]
    fn app_paths_are_normalised_and_stay_on_origin() {
        assert_eq!(
            validate_app_path("/deployments/x/../y").unwrap(),
            "/deployments/y"
        );
        assert_eq!(validate_app_path("/a b").unwrap(), "/a%20b");
        assert_eq!(validate_app_path("/alerts#e1").unwrap(), "/alerts#e1");
        assert!(validate_app_path(&format!("/{}", "a".repeat(MAX_PATH_LEN))).is_err());
        assert_eq!(
            app_url("http://localhost:3180", "/deployments/1")
                .unwrap()
                .as_str(),
            "http://localhost:3180/deployments/1"
        );
        assert!(app_url("http://localhost:3180", "//evil.example.com").is_none());
    }

    #[test]
    fn saved_servers_connect_unknown_servers_prefill() {
        let l = list();
        let by_name = parse_deep_link("gpudeck://open?server=prod&path=/deployments/9").unwrap();
        assert_eq!(
            resolve(&l, &by_name),
            DeepLinkAction::Connect {
                server_id: "id-2".into(),
                path: Some("/deployments/9".into())
            }
        );
        let by_url = parse_deep_link("gpudeck://open?server=http://localhost:3180/").unwrap();
        assert!(
            matches!(resolve(&l, &by_url), DeepLinkAction::Connect { ref server_id, .. } if server_id == "id-1")
        );

        let unknown_url =
            parse_deep_link("gpudeck://open?server=https://new.example.com&path=/x").unwrap();
        match resolve(&l, &unknown_url) {
            DeepLinkAction::Prefill(p) => {
                assert_eq!(p.url.as_deref(), Some("https://new.example.com"));
                assert_eq!(p.name, None);
                assert_eq!(p.path.as_deref(), Some("/x"));
            }
            other => panic!("{other:?}"),
        }
        // Same host, different port: not the saved origin.
        let other_port = parse_deep_link("gpudeck://open?server=http://localhost:8180").unwrap();
        assert!(matches!(
            resolve(&l, &other_port),
            DeepLinkAction::Prefill(_)
        ));
        let unknown_name = parse_deep_link("gpudeck://open?server=Staging").unwrap();
        match resolve(&l, &unknown_name) {
            DeepLinkAction::Prefill(p) => {
                assert_eq!(p.name.as_deref(), Some("Staging"));
                assert_eq!(p.url, None);
            }
            other => panic!("{other:?}"),
        }
        // An invalid URL is still shown (for the user to fix), never connected.
        let insecure = parse_deep_link("gpudeck://open?server=http://evil.example.com").unwrap();
        match resolve(&l, &insecure) {
            DeepLinkAction::Prefill(p) => {
                assert_eq!(p.url.as_deref(), Some("http://evil.example.com"))
            }
            other => panic!("{other:?}"),
        }
    }
}
