terraform {
  required_version = "= 1.17.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "= 5.11.0"
    }
  }

  # Partial configuration: coordinates come from state.azurerm.tfbackend.json. The one-time creation
  # runs with local state (init -backend=false) and then migrates into this backend (FR-006, FR-007).
  backend "azurerm" {}
}

# Authentication comes from the environment: OIDC in workflows, the Azure CLI for the one-time creation.
provider "azurerm" {
  features {}

  subscription_id                 = var.environment.subscription_id
  tenant_id                       = var.environment.tenant_id
  client_id                       = var.environment.client_id
  storage_use_azuread             = true
  resource_provider_registrations = "none"
}
