data "gpudeck_nodes" "online_mac" {
  cluster_id = gpudeck_cluster.office.id
  status     = "online"
  os         = "macos"
  labels     = ["role=apple"]
}

output "mac_nodes" {
  value = [for n in data.gpudeck_nodes.online_mac.nodes : n.name]
}
