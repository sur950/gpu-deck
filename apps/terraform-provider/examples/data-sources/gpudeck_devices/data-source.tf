data "gpudeck_devices" "big_nvidia" {
  vendor              = "nvidia"
  health              = "healthy"
  free_vram_min_bytes = 40 * 1024 * 1024 * 1024
}

output "big_gpu_count" {
  value = length(data.gpudeck_devices.big_nvidia.devices)
}
