---
page_title: "gpudeck_devices Data Source"
---

# gpudeck_devices (Data Source)

Accelerators (GPUs, Apple silicon) reported by node agents.

## Example Usage

```terraform
data "gpudeck_devices" "big_nvidia" {
  vendor              = "nvidia"
  health              = "healthy"
  free_vram_min_bytes = 40 * 1024 * 1024 * 1024
}

output "big_gpu_count" {
  value = length(data.gpudeck_devices.big_nvidia.devices)
}
```

## Schema

### Optional

- `cluster_id` (String) Only devices in this cluster.
- `free_vram_min_bytes` (Number) Only devices with at least this much free VRAM.
- `health` (String) Only this health state. One of: `healthy`, `degraded`, `unhealthy`, `unknown`.
- `node_id` (String) Only devices on this node.
- `vendor` (String) Only this vendor. One of: `nvidia`, `amd`, `hygon`, `metax`, `moorethreads`, `thead`, `apple`.

### Read-Only

- `devices` (Attributes List) 
  - `cluster_id` (String) Cluster UUID.
  - `compute_capability` (String) Compute capability.
  - `driver_version` (String) Driver version.
  - `health` (String) Health.
  - `id` (String) Device UUID.
  - `index` (Number) Index on the node.
  - `is_unified_memory` (Boolean) Unified memory device.
  - `labels` (Map of String) Labels.
  - `name` (String) Model name.
  - `node_id` (String) Node UUID.
  - `node_name` (String) Node name.
  - `uuid` (String) Vendor device UUID.
  - `vendor` (String) Vendor.
  - `vram_reserved_bytes` (Number) VRAM reserved by slices and instances.
  - `vram_total_bytes` (Number) Total VRAM (unified memory on Apple).
- `id` (String) 

