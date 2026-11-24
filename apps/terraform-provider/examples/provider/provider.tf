terraform {
  required_providers {
    gpudeck = {
      source = "sur950/gpudeck"
    }
  }
}

# Credentials can also come from GPUDECK_ENDPOINT, GPUDECK_USERNAME, GPUDECK_PASSWORD
# or GPUDECK_TOKEN (a session token from `gpudeck login`).
provider "gpudeck" {
  endpoint = "https://gpudeck.example.com"
  username = "admin"
  password = var.gpudeck_password
}

variable "gpudeck_password" {
  type      = string
  sensitive = true
}
