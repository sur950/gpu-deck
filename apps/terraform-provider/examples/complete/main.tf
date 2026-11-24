# A small office GPU fleet: one cluster, a Mac joining it, two models, day/night layouts with
# schedule rules, idle unload, Slack alerts and an API key for your AI gateway.
#
#   export GPUDECK_ENDPOINT=http://localhost:8180 GPUDECK_TOKEN="$(...)"   # or USERNAME/PASSWORD
#   terraform init && terraform apply -var 'admin_allow_cidrs=["10.0.0.0/8"]'
#
# Point it at an empty control plane: it sets a default price plan, adds schedule and idle-unload
# rules and creates deployments named qwen3-0.6b / nomic-embed.

terraform {
  required_providers {
    gpudeck = {
      source = "sur950/gpudeck"
    }
  }
}

provider "gpudeck" {}

variable "slack_bot_token" {
  type      = string
  sensitive = true
  default   = null
}

resource "gpudeck_price_plan" "standard" {
  name                     = "standard"
  usd_per_gpu_minute       = 0.03
  usd_per_1k_input_tokens  = 0.0002
  usd_per_1k_output_tokens = 0.0006
  is_default               = true
}

resource "gpudeck_cluster" "office" {
  name          = "office"
  type          = "bare_metal"
  price_plan_id = gpudeck_price_plan.standard.id
  labels        = { site = "office" }
}

resource "gpudeck_join_token" "mac" {
  cluster_id     = gpudeck_cluster.office.id
  node_name_hint = "mac-studio-1"
  ttl_minutes    = 60
}

data "gpudeck_engines" "apple" {
  vendor  = "apple"
  enabled = true
}

resource "gpudeck_model" "qwen3" {
  name     = "qwen3-0.6b"
  source   = "huggingface"
  repo_id  = "Qwen/Qwen3-0.6B-GGUF"
  type     = "chat"
  priority = "always_on"
}

resource "gpudeck_model" "embed" {
  name     = "nomic-embed-text-v1.5"
  source   = "huggingface"
  repo_id  = "nomic-ai/nomic-embed-text-v1.5-GGUF"
  type     = "embedding"
  priority = "evictable"
}

resource "gpudeck_deployment" "qwen3" {
  name       = "qwen3-0.6b"
  model_id   = gpudeck_model.qwen3.id
  cluster_id = gpudeck_cluster.office.id
  replicas   = 1
}

resource "gpudeck_deployment" "embed" {
  name       = "nomic-embed"
  model_id   = gpudeck_model.embed.id
  cluster_id = gpudeck_cluster.office.id
}

resource "gpudeck_layout" "day" {
  name            = "day"
  unlisted_policy = "keep"
  items = [
    { deployment_id = gpudeck_deployment.qwen3.id, replicas = 1 },
    { deployment_id = gpudeck_deployment.embed.id, replicas = 1 },
  ]
}

resource "gpudeck_layout" "night" {
  name            = "night"
  unlisted_policy = "unload"
  items = [
    { deployment_id = gpudeck_deployment.qwen3.id, replicas = 1 },
  ]
}

resource "gpudeck_rule" "morning" {
  name = "day layout"
  type = "time_schedule"
  params = jsonencode({
    layout_id = gpudeck_layout.day.id
    at        = "08:00"
    days      = ["mon", "tue", "wed", "thu", "fri"]
    timezone  = "Asia/Kolkata"
  })
}

resource "gpudeck_rule" "evening" {
  name = "night layout"
  type = "time_schedule"
  params = jsonencode({
    layout_id = gpudeck_layout.night.id
    at        = "20:00"
    timezone  = "Asia/Kolkata"
  })
}

resource "gpudeck_rule" "idle" {
  name   = "unload idle evictable models"
  type   = "idle_unload"
  params = jsonencode({ idle_minutes = 30 })
  target = jsonencode({ priorities = ["evictable"] })
}

resource "gpudeck_alert_channel" "slack" {
  count        = var.slack_bot_token == null ? 0 : 1
  name         = "ops-slack"
  type         = "slack"
  config       = jsonencode({ channel = "#gpu-ops" })
  secret       = var.slack_bot_token
  min_severity = "warning"
}

# Any allow rule switches the admin scope to allow-list mode: every other address is refused,
# including the machine running Terraform. List the networks admins (and this Terraform run)
# come from, e.g. ["10.0.0.0/8", "192.168.1.0/24"]; empty = no admin allow-list. The API refuses
# (409 SELF_LOCKOUT) to delete the rule that admits the caller while other allow rules remain, so
# before `terraform destroy` shrink the list to the one CIDR you connect from.
variable "admin_allow_cidrs" {
  type    = list(string)
  default = []
}

resource "gpudeck_ip_rule" "admin_allow" {
  for_each = toset(var.admin_allow_cidrs)
  action   = "allow"
  scope    = "admin"
  cidr     = each.value
}

resource "gpudeck_api_key" "gateway" {
  name = "gateway"
}

output "join_command" {
  value     = gpudeck_join_token.mac.join_command
  sensitive = true
}

output "gateway_api_key" {
  value     = gpudeck_api_key.gateway.key
  sensitive = true
}

output "apple_engines" {
  value = [for e in data.gpudeck_engines.apple.engines : e.name]
}
