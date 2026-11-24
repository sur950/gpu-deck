//! KV-cache size estimates from `model_versions.config` (`metrics.md` §2 `kv_cache_bytes`):
//! bytes/token = n_layers × n_kv_heads × head_dim × (K bytes + V bytes), or for MLA models
//! (DeepSeek V2/V3) n_layers × (kv_lora_rank + qk_rope_head_dim) × dtype bytes.

use serde_json::{Map, Value};

fn get_u64(cfg: &Map<String, Value>, keys: &[&str]) -> Option<u64> {
    keys.iter().find_map(|k| {
        let v = cfg.get(*k)?;
        v.as_u64()
            .or_else(|| v.as_f64().map(|f| f as u64))
            .or_else(|| v.as_str().and_then(|s| s.parse().ok()))
    })
}

/// Bytes per element for a KV cache type name (llama.cpp `--cache-type-k`, vLLM `--kv-cache-dtype`,
/// SGLang `--kv-cache-dtype`). Block-quantised GGML types include their scale overhead.
pub fn dtype_bytes(name: &str) -> f64 {
    match name.trim().to_ascii_lowercase().as_str() {
        "f32" | "float32" | "fp32" => 4.0,
        "f16" | "float16" | "fp16" | "bf16" | "bfloat16" | "auto" | "half" => 2.0,
        "q8_0" => 34.0 / 32.0,
        "q5_1" => 24.0 / 32.0,
        "q5_0" => 22.0 / 32.0,
        "q4_1" => 20.0 / 32.0,
        "q4_0" | "iq4_nl" => 18.0 / 32.0,
        "fp8" | "fp8_e4m3" | "fp8_e5m2" | "int8" | "i8" => 1.0,
        "fp4" | "nvfp4" | "int4" => 0.5,
        _ => 2.0,
    }
}

/// KV bytes per token, `None` when the config lacks the layer/head geometry.
pub fn bytes_per_token(
    cfg: &Map<String, Value>,
    k_dtype: Option<&str>,
    v_dtype: Option<&str>,
) -> Option<u64> {
    // Nested HF configs (multimodal) keep the LM geometry under `text_config`.
    if let Some(Value::Object(text)) = cfg.get("text_config") {
        if get_u64(cfg, &["num_hidden_layers", "n_layers", "block_count"]).is_none() {
            return bytes_per_token(text, k_dtype, v_dtype);
        }
    }
    let layers = get_u64(
        cfg,
        &["n_layers", "num_hidden_layers", "block_count", "num_layers"],
    )?;
    let kb = dtype_bytes(k_dtype.unwrap_or("f16"));
    let vb = dtype_bytes(v_dtype.or(k_dtype).unwrap_or("f16"));
    if let (Some(rank), Some(rope)) = (
        get_u64(cfg, &["kv_lora_rank"]),
        get_u64(cfg, &["qk_rope_head_dim"]),
    ) {
        return Some((layers as f64 * (rank + rope) as f64 * kb).round() as u64);
    }
    let heads = get_u64(
        cfg,
        &["n_heads", "num_attention_heads", "attention.head_count"],
    );
    let kv_heads = get_u64(
        cfg,
        &[
            "n_kv_heads",
            "num_key_value_heads",
            "attention.head_count_kv",
        ],
    )
    .or(heads)?;
    let head_dim = get_u64(cfg, &["head_dim", "attention.key_length"])
        .or_else(|| Some(get_u64(cfg, &["hidden_size", "n_embd", "embedding_length"])? / heads?))?;
    Some((layers as f64 * kv_heads as f64 * head_dim as f64 * (kb + vb)).round() as u64)
}

/// Total KV bytes for `tokens` (whole engine: context × slots).
pub fn total_bytes(bytes_per_token: u64, tokens: u64) -> u64 {
    bytes_per_token.saturating_mul(tokens)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cfg(v: Value) -> Map<String, Value> {
        v.as_object().unwrap().clone()
    }

    #[test]
    fn qwen3_0_6b() {
        // 28 layers, 8 KV heads, head_dim 128, f16: 28*8*128*4 = 114688 B/token.
        let c = cfg(
            serde_json::json!({"num_hidden_layers": 28, "num_attention_heads": 16, "num_key_value_heads": 8, "head_dim": 128}),
        );
        assert_eq!(bytes_per_token(&c, None, None), Some(114_688));
        // q8_0 K/V: 28*8*128*2*34/32
        assert_eq!(bytes_per_token(&c, Some("q8_0"), None), Some(60_928));
    }

    #[test]
    fn head_dim_from_hidden_and_mla() {
        let c = cfg(serde_json::json!({"n_layers": 32, "n_heads": 32, "hidden_size": 4096}));
        assert_eq!(bytes_per_token(&c, None, None), Some(32 * 32 * 128 * 4));
        let ds = cfg(
            serde_json::json!({"num_hidden_layers": 61, "kv_lora_rank": 512, "qk_rope_head_dim": 64}),
        );
        assert_eq!(bytes_per_token(&ds, Some("bf16"), None), Some(61 * 576 * 2));
        let nested = cfg(
            serde_json::json!({"text_config": {"num_hidden_layers": 2, "num_key_value_heads": 1, "head_dim": 4}}),
        );
        assert_eq!(bytes_per_token(&nested, None, None), Some(2 * 4 * 4));
        assert_eq!(
            bytes_per_token(&cfg(serde_json::json!({})), None, None),
            None
        );
    }
}
