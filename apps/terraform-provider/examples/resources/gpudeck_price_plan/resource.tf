resource "gpudeck_price_plan" "standard" {
  name                     = "standard"
  usd_per_gpu_minute       = 0.03
  usd_per_1k_input_tokens  = 0.0002
  usd_per_1k_output_tokens = 0.0006
  is_default               = true
}
