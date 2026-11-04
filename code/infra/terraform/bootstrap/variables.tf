variable "label" {
  type        = string
  default     = null
  description = "Optional free-form label used only in tags; no behavior depends on its value."
  validation {
    condition     = var.label == null || can(regex("^[A-Za-z0-9][A-Za-z0-9 _.-]{0,62}$", var.label))
    error_message = "Supply a short plain label without paths, or leave it unset."
  }
}

variable "plan_container_name" {
  type        = string
  default     = "tfplans"
  description = "Private plan/review container, distinct from Terraform state."
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$", var.plan_container_name)) && !strcontains(var.plan_container_name, "--") && var.plan_container_name != "tfstate"
    error_message = "Supply a valid separate private plan-container name."
  }
}

variable "subscription_id" {
  type        = string
  description = "Approved subscription for the state resources; no default environment."
  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", var.subscription_id))
    error_message = "Supply an approved subscription UUID."
  }
}

variable "tenant_id" {
  type        = string
  description = "Approved Entra tenant for federated provisioning identity."
  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", var.tenant_id))
    error_message = "Supply an approved tenant UUID."
  }
}

variable "client_id" {
  type        = string
  description = "Approved OIDC client ID, never a client secret or token."
  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", var.client_id))
    error_message = "Supply an approved federated client UUID."
  }
}

variable "location" {
  type        = string
  description = "Explicit approved Azure region; synthetic test region is not approval."
  validation {
    condition     = can(regex("^[a-z][a-z0-9]{1,31}$", var.location))
    error_message = "Supply an explicitly approved canonical Azure region."
  }
}

variable "resource_group_name" {
  type        = string
  description = "Dedicated state resource group name."
  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9_-]{0,89}$", var.resource_group_name))
    error_message = "Supply a valid dedicated resource group name."
  }
}

variable "storage_account_name" {
  type        = string
  description = "Globally unique state storage account name, reviewed before provisioning."
  validation {
    condition     = can(regex("^[a-z0-9]{3,24}$", var.storage_account_name))
    error_message = "Storage names require 3-24 lowercase letters or digits."
  }
}

variable "state_retention_days" {
  type        = number
  description = "Explicit approved state soft-delete retention; not a legal retention default."
  validation {
    condition     = var.state_retention_days >= 7 && var.state_retention_days <= 365 && floor(var.state_retention_days) == var.state_retention_days
    error_message = "Supply an approved integral state recovery interval between 7 and 365 days."
  }
}

variable "state_writer_principal_ids" {
  type        = set(string)
  description = "Explicit Entra object IDs allowed to write/lease the state container."
  validation {
    condition     = length(var.state_writer_principal_ids) > 0 && length(var.state_writer_principal_ids) <= 32 && alltrue([for id in var.state_writer_principal_ids : can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", id))])
    error_message = "Supply 1-32 explicitly approved writer object UUIDs."
  }
}

variable "private_endpoint_subnet_id" {
  type        = string
  description = "Approved existing private endpoint subnet, reachable by the authorized runner."
  validation {
    condition     = can(regex("^/subscriptions/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/resourceGroups/[^/]+/providers/Microsoft.Network/virtualNetworks/[^/]+/subnets/[^/]+$", var.private_endpoint_subnet_id))
    error_message = "Supply an approved private-endpoint subnet resource ID."
  }
}

variable "private_dns_zone_ids" {
  type        = list(string)
  description = "Approved existing private Blob DNS zones; no public-access bootstrap exception."
  validation {
    condition     = length(var.private_dns_zone_ids) > 0 && length(var.private_dns_zone_ids) <= 5 && alltrue([for id in var.private_dns_zone_ids : can(regex("^/subscriptions/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/resourceGroups/[^/]+/providers/Microsoft.Network/privateDnsZones/privatelink\\.blob\\.core\\.windows\\.net$", id))])
    error_message = "Supply approved Azure public-cloud private Blob DNS zone resource IDs."
  }
}

variable "tags" {
  type        = map(string)
  description = "Nonsecret governance tags including an accountable opaque owner reference."
  validation {
    condition     = try(length(trimspace(var.tags["owner"])) > 0, false)
    error_message = "An accountable owner tag is required."
  }
}
