---
page_title: "gpudeck_nodes Data Source"
---

# gpudeck_nodes (Data Source)

GPU machines registered with the control plane.

## Example Usage

```terraform
data "gpudeck_nodes" "online_mac" {
  cluster_id = gpudeck_cluster.office.id
  status     = "online"
  os         = "macos"
  labels     = ["role=apple"]
}

output "mac_nodes" {
  value = [for n in data.gpudeck_nodes.online_mac.nodes : n.name]
}
```

## Schema

### Optional

- `cluster_id` (String) Only nodes in this cluster.
- `labels` (List of String) `key=value` label filters (all must match).
- `os` (String) Only nodes with this OS. One of: `linux`, `macos`.
- `search` (String) Substring match on the name.
- `status` (String) Only nodes with this status. One of: `pending`, `online`, `offline`, `unhealthy`, `draining`, `drained`, `removed`.

### Read-Only

- `id` (String) 
- `nodes` (Attributes List) 
  - `agent_version` (String) Node agent version.
  - `arch` (String) CPU architecture.
  - `cluster_id` (String) Cluster UUID.
  - `cpu_cores` (Number) CPU cores.
  - `device_count` (Number) Accelerators.
  - `hostname` (String) Hostname.
  - `id` (String) Node UUID.
  - `instance_count` (Number) Running instances.
  - `ip_address` (String) IP address.
  - `labels` (Map of String) Labels.
  - `last_heartbeat_at` (String) Last heartbeat.
  - `memory_total_bytes` (Number) System memory.
  - `name` (String) Node name.
  - `os` (String) Operating system.
  - `price_plan_id` (String) Node price plan override.
  - `proxy_address` (String) Agent proxy address.
  - `proxy_port` (Number) Agent proxy port.
  - `status` (String) Node status.

