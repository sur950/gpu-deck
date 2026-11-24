resource "gpudeck_cluster" "office" {
  name             = "office"
  type             = "bare_metal"
  description      = "Rack in the Bengaluru office"
  region           = "blr"
  price_plan_id    = gpudeck_price_plan.standard.id
  egress_allowlist = ["huggingface.co:443", "cdn-lfs.huggingface.co:443"]
  labels = {
    site = "blr"
  }
}
