resource "gpudeck_join_token" "mac_studio" {
  cluster_id     = gpudeck_cluster.office.id
  node_name_hint = "mac-studio-1"
  ttl_minutes    = 60
  labels = {
    role = "apple"
  }
}

# Run on the machine: the command contains the one-time token.
output "join_command" {
  value     = gpudeck_join_token.mac_studio.join_command
  sensitive = true
}

# One multi-use enrolment token for a Kubernetes GPU pool (the Helm chart's
# nodeAgent.joinTokenSecret): up to 100 machines, valid 30 days.
resource "gpudeck_join_token" "k8s_pool" {
  cluster_id  = gpudeck_cluster.office.id
  max_uses    = 100
  ttl_minutes = 43200
}
