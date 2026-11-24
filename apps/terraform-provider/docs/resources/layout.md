---
page_title: "gpudeck_layout Resource"
---

# gpudeck_layout (Resource)

Named GPU layout (for example Day / Night): which deployments run where, with replicas, slices and traffic weights. Apply it with `gpudeck layouts apply` or a time_schedule rule.

## Example Usage

```terraform
# Day: the big chat model on two GPUs. Night: one replica plus an embedding model.
resource "gpudeck_layout" "day" {
  name            = "day"
  unlisted_policy = "unload"
  items = [
    { deployment_id = gpudeck_deployment.qwen3.id, replicas = 2 },
  ]
}

resource "gpudeck_layout" "night" {
  name            = "night"
  unlisted_policy = "unload"
  items = [
    { deployment_id = gpudeck_deployment.qwen3.id, replicas = 1, traffic_weight = 100 },
    {
      deployment_id     = gpudeck_deployment.embed.id
      slice_vram_bytes  = 4294967296
      slice_compute_pct = 25
    },
  ]
}
```

## Schema

### Required

- `items` (Attributes List) Placements.
  - `deployment_id` (String, Required) Deployment.
  - `device_ids` (List of String, Optional) Pin to devices.
  - `node_id` (String, Optional) Pin to a node.
  - `position` (Number, Optional) Order (defaults to list order).
  - `replicas` (Number, Optional) Replicas (0-64). Server default: `1`.
  - `slice_compute_pct` (Number, Optional) GPU slice compute share (1-100).
  - `slice_vram_bytes` (Number, Optional) GPU slice size in bytes.
  - `traffic_weight` (Number, Optional) Traffic weight (0-1000). Server default: `100`.
- `name` (String) Layout name.

### Optional

- `description` (String) Free-form description.
- `unlisted_policy` (String) What happens to deployments not listed when the layout is applied. One of: `keep`, `unload`.

### Read-Only

- `applied_at` (String) Last apply.
- `created_at` (String) Creation time (RFC 3339).
- `id` (String) Object UUID.
- `is_active` (Boolean) Layout currently applied.
- `updated_at` (String) Last update time (RFC 3339).

## Import

```shell
# Import by UUID.
terraform import gpudeck_layout.example 00000000-0000-0000-0000-000000000000
```

Attributes equal to their server default are left unset after import.
