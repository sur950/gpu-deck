resource "gpudeck_model" "qwen3" {
  name     = "qwen3-8b"
  source   = "huggingface"
  repo_id  = "Qwen/Qwen3-8B-GGUF"
  type     = "thinking"
  family   = "qwen3"
  priority = "always_on"
  aliases  = ["qwen3"]
  initial_version = jsonencode({
    version      = "q4_k_m"
    quantization = "Q4_K_M"
    files        = ["Qwen3-8B-Q4_K_M.gguf"]
    is_default   = true
  })
}
