---
page_title: "gpudeck_model Resource"
---

# gpudeck_model (Resource)

A registered model (Hugging Face, ModelScope or a local path such as Ollama / LM Studio files).

## Example Usage

```terraform
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
```

## Schema

### Required

- `name` (String) Unique name served to clients (`[A-Za-z0-9._:-]+`).
- `repo_id` (String) Repository id (org/name) or absolute local path. Changing this replaces the resource.
- `source` (String) Where the files come from. One of: `huggingface`, `modelscope`, `local_path`. Changing this replaces the resource.
- `type` (String) Model type. One of: `chat`, `thinking`, `vision`, `embedding`, `rerank`, `stt`, `tts`, `image`, `video`.

### Optional

- `aliases` (List of String) Extra names clients may use.
- `allow_pickle` (Boolean) Allow pickle weight files. Server default: `false`.
- `default_context_size` (Number) Default context size in tokens.
- `default_launch_flags` (String, JSON) Engine flags applied to every deployment of the model. JSON-encoded (use `jsonencode`).
- `description` (String) Free-form description.
- `display_name` (String) Display name.
- `family` (String) Model family (qwen3, gemma3, ...; default: detected).
- `initial_version` (String, JSON) ModelVersionCreate object for the first version (default: version `main` with all files). JSON-encoded. Only used when the resource is created.
- `metadata` (String, JSON) Arbitrary metadata. JSON-encoded (use `jsonencode`).
- `priority` (String) Eviction priority. One of: `always_on`, `normal`, `evictable`.
- `tags` (List of String) Tags.
- `tool_parser_default` (String) Default tool-call parser (default: detected).
- `trust_remote_code` (Boolean) Allow remote code (vLLM/SGLang). Server default: `false`.

### Read-Only

- `created_at` (String) Creation time (RFC 3339).
- `deployment_count` (Number) Number of deployments using the model.
- `id` (String) Object UUID.
- `updated_at` (String) Last update time (RFC 3339).
- `version_count` (Number) Number of versions.

## Import

```shell
# Import by UUID.
terraform import gpudeck_model.example 00000000-0000-0000-0000-000000000000
```

Attributes equal to their server default are left unset after import.
