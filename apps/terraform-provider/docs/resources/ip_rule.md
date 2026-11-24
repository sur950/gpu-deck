---
page_title: "gpudeck_ip_rule Resource"
---

# gpudeck_ip_rule (Resource)

IP allow/block rule for the admin API (`admin`) or the serving API (`api_layer`). Block wins; any allow rule turns its scope into allow-list mode.

## Example Usage

```terraform
# Only the office network may reach the admin API.
resource "gpudeck_ip_rule" "office_admin" {
  action      = "allow"
  scope       = "admin"
  cidr        = "203.0.113.0/24"
  description = "Office VPN"
}

resource "gpudeck_ip_rule" "abuser" {
  action     = "block"
  scope      = "api_layer"
  cidr       = "198.51.100.7"
  expires_at = "2027-01-01T00:00:00Z"
}
```

## Schema

### Required

- `action` (String) allow or block. One of: `allow`, `block`.
- `cidr` (String) IP address or CIDR.
- `scope` (String) Which API the rule applies to. One of: `admin`, `api_layer`.

### Optional

- `description` (String) Free-form description.
- `enabled` (Boolean) Rule is active. Server default: `true`.
- `expires_at` (String) Expiry time (RFC 3339).

### Read-Only

- `created_at` (String) Creation time (RFC 3339).
- `id` (String) Object UUID.
- `updated_at` (String) Last update time (RFC 3339).

## Import

```shell
# Import by UUID.
terraform import gpudeck_ip_rule.example 00000000-0000-0000-0000-000000000000
```

Attributes equal to their server default are left unset after import.
