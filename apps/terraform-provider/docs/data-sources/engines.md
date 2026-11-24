---
page_title: "gpudeck_engines Data Source"
---

# gpudeck_engines (Data Source)

Inference engines in the registry (llama.cpp, vLLM, SGLang, MLX, ...).

## Example Usage

```terraform
data "gpudeck_engines" "apple_chat" {
  vendor     = "apple"
  model_type = "chat"
  enabled    = true
}

output "apple_chat_engines" {
  value = [for e in data.gpudeck_engines.apple_chat.engines : e.name]
}
```

## Schema

### Optional

- `enabled` (Boolean) Only enabled (true) or disabled (false) engines.
- `model_type` (String) Only engines that serve this model type. One of: `chat`, `thinking`, `vision`, `embedding`, `rerank`, `stt`, `tts`, `image`, `video`.
- `vendor` (String) Only engines that support this vendor. One of: `nvidia`, `amd`, `hygon`, `metax`, `moorethreads`, `thead`, `apple`.

### Read-Only

- `engines` (Attributes List) 
  - `description` (String) Description.
  - `display_name` (String) Display name.
  - `id` (String) Engine UUID.
  - `is_builtin` (Boolean) Shipped with GPU Deck.
  - `is_enabled` (Boolean) Enabled.
  - `name` (String) Engine name.
  - `runtime` (String) native or container.
  - `supported_formats` (List of String) Model file formats.
  - `supported_model_types` (List of String) Model types.
  - `supported_os` (List of String) Operating systems.
  - `supported_vendors` (List of String) Device vendors.
- `id` (String) 

