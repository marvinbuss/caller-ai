terraform {
  required_version = "= 1.17.0"

  required_providers {
    azapi = {
      source  = "Azure/azapi"
      version = "= 2.13.0"
    }
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "= 5.11.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "= 4.4.2"
    }
  }

  # Partial configuration: coordinates come from app_environment.azurerm.tfbackend.json. The deployment
  # refuses local state and an unreachable backend (FR-007).
  backend "azurerm" {}
}

# Authentication comes from the environment (OIDC in workflows). No directory provider is used (FR-042).
provider "azurerm" {
  features {}

  subscription_id                 = var.environment.subscription_id
  tenant_id                       = var.environment.tenant_id
  client_id                       = var.environment.client_id
  storage_use_azuread             = true
  resource_provider_registrations = "none"
}

provider "azapi" {
  subscription_id = var.environment.subscription_id
  tenant_id       = var.environment.tenant_id
  client_id       = var.environment.client_id
}
