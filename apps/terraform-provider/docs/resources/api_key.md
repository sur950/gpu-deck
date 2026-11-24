---
page_title: "gpudeck_api_key Resource"
---

# gpudeck_api_key (Resource)

API key for the serving API (used by your AI gateway or any client). The key is only returned when created; it is kept in state as a sensitive value.

## Example Usage

```terraform
resource "gpudeck_api_key" "gateway" {
  name        = "gateway"
  description = "Used by the AI gateway in front of GPU Deck"
  metadata    = jsonencode({ team = "platform" })
}

output "gateway_api_key" {
  value     = gpudeck_api_key.gateway.key
  sensitive = true
}
```

## Schema

### Required

- `name` (String) Key name.

### Optional

- `description` (String) Free-form description.
- `expires_at` (String) Expiry time (RFC 3339); null = never.
- `metadata` (String, JSON) Arbitrary metadata. JSON-encoded (use `jsonencode`).

### Read-Only

- `created_at` (String) Creation time (RFC 3339).
- `id` (String) Object UUID.
- `key` (String, Sensitive) The full key (`gd-...`).
- `key_prefix` (String) Non-secret key prefix.
- `last_used_at` (String) Last use.
- `status` (String) active, expired, revoked or suspended.
- `total_requests` (Number) Requests made with the key.
- `updated_at` (String) Last update time (RFC 3339).

## Import

```shell
# Import by UUID.
terraform import gpudeck_api_key.example 00000000-0000-0000-0000-000000000000
```

Attributes equal to their server default are left unset after import.
