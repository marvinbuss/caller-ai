terraform {
  required_version = "= 1.17.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "= 5.11.0"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = "= 3.10.0"
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

provider "azuread" {
  tenant_id = var.tenant_id
  client_id = var.client_id
  use_oidc  = true
  use_cli   = false
}

module "platform" {
  source              = "../modules/platform"
  location            = var.location
  resource_group_name = var.resource_group_name
  tenant_id           = var.tenant_id
  services            = var.services
  security            = var.security
  monitoring          = var.monitoring
  tags                = merge(var.tags, { environment = var.environment })
}

output "platform_resource_group_name" {
  value       = module.platform.resource_group_name
  description = "Nonsecret platform reference; not deployment or application readiness."
}

output "environment" {
  value       = var.environment
  description = "Explicit environment binding shared by every workload deployment."
}

output "managed_action_group_id" {
  value       = module.platform.managed_action_group_id
  description = "Optional managed notification destination; application delivery remains unqualified."
}
