resource "gpudeck_alert_channel" "slack" {
  name         = "ops-slack"
  type         = "slack"
  config       = jsonencode({ channel = "#gpu-ops" })
  secret       = var.slack_bot_token # stored in the vault, never read back
  min_severity = "warning"
  event_filter = ["node.offline", "instance.crashed", "swap.failed"]
}

variable "slack_bot_token" {
  type      = string
  sensitive = true
}
