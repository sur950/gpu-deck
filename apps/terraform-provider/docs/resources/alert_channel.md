---
page_title: "gpudeck_alert_channel Resource"
---

# gpudeck_alert_channel (Resource)

Slack, email or webhook destination for alerts.

## Example Usage

```terraform
resource "gpudeck_alert_channel" "slack" {
  name         = "ops-slack"
  type         = "slack"
  config       = jsonencode({ channel = "#gpu-ops" })
  secret       = var.slack_bot_token # stored in the vault, never read back
  min_severity = "warning"
  event_filter = ["node.offline", "instance.crashed", "swap.failed"]
}

variable "slack_bot_token" {
  type      = string
  sensitive = true
}
```

## Schema

### Required

- `config` (String, JSON) Channel settings (slack {channel}, email {to, from}, webhook {url, headers}). JSON-encoded.
- `name` (String) Channel name.
- `type` (String) Channel type. One of: `slack`, `email`, `webhook`. Changing this replaces the resource.

### Optional

- `credential_id` (String) Vault credential holding the secret (set by the server when `secret` is given).
- `enabled` (Boolean) Deliver alerts. Server default: `true`.
- `event_filter` (List of String) Event types delivered (empty = all).
- `min_severity` (String) Lowest severity delivered. One of: `info`, `warning`, `critical`.
- `secret` (String, Sensitive) Webhook URL with token / SMTP password / bot token, stored in the vault and never read back from the API; Terraform keeps the configured value in state (sensitive).

### Read-Only

- `created_at` (String) Creation time (RFC 3339).
- `id` (String) Object UUID.
- `last_error` (String) Last delivery error.
- `last_test_at` (String) Last test delivery.
- `last_test_ok` (Boolean) Whether the last test succeeded.
- `updated_at` (String) Last update time (RFC 3339).

## Import

```shell
# Import by UUID.
terraform import gpudeck_alert_channel.example 00000000-0000-0000-0000-000000000000
```

Attributes equal to their server default are left unset after import.
