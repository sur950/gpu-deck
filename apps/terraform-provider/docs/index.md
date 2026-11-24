---
page_title: "gpudeck provider"
---

# gpudeck provider

Manage a GPU Deck control plane (clusters, models, deployments, rules, layouts, alerts, API keys, IP rules) through its admin API.

Authentication: a session token (`token` / `GPUDECK_TOKEN`, e.g. from `gpudeck login`) or `username` + `password`, which the provider exchanges for a session token at start-up. Requests use `Authorization: Bearer`, so no CSRF token is needed.

## Example Usage

```terraform
terraform {
  required_providers {
    gpudeck = {
      source = "sur950/gpudeck"
    }
  }
}

# Credentials can also come from GPUDECK_ENDPOINT, GPUDECK_USERNAME, GPUDECK_PASSWORD
# or GPUDECK_TOKEN (a session token from `gpudeck login`).
provider "gpudeck" {
  endpoint = "https://gpudeck.example.com"
  username = "admin"
  password = var.gpudeck_password
}

variable "gpudeck_password" {
  type      = string
  sensitive = true
}
```

## Schema

### Optional

- `endpoint` (String) 
- `insecure_skip_verify` (Boolean) 
- `password` (String, Sensitive) 
- `timeout_seconds` (Number) 
- `token` (String, Sensitive) 
- `username` (String) 

## Resources

- [`gpudeck_cluster`](resources/cluster.md)
- [`gpudeck_price_plan`](resources/price_plan.md)
- [`gpudeck_join_token`](resources/join_token.md)
- [`gpudeck_model`](resources/model.md)
- [`gpudeck_deployment`](resources/deployment.md)
- [`gpudeck_layout`](resources/layout.md)
- [`gpudeck_rule`](resources/rule.md)
- [`gpudeck_alert_channel`](resources/alert_channel.md)
- [`gpudeck_api_key`](resources/api_key.md)
- [`gpudeck_ip_rule`](resources/ip_rule.md)

## Data sources

- [`gpudeck_nodes`](data-sources/nodes.md)
- [`gpudeck_devices`](data-sources/devices.md)
- [`gpudeck_engines`](data-sources/engines.md)
