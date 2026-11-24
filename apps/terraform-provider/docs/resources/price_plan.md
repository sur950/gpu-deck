---
page_title: "gpudeck_price_plan Resource"
---

# gpudeck_price_plan (Resource)

USD prices per GPU minute and per 1k tokens, attached to clusters or nodes.

## Example Usage

```terraform
resource "gpudeck_price_plan" "standard" {
  name                     = "standard"
  usd_per_gpu_minute       = 0.03
  usd_per_1k_input_tokens  = 0.0002
  usd_per_1k_output_tokens = 0.0006
  is_default               = true
}
```

## Schema

### Required

- `name` (String) Plan name (unique).

### Optional

- `description` (String) Free-form description.
- `is_default` (Boolean) Use this plan where no cluster/node plan is set. Server default: `false`.
- `usd_per_1k_cached_tokens` (Number) USD per 1k cached input tokens. Server default: `0`.
- `usd_per_1k_input_tokens` (Number) USD per 1k input tokens. Server default: `0`.
- `usd_per_1k_output_tokens` (Number) USD per 1k output tokens. Server default: `0`.
- `usd_per_gpu_minute` (Number) USD per GPU minute of instance uptime. Server default: `0`.

### Read-Only

- `created_at` (String) Creation time (RFC 3339).
- `currency` (String) Always USD.
- `id` (String) Object UUID.
- `updated_at` (String) Last update time (RFC 3339).

## Import

```shell
# Import by UUID.
terraform import gpudeck_price_plan.example 00000000-0000-0000-0000-000000000000
```

Attributes equal to their server default are left unset after import.
