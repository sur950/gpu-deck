# Only the office network may reach the admin API.
resource "gpudeck_ip_rule" "office_admin" {
  action      = "allow"
  scope       = "admin"
  cidr        = "203.0.113.0/24"
  description = "Office VPN"
}

resource "gpudeck_ip_rule" "abuser" {
  action     = "block"
  scope      = "api_layer"
  cidr       = "198.51.100.7"
  expires_at = "2027-01-01T00:00:00Z"
}
