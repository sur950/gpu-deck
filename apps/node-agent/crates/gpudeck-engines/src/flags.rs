//! Ordered engine CLI flags with override semantics (`LaunchSpec.launch_flags` contract):
//! `true` = bare flag, `false`/`null` = remove, arrays repeat the flag, objects are passed as JSON.
//! Keys may be given with or without leading dashes; one-letter keys become `-k`, longer keys
//! `--key`. Alias groups (`-c` = `--ctx-size`) make user flags replace adapter defaults instead of
//! duplicating them.

use std::collections::BTreeMap;

use serde_json::Value;

/// Bind address / port flags the adapters set from the launch spec. Engines listen on
/// 127.0.0.1 only (docs/security-model.md, "Engines never listen on a network interface"), so user
/// launch flags can never override them.
pub const PROTECTED_FLAGS: &[&str] = &["--host", "--port", "--listen-ip", "--listen-port"];

/// True when a user flag key (any spelling: `host`, `--host`, `--host=0.0.0.0`) names a
/// protected bind flag.
pub fn is_protected(key: &str) -> bool {
    let name = key.trim().split('=').next().unwrap_or("");
    PROTECTED_FLAGS.contains(&normalize(name).as_str())
}

/// Add leading dashes when missing.
pub fn normalize(key: &str) -> String {
    let k = key.trim();
    if k.starts_with('-') {
        k.to_string()
    } else if k.chars().count() == 1 {
        format!("-{k}")
    } else {
        format!("--{k}")
    }
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct Flags {
    items: Vec<(String, Value)>,
    aliases: &'static [&'static [&'static str]],
}

impl Flags {
    pub fn new(aliases: &'static [&'static [&'static str]]) -> Self {
        Flags {
            items: Vec::new(),
            aliases,
        }
    }

    /// Spelling for a key: as given when dashed, else the alias with the same bare name
    /// (`ngl` → `-ngl`), else [`normalize`].
    fn resolve(&self, key: &str) -> String {
        let k = key.trim();
        if k.starts_with('-') {
            return k.to_string();
        }
        for group in self.aliases {
            if let Some(a) = group.iter().find(|a| a.trim_start_matches('-') == k) {
                return a.to_string();
            }
        }
        normalize(k)
    }

    fn canonical(&self, key: &str) -> String {
        let k = self.resolve(key);
        for group in self.aliases {
            if group.contains(&k.as_str()) {
                return group[0].to_string();
            }
        }
        k
    }

    fn position(&self, key: &str) -> Option<usize> {
        let c = self.canonical(key);
        self.items.iter().position(|(k, _)| self.canonical(k) == c)
    }

    /// Set (replacing any alias of the same flag in place).
    pub fn set(&mut self, key: &str, value: impl Into<Value>) -> &mut Self {
        let value = value.into();
        match self.position(key) {
            Some(i) => self.items[i] = (self.resolve(key), value),
            None => self.items.push((self.resolve(key), value)),
        }
        self
    }

    /// Set only when the flag (or an alias) is not present yet.
    pub fn set_default(&mut self, key: &str, value: impl Into<Value>) -> &mut Self {
        if self.position(key).is_none() {
            self.items.push((self.resolve(key), value.into()));
        }
        self
    }

    pub fn remove(&mut self, key: &str) -> &mut Self {
        if let Some(i) = self.position(key) {
            self.items.remove(i);
        }
        self
    }

    pub fn get(&self, key: &str) -> Option<&Value> {
        self.position(key).map(|i| &self.items[i].1)
    }

    /// True when present with a value other than `false`/`null`.
    pub fn has(&self, key: &str) -> bool {
        matches!(self.get(key), Some(v) if !matches!(v, Value::Null | Value::Bool(false)))
    }

    pub fn get_u64(&self, key: &str) -> Option<u64> {
        match self.get(key)? {
            Value::Number(n) => n.as_u64().or_else(|| n.as_f64().map(|f| f as u64)),
            Value::String(s) => s.trim().parse().ok(),
            _ => None,
        }
    }

    pub fn get_f64(&self, key: &str) -> Option<f64> {
        match self.get(key)? {
            Value::Number(n) => n.as_f64(),
            Value::String(s) => s.trim().parse().ok(),
            _ => None,
        }
    }

    pub fn get_str(&self, key: &str) -> Option<String> {
        match self.get(key)? {
            Value::String(s) => Some(s.clone()),
            Value::Number(n) => Some(n.to_string()),
            _ => None,
        }
    }

    /// Apply user flags on top: they replace defaults; `false`/`null` remove the flag.
    pub fn merge_user(&mut self, user: &BTreeMap<String, Value>) -> &mut Self {
        for (k, v) in user {
            if is_protected(k) {
                tracing::warn!(flag = %k, "launch flag ignored: bind address/port are set by the agent");
                continue;
            }
            match v {
                Value::Null | Value::Bool(false) => {
                    self.remove(k);
                }
                other => {
                    self.set(k, other.clone());
                }
            }
        }
        self
    }

    /// Apply preset flags as defaults (never override what is already set).
    pub fn merge_defaults(&mut self, defaults: &[(&str, Value)]) -> &mut Self {
        for (k, v) in defaults {
            self.set_default(k, v.clone());
        }
        self
    }

    pub fn to_args(&self) -> Vec<String> {
        let mut out = Vec::new();
        for (k, v) in &self.items {
            push_flag(&mut out, k, v);
        }
        out
    }
}

fn scalar(v: &Value) -> Option<String> {
    match v {
        Value::String(s) => Some(s.clone()),
        Value::Number(n) => Some(n.to_string()),
        Value::Object(_) => Some(v.to_string()),
        _ => None,
    }
}

fn push_flag(out: &mut Vec<String>, k: &str, v: &Value) {
    match v {
        Value::Null | Value::Bool(false) => {}
        Value::Bool(true) => out.push(k.to_string()),
        Value::Array(items) => {
            for it in items {
                push_flag(out, k, it);
            }
        }
        other => {
            if let Some(s) = scalar(other) {
                out.push(k.to_string());
                out.push(s);
            }
        }
    }
}

/// Render a free-form flag map (the `{{flags}}` placeholder).
pub fn render_map(map: &BTreeMap<String, Value>) -> Vec<String> {
    let mut f = Flags::new(&[]);
    f.merge_user(map);
    f.to_args()
}

#[cfg(test)]
mod tests {
    use super::*;

    const ALIASES: &[&[&str]] = &[
        &["--ctx-size", "-c"],
        &["--n-gpu-layers", "-ngl", "--gpu-layers"],
    ];

    #[test]
    fn render_and_override() {
        let mut f = Flags::new(ALIASES);
        f.set("--ctx-size", 4096)
            .set("ngl", 99)
            .set("jinja", true)
            .set("lora", serde_json::json!(["a", "b"]));
        let mut user = BTreeMap::new();
        user.insert("c".to_string(), serde_json::json!(8192));
        user.insert("jinja".to_string(), Value::Null);
        user.insert("--gpu-layers".to_string(), serde_json::json!("all"));
        f.merge_user(&user);
        assert_eq!(
            f.to_args(),
            vec![
                "-c",
                "8192",
                "--gpu-layers",
                "all",
                "--lora",
                "a",
                "--lora",
                "b"
            ]
        );
        assert_eq!(f.get_u64("--ctx-size"), Some(8192));
        assert!(!f.has("jinja"));
    }

    #[test]
    fn user_flags_cannot_rebind_the_engine() {
        let mut f = Flags::new(ALIASES);
        f.set("--host", "127.0.0.1").set("--port", 10240u64);
        let mut user = BTreeMap::new();
        user.insert("host".to_string(), serde_json::json!("0.0.0.0"));
        user.insert("--port".to_string(), serde_json::json!(80));
        user.insert("--host=0.0.0.0".to_string(), serde_json::json!(true));
        user.insert("listen-ip".to_string(), serde_json::json!("0.0.0.0"));
        user.insert("c".to_string(), serde_json::json!(4096));
        f.merge_user(&user);
        assert_eq!(
            f.to_args(),
            vec!["--host", "127.0.0.1", "--port", "10240", "-c", "4096"]
        );
        // `null` / `false` cannot remove them either.
        let mut f = Flags::new(ALIASES);
        f.set("--host", "127.0.0.1");
        f.merge_user(&BTreeMap::from([("--host".to_string(), Value::Null)]));
        assert_eq!(f.to_args(), vec!["--host", "127.0.0.1"]);
    }

    #[test]
    fn defaults_do_not_override() {
        let mut f = Flags::new(ALIASES);
        f.set("-c", 100);
        f.merge_defaults(&[
            ("--ctx-size", serde_json::json!(5)),
            ("--x", serde_json::json!(true)),
        ]);
        assert_eq!(f.to_args(), vec!["-c", "100", "--x"]);
        assert_eq!(normalize("tp_size"), "--tp_size");
        let obj = render_map(&BTreeMap::from([(
            "cfg".to_string(),
            serde_json::json!({"a": 1}),
        )]));
        assert_eq!(obj, vec!["--cfg", "{\"a\":1}"]);
    }
}
