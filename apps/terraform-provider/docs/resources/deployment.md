---
page_title: "gpudeck_deployment Resource"
---

# gpudeck_deployment (Resource)

A model deployment: engine, placement (spread/pack, GPU slices, TP/PP), replicas and launch settings. Deleting is asynchronous; the provider waits until the deployment is gone (up to `wait_timeout_seconds`).

## Example Usage

```terraform
resource "gpudeck_deployment" "qwen3" {
  name               = "qwen3-8b"
  model_id           = gpudeck_model.qwen3.id
  cluster_id         = gpudeck_cluster.office.id
  replicas           = 2
  max_replicas       = 4
  gpu_count          = 1
  placement_strategy = "spread"
  context_size       = 32768
  launch_flags = jsonencode({
    "--flash-attn" = true
  })
  env_vars = {
    LLAMA_ARG_N_PARALLEL = "4"
  }
  wait_for_ready       = true
  wait_timeout_seconds = 900
}
```

## Schema

### Required

- `model_id` (String) Model to serve. Changing this replaces the resource.
- `name` (String) Deployment name (the model name clients call unless aliases are used).

### Optional

- `cluster_id` (String) Restrict placement to a cluster.
- `context_size` (Number) Context size in tokens.
- `control_vectors` (String, JSON) Control vectors (array of objects). JSON-encoded (use `jsonencode`).
- `description` (String) Free-form description.
- `device_vendor_filter` (List of String) Only place on these vendors. One of: `nvidia`, `amd`, `hygon`, `metax`, `moorethreads`, `thead`, `apple`.
- `engine_version_id` (String) Engine version (default: auto-pick).
- `env_vars` (Map of String) Environment variables for the engine.
- `gpu_count` (Number) GPUs per replica. Server default: `1`.
- `kv_sharing` (Boolean) Shared KV cache. Server default: `false`.
- `kv_sharing_config` (String, JSON) Shared KV cache settings. JSON-encoded (use `jsonencode`).
- `launch_flags` (String, JSON) Engine flags: {"flag": value}; true = bare flag. JSON-encoded (use `jsonencode`).
- `max_replicas` (Number) Upper bound for automation rules. Server default: `1`.
- `min_replicas` (Number) Lower bound for automation rules. Server default: `0`.
- `model_version_id` (String) Model version (default: the model's default version).
- `node_selector` (String, JSON) Node label selector. JSON-encoded (use `jsonencode`).
- `pd_config` (String, JSON) Prefill/decode split settings. JSON-encoded (use `jsonencode`).
- `pd_split` (Boolean) Prefill/decode split. Server default: `false`.
- `placement_strategy` (String) spread or pack. One of: `spread`, `pack`.
- `pp_size` (Number) Pipeline parallel size. Server default: `1`.
- `preset` (String) Tuning preset. One of: `low_latency`, `high_throughput`.
- `priority_override` (String) Override the model priority. One of: `always_on`, `normal`, `evictable`.
- `replicas` (Number) Desired replicas. Server default: `1`.
- `running` (Boolean) Desired run state; false stops all instances (default true).
- `slice_compute_pct` (Number) GPU slice compute share (1-100).
- `slice_vram_bytes` (Number) GPU slice size per replica in bytes.
- `spec_decoding` (String, JSON) Speculative decoding settings. JSON-encoded (use `jsonencode`).
- `tool_parser` (String) Tool-call parser (default: the model's).
- `tp_size` (Number) Tensor parallel size. Server default: `1`.
- `traffic_config` (String, JSON) Load balancing / failover settings. JSON-encoded (use `jsonencode`).
- `wait_for_ready` (Boolean) After create/update wait until status is `running` (default false).
- `wait_timeout_seconds` (Number) Timeout for waits, including delete (default 600).

### Read-Only

- `created_at` (String) Creation time (RFC 3339).
- `engine_name` (String) Engine picked.
- `engine_version` (String) Engine version picked.
- `id` (String) Object UUID.
- `model_name` (String) Model name.
- `ready_replicas` (Number) Replicas ready to serve.
- `status` (String) pending, scheduling, deploying, running, degraded, scaling, stopped, failed or deleting.
- `status_message` (String) Detail for the status.
- `updated_at` (String) Last update time (RFC 3339).

## Import

```shell
# Import by UUID.
terraform import gpudeck_deployment.example 00000000-0000-0000-0000-000000000000
```

Attributes equal to their server default are left unset after import.
