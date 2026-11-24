resource "gpudeck_api_key" "gateway" {
  name        = "gateway"
  description = "Used by the AI gateway in front of GPU Deck"
  metadata    = jsonencode({ team = "platform" })
}

output "gateway_api_key" {
  value     = gpudeck_api_key.gateway.key
  sensitive = true
}
