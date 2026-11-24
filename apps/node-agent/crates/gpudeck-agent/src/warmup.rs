//! Warm-up requests after a model loads, so the first user requests do not pay one-time costs:
//! llama.cpp CUDA builds JIT-compile PTX for GPUs newer than the build's SASS (8–25 s for each
//! new prompt shape on an H200 with the CUDA 12.8 x64 build; cached on disk afterwards), and
//! every engine allocates its batch buffers lazily.
//!
//! A few requests of different prompt lengths (short, about one ubatch, a few ubatches — bounded
//! by the context size) go straight to the engine on loopback before the instance is reported
//! `ready`. Failures and timeouts are logged and never fail the load. Agent setting
//! `engine_warmup`: `auto` (llama.cpp on NVIDIA GPUs), `on` (every chat / embedding model), `off`.

use std::time::{Duration, Instant};

use gpudeck_protocol::{DeviceAssignment, DeviceVendor, EngineName, ModelType};
use serde_json::{json, Value};

/// One warm-up request.
#[derive(Debug, Clone, PartialEq)]
pub struct WarmupRequest {
    pub path: &'static str,
    pub body: Value,
}

/// Approximate prompt sizes (words; ~1.3 tokens each) of the chat warm-up requests.
const CHAT_WORDS: &[u64] = &[8, 400, 1500];

fn filler(words: u64) -> String {
    const W: &[&str] = &[
        "the", "quick", "brown", "fox", "jumps", "over", "a", "lazy", "dog", "while", "seven",
        "engines", "warm", "their", "kernels", "before", "serving", "real", "traffic", "today",
    ];
    (0..words)
        .map(|i| W[(i as usize * 7 + i as usize / 3) % W.len()])
        .collect::<Vec<_>>()
        .join(" ")
}

/// Whether to warm up this launch.
pub fn enabled(mode: &str, engine: EngineName, devices: &[DeviceAssignment]) -> bool {
    match mode {
        "off" | "false" | "0" => false,
        "on" | "true" | "1" | "all" => true,
        // auto: llama.cpp CUDA builds (PTX JIT on GPUs without matching SASS).
        _ => {
            engine == EngineName::LlamaCpp
                && devices.iter().any(|d| d.vendor == DeviceVendor::Nvidia)
        }
    }
}

/// The requests for a model (`model` = the name the engine answers to; the router routes by it).
pub fn plan(model: &str, model_type: ModelType, context_size: Option<u64>) -> Vec<WarmupRequest> {
    // Leave room for the reply and the chat template: at most ~half the context in prompt.
    let max_words = context_size.map(|c| (c / 2) * 3 / 4).unwrap_or(1500).max(8);
    match model_type {
        ModelType::Chat | ModelType::Thinking | ModelType::Vision => {
            let mut sizes: Vec<u64> = CHAT_WORDS.iter().map(|w| (*w).min(max_words)).collect();
            sizes.dedup();
            sizes
                .into_iter()
                .map(|w| WarmupRequest {
                    path: "/v1/chat/completions",
                    body: json!({
                        "model": model,
                        "messages": [{"role": "user", "content": filler(w)}],
                        "max_tokens": 4,
                        "temperature": 0,
                        "stream": false,
                    }),
                })
                .collect()
        }
        ModelType::Embedding => [8u64, 400.min(max_words)]
            .into_iter()
            .map(|w| WarmupRequest {
                path: "/v1/embeddings",
                body: json!({"model": model, "input": [filler(w)]}),
            })
            .collect(),
        _ => vec![],
    }
}

/// Send `reqs` in order to `base` (`http://127.0.0.1:<port>`), within `budget`. Returns how many
/// answered 2xx and the time spent.
pub async fn run(base: &str, reqs: &[WarmupRequest], budget: Duration) -> (usize, Duration) {
    let started = Instant::now();
    let Ok(http) = reqwest::Client::builder().no_proxy().build() else {
        return (0, Duration::ZERO);
    };
    let mut ok = 0;
    for r in reqs {
        let left = budget.saturating_sub(started.elapsed());
        if left.is_zero() {
            tracing::warn!("warm-up budget of {:?} used up", budget);
            break;
        }
        let t = Instant::now();
        match http
            .post(format!("{base}{}", r.path))
            .timeout(left)
            .json(&r.body)
            .send()
            .await
        {
            Ok(resp) if resp.status().is_success() => {
                let _ = resp.bytes().await;
                ok += 1;
                tracing::debug!(path = r.path, "warm-up request done in {:?}", t.elapsed());
            }
            Ok(resp) => {
                tracing::warn!(path = r.path, status = %resp.status(), "warm-up request refused");
                break;
            }
            Err(e) => {
                tracing::warn!(path = r.path, "warm-up request failed: {e}");
                break;
            }
        }
    }
    (ok, started.elapsed())
}

#[cfg(test)]
mod tests {
    use super::*;
    use uuid::Uuid;

    fn dev(v: DeviceVendor) -> Vec<DeviceAssignment> {
        vec![DeviceAssignment {
            device_id: Uuid::nil(),
            index: 0,
            vendor: v,
            slice: None,
        }]
    }

    #[test]
    fn auto_is_llama_cpp_on_nvidia() {
        assert!(enabled(
            "auto",
            EngineName::LlamaCpp,
            &dev(DeviceVendor::Nvidia)
        ));
        assert!(!enabled(
            "auto",
            EngineName::LlamaCpp,
            &dev(DeviceVendor::Apple)
        ));
        assert!(!enabled(
            "auto",
            EngineName::LlamaCpp,
            &dev(DeviceVendor::Amd)
        ));
        assert!(!enabled(
            "auto",
            EngineName::Vllm,
            &dev(DeviceVendor::Nvidia)
        ));
        assert!(enabled("on", EngineName::Vllm, &[]));
        assert!(!enabled(
            "off",
            EngineName::LlamaCpp,
            &dev(DeviceVendor::Nvidia)
        ));
    }

    #[test]
    fn plans_by_type_and_context() {
        let p = plan("m", ModelType::Chat, None);
        assert_eq!(p.len(), 3);
        assert!(p
            .iter()
            .all(|r| r.path == "/v1/chat/completions" && r.body["model"] == "m"));
        let words = |r: &WarmupRequest| {
            r.body["messages"][0]["content"]
                .as_str()
                .unwrap()
                .split(' ')
                .count() as u64
        };
        assert_eq!(p.iter().map(words).collect::<Vec<_>>(), vec![8, 400, 1500]);
        // A 1024-token context caps the long prompts (~384 words) and drops duplicates.
        let p = plan("m", ModelType::Thinking, Some(1024));
        assert_eq!(p.iter().map(words).collect::<Vec<_>>(), vec![8, 384]);
        let e = plan("e", ModelType::Embedding, None);
        assert_eq!(e.len(), 2);
        assert_eq!(e[0].path, "/v1/embeddings");
        assert!(plan("r", ModelType::Rerank, None).is_empty());
        assert!(plan("i", ModelType::Image, None).is_empty());
    }
}
