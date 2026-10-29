terraform {
  required_version = "= 1.17.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "= 5.11.0"
    }
  }

  backend "azurerm" {
    use_azuread_auth = true
    use_oidc         = true
    use_cli          = false
  }
}

provider "azurerm" {
  features {}
  subscription_id                 = var.subscription_id
  tenant_id                       = var.tenant_id
  client_id                       = var.client_id
  use_oidc                        = true
  use_cli                         = false
  storage_use_azuread             = true
  resource_provider_registrations = "none"
}
