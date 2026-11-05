terraform {
  required_version = "= 1.17.0"

  required_providers {
    tls = {
      source  = "hashicorp/tls"
      version = "= 4.4.2"
    }
  }
}
