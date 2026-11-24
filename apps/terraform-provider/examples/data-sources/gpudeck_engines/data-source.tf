data "gpudeck_engines" "apple_chat" {
  vendor     = "apple"
  model_type = "chat"
  enabled    = true
}

output "apple_chat_engines" {
  value = [for e in data.gpudeck_engines.apple_chat.engines : e.name]
}
