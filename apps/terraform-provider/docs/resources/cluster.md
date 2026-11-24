---
page_title: "gpudeck_cluster Resource"
---

# gpudeck_cluster (Resource)

A cluster of GPU machines (bare metal, VM, Kubernetes or cloud).

## Example Usage

```terraform
resource "gpudeck_cluster" "office" {
  name             = "office"
  type             = "bare_metal"
  description      = "Rack in the Bengaluru office"
  region           = "blr"
  price_plan_id    = gpudeck_price_plan.standard.id
  egress_allowlist = ["huggingface.co:443", "cdn-lfs.huggingface.co:443"]
  labels = {
    site = "blr"
  }
}
```

## Schema

### Required

- `name` (String) Cluster name (unique).
- `type` (String) Where the machines run. One of: `bare_metal`, `vm`, `kubernetes`, `cloud_aws`, `cloud_azure`, `cloud_gcp`, `cloud_do`.

### Optional

- `config` (String, JSON) Provider settings: kubernetes {namespace, kubeconfig_credential_id}, cloud {vpc, subnet, instance_types, ssh_key_credential_id}. JSON-encoded (use `jsonencode`).
- `credential_id` (String) Vault credential used to provision machines (cloud / Kubernetes).
- `description` (String) Free-form description.
- `egress_allowlist` (List of String) host:port destinations nodes may reach besides the control plane.
- `labels` (Map of String) Labels.
- `price_plan_id` (String) Price plan for usage on this cluster (nodes may override).
- `region` (String) Region or site label.
- `status` (String) active or disabled. One of: `active`, `disabled`.

### Read-Only

- `created_at` (String) Creation time (RFC 3339).
- `id` (String) Object UUID.
- `updated_at` (String) Last update time (RFC 3339).

## Import

```shell
# Import by UUID.
terraform import gpudeck_cluster.example 00000000-0000-0000-0000-000000000000
```

Attributes equal to their server default are left unset after import.
