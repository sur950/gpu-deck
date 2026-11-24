# Day: the big chat model on two GPUs. Night: one replica plus an embedding model.
resource "gpudeck_layout" "day" {
  name            = "day"
  unlisted_policy = "unload"
  items = [
    { deployment_id = gpudeck_deployment.qwen3.id, replicas = 2 },
  ]
}

resource "gpudeck_layout" "night" {
  name            = "night"
  unlisted_policy = "unload"
  items = [
    { deployment_id = gpudeck_deployment.qwen3.id, replicas = 1, traffic_weight = 100 },
    {
      deployment_id     = gpudeck_deployment.embed.id
      slice_vram_bytes  = 4294967296
      slice_compute_pct = 25
    },
  ]
}
