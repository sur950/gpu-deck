---
page_title: "gpudeck_join_token Resource"
---

# gpudeck_join_token (Resource)

Token that lets machines join a cluster (`gpudeck-agent join`): one-time by default, or a multi-use enrolment token (`max_uses` > 1) for Kubernetes DaemonSets and node pools. Destroying the resource revokes the token if it can still be used. A token revoked outside Terraform is created again on the next apply.

## Example Usage

```terraform
resource "gpudeck_join_token" "mac_studio" {
  cluster_id     = gpudeck_cluster.office.id
  node_name_hint = "mac-studio-1"
  ttl_minutes    = 60
  labels = {
    role = "apple"
  }
}

# Run on the machine: the command contains the one-time token.
output "join_command" {
  value     = gpudeck_join_token.mac_studio.join_command
  sensitive = true
}

# One multi-use enrolment token for a Kubernetes GPU pool (the Helm chart's
# nodeAgent.joinTokenSecret): up to 100 machines, valid 30 days.
resource "gpudeck_join_token" "k8s_pool" {
  cluster_id  = gpudeck_cluster.office.id
  max_uses    = 100
  ttl_minutes = 43200
}
```

## Schema

### Required

- `cluster_id` (String) Cluster the machine joins. Changing this replaces the resource.

### Optional

- `labels` (Map of String) Labels applied to the node on join. Changing this replaces the resource.
- `max_uses` (Number) Machines the token may enrol (1 = one-time token; node_name_hint must be empty when > 1). Server default: `1`. Changing this replaces the resource.
- `node_name_hint` (String) Name to give the node. Changing this replaces the resource.
- `ttl_minutes` (Number) Token lifetime in minutes (1-1440 for a one-time token, up to 525600 for a multi-use one). Server default: `15`. Changing this replaces the resource. Only used when the resource is created.

### Read-Only

- `ca_fingerprint_sha256` (String) SHA-256 fingerprint of the internal CA the agent should pin.
- `created_at` (String) Creation time (RFC 3339).
- `expires_at` (String) Expiry time.
- `id` (String) Object UUID.
- `install_command` (String, Sensitive) One-line installer for a GPU machine; it talks only to the agent hub (contains the token).
- `join_command` (String, Sensitive) Ready-to-run agent join command (contains the token).
- `token` (String, Sensitive) The token (only known to the run that created it).
- `token_prefix` (String) Non-secret token prefix.
- `use_count` (Number) Machines that joined with the token so far.
- `used_at` (String) When the token was used up (its last allowed join).
- `used_by_node_id` (String) Node created by the most recent join.

