resource "gpudeck_deployment" "qwen3" {
  name               = "qwen3-8b"
  model_id           = gpudeck_model.qwen3.id
  cluster_id         = gpudeck_cluster.office.id
  replicas           = 2
  max_replicas       = 4
  gpu_count          = 1
  placement_strategy = "spread"
  context_size       = 32768
  launch_flags = jsonencode({
    "--flash-attn" = true
  })
  env_vars = {
    LLAMA_ARG_N_PARALLEL = "4"
  }
  wait_for_ready       = true
  wait_timeout_seconds = 900
}
