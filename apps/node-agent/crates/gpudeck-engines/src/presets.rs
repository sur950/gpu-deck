//! Per-engine defaults: tool-call / reasoning parsers per model family and the flags behind the
//! `low_latency` / `high_throughput` deployment presets. Explicit `LaunchSpec` values
//! (`tool_parser`, `reasoning_parser`, `launch_flags`, `max_concurrency`) always win.

use serde_json::{json, Value};

use crate::types::{EngineKind, Preset};

/// Normalised model family: lower-case, `_`/space → `-`.
fn fam(family: &str) -> String {
    family.trim().to_ascii_lowercase().replace(['_', ' '], "-")
}

/// Family buckets, most specific first.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Family {
    Qwen3Coder,
    Qwen,
    Llama4,
    Llama3,
    Mistral,
    DeepSeekV31,
    DeepSeekR1,
    DeepSeekV3,
    Kimi,
    Glm,
    GptOss,
    Granite,
    Hermes,
    Phi4,
    Gemma,
    Other,
}

pub fn classify(family: &str) -> Family {
    let f = fam(family);
    let has = |p: &str| f.contains(p);
    if has("qwen3-coder") || has("qwen3coder") {
        Family::Qwen3Coder
    } else if has("qwen") || has("qwq") {
        Family::Qwen
    } else if has("llama4") || has("llama-4") {
        Family::Llama4
    } else if has("llama3") || has("llama-3") || f == "llama" {
        Family::Llama3
    } else if has("mistral")
        || has("mixtral")
        || has("devstral")
        || has("magistral")
        || has("ministral")
    {
        Family::Mistral
    } else if has("deepseek-v3.1") || has("deepseek-v3-1") || has("deepseek-v3.2") {
        Family::DeepSeekV31
    } else if has("deepseek-r1") {
        Family::DeepSeekR1
    } else if has("deepseek") {
        Family::DeepSeekV3
    } else if has("kimi") {
        Family::Kimi
    } else if has("glm") {
        Family::Glm
    } else if has("gpt-oss") || has("gptoss") {
        Family::GptOss
    } else if has("granite") {
        Family::Granite
    } else if has("hermes") {
        Family::Hermes
    } else if has("phi-4") || has("phi4") {
        Family::Phi4
    } else if has("gemma") {
        Family::Gemma
    } else {
        Family::Other
    }
}

/// Default tool-call parser for `engine` and model `family` (`None` = engine needs no flag or
/// has no parser for the family).
///
/// * llama.cpp: tool calls come from the Jinja chat template (`--jinja`), so the "parser" is
///   always `jinja`.
/// * MLX / mlx-audio / whisper.cpp / sd.cpp: none.
pub fn default_tool_parser(engine: EngineKind, family: Option<&str>) -> Option<&'static str> {
    use Family::*;
    match engine {
        EngineKind::LlamaCpp => Some("jinja"),
        EngineKind::Vllm | EngineKind::VllmOmni => Some(match classify(family?) {
            Qwen3Coder => "qwen3_coder",
            Qwen | Hermes => "hermes",
            Llama4 => "llama4_pythonic",
            Llama3 => "llama3_json",
            Mistral => "mistral",
            DeepSeekV31 => "deepseek_v31",
            DeepSeekV3 | DeepSeekR1 => "deepseek_v3",
            Kimi => "kimi_k2",
            Glm => "glm45",
            GptOss => "openai",
            Granite => "granite",
            Phi4 => "phi4_mini_json",
            Gemma => "pythonic",
            Other => return None,
        }),
        EngineKind::Sglang => Some(match classify(family?) {
            Qwen3Coder => "qwen3_coder",
            Qwen | Hermes => "qwen25",
            Llama4 => "llama4",
            Llama3 => "llama3",
            Mistral => "mistral",
            DeepSeekV31 => "deepseekv31",
            DeepSeekV3 | DeepSeekR1 => "deepseekv3",
            Kimi => "kimi_k2",
            Glm => "glm45",
            GptOss => "gpt-oss",
            Gemma | Phi4 | Granite => "pythonic",
            Other => return None,
        }),
        EngineKind::TensorrtLlm => match classify(family?) {
            Qwen3Coder => Some("qwen3_coder"),
            Qwen => Some("qwen3"),
            Kimi => Some("kimi_k2"),
            _ => None,
        },
        _ => None,
    }
}

/// Default reasoning parser for thinking-capable families.
pub fn default_reasoning_parser(engine: EngineKind, family: Option<&str>) -> Option<&'static str> {
    use Family::*;
    let f = classify(family?);
    match engine {
        // llama.cpp: `--reasoning-format deepseek` extracts <think> into reasoning_content for
        // every thinking template; it is harmless for non-thinking models.
        EngineKind::LlamaCpp => Some("deepseek"),
        EngineKind::Vllm | EngineKind::VllmOmni => match f {
            Qwen | Qwen3Coder => Some("qwen3"),
            DeepSeekR1 | DeepSeekV31 => Some("deepseek_r1"),
            Glm => Some("glm45"),
            GptOss => Some("openai_gptoss"),
            Granite => Some("granite"),
            Mistral => Some("mistral"),
            _ => None,
        },
        EngineKind::Sglang => match f {
            Qwen | Qwen3Coder => Some("qwen3"),
            DeepSeekR1 | DeepSeekV31 => Some("deepseek-r1"),
            Glm => Some("glm45"),
            Kimi => Some("kimi"),
            GptOss => Some("gpt-oss"),
            _ => None,
        },
        EngineKind::TensorrtLlm => match f {
            Qwen | Qwen3Coder => Some("qwen3"),
            DeepSeekR1 => Some("deepseek-r1"),
            _ => None,
        },
        _ => None,
    }
}

