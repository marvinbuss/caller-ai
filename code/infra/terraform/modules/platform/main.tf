terraform {
  required_version = ">= 1.17.0, < 2.0.0"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = ">= 5.11.0, < 6.0.0"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = ">= 3.10.0, < 4.0.0"
    }
  }
}

variable "location" {
  type        = string
  description = "Explicit approved Azure region; no invented deployment default."
  validation {
    condition     = can(regex("^[a-z][a-z0-9]{1,31}$", var.location))
    error_message = "Supply an approved canonical Azure region."
  }
}

variable "resource_group_name" {
  type        = string
  description = "Dedicated platform resource group, separate from remote-state administration."
  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9_-]{0,89}$", var.resource_group_name))
    error_message = "Supply a valid platform resource group name."
  }
}

variable "tags" {
  type        = map(string)
  description = "Nonsecret governance tags with an accountable owner reference."
  validation {
    condition     = try(length(trimspace(var.tags["owner"])) > 0, false)
    error_message = "An accountable owner tag is required."
  }
}

resource "azurerm_resource_group" "platform" {
  name     = var.resource_group_name
  location = var.location
  tags     = merge(var.tags, { application = "caller-ai", managed_by = "terraform" })

  lifecycle {
    prevent_destroy = true
  }
}

output "resource_group_id" {
  value       = azurerm_resource_group.platform.id
  description = "Platform resource group reference; not application readiness."
}

output "resource_group_name" {
  value       = azurerm_resource_group.platform.name
  description = "Resource group for subsequent reviewed platform resources."
}