/// Flag carrying the concurrency limit per engine (`max_concurrency` maps onto it).
pub fn concurrency_flag(engine: EngineKind) -> Option<&'static str> {
    match engine {
        EngineKind::LlamaCpp => Some("--parallel"),
        EngineKind::Vllm | EngineKind::VllmOmni => Some("--max-num-seqs"),
        EngineKind::Sglang | EngineKind::SglangDiffusion => Some("--max-running-requests"),
        EngineKind::TensorrtLlm => Some("--max_batch_size"),
        _ => None,
    }
}

/// Preset → flags (applied as defaults under explicit `launch_flags`).
pub fn preset_flags(engine: EngineKind, preset: Preset) -> Vec<(&'static str, Value)> {
    match (engine, preset) {
        (EngineKind::LlamaCpp, Preset::LowLatency) => vec![
            ("--parallel", json!(1)),
            ("--flash-attn", json!("on")),
            ("--ubatch-size", json!(512)),
        ],
        (EngineKind::LlamaCpp, Preset::HighThroughput) => vec![
            ("--parallel", json!(8)),
            ("--flash-attn", json!("on")),
            ("--cont-batching", json!(true)),
            ("--batch-size", json!(4096)),
            ("--ubatch-size", json!(1024)),
        ],
        (EngineKind::Vllm | EngineKind::VllmOmni, Preset::LowLatency) => vec![
            ("--max-num-seqs", json!(16)),
            ("--max-num-batched-tokens", json!(4096)),
        ],
        (EngineKind::Vllm | EngineKind::VllmOmni, Preset::HighThroughput) => vec![
            ("--max-num-seqs", json!(256)),
            ("--max-num-batched-tokens", json!(16384)),
            ("--enable-chunked-prefill", json!(true)),
            ("--enable-prefix-caching", json!(true)),
        ],
        (EngineKind::Sglang, Preset::LowLatency) => vec![
            ("--max-running-requests", json!(16)),
            ("--chunked-prefill-size", json!(2048)),
        ],
        (EngineKind::Sglang, Preset::HighThroughput) => vec![
            ("--max-running-requests", json!(256)),
            ("--chunked-prefill-size", json!(8192)),
            ("--enable-mixed-chunk", json!(true)),
        ],
        (EngineKind::TensorrtLlm, Preset::LowLatency) => vec![
            ("--max_batch_size", json!(8)),
            ("--max_num_tokens", json!(4096)),
        ],
        (EngineKind::TensorrtLlm, Preset::HighThroughput) => {
            vec![
                ("--max_batch_size", json!(256)),
                ("--max_num_tokens", json!(16384)),
            ]
        }
        (EngineKind::WhisperCpp, Preset::LowLatency) => vec![("--beam-size", json!(1))],
        (EngineKind::WhisperCpp, Preset::HighThroughput) => vec![("--processors", json!(2))],
        _ => Vec::new(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parsers_per_family() {
        assert_eq!(
            default_tool_parser(EngineKind::Vllm, Some("qwen3")),
            Some("hermes")
        );
        assert_eq!(
            default_tool_parser(EngineKind::Vllm, Some("Qwen3-Coder")),
            Some("qwen3_coder")
        );
        assert_eq!(
            default_tool_parser(EngineKind::Sglang, Some("qwen3")),
            Some("qwen25")
        );
        assert_eq!(
            default_tool_parser(EngineKind::Vllm, Some("llama3.1")),
            Some("llama3_json")
        );
        assert_eq!(
            default_tool_parser(EngineKind::Vllm, Some("gpt-oss")),
            Some("openai")
        );
        assert_eq!(
            default_tool_parser(EngineKind::LlamaCpp, None),
            Some("jinja")
        );
        assert_eq!(default_tool_parser(EngineKind::Mlx, Some("qwen3")), None);
        assert_eq!(
            default_tool_parser(EngineKind::Vllm, Some("unknownlm")),
            None
        );
        assert_eq!(
            default_reasoning_parser(EngineKind::Vllm, Some("deepseek-r1")),
            Some("deepseek_r1")
        );
        assert_eq!(
            default_reasoning_parser(EngineKind::Sglang, Some("qwen3")),
            Some("qwen3")
        );
    }

    #[test]
    fn presets_have_concurrency_flag() {
        for e in [
            EngineKind::LlamaCpp,
            EngineKind::Vllm,
            EngineKind::Sglang,
            EngineKind::TensorrtLlm,
        ] {
            let flag = concurrency_flag(e).unwrap();
            for p in [Preset::LowLatency, Preset::HighThroughput] {
                assert!(
                    preset_flags(e, p).iter().any(|(k, _)| *k == flag),
                    "{e} {p:?}"
                );
            }
        }
        assert!(preset_flags(EngineKind::Mlx, Preset::LowLatency).is_empty());
    }
}
