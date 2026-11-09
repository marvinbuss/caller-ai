variable "name" {
  description = "Name of the Foundry account, also its custom subdomain, so it must be globally unique."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$", var.name))
    error_message = "app_environment.model.account_name must be 2 to 64 lowercase letters, digits or hyphens, starting and ending with a letter or digit."
  }
}

variable "resource_group_name" {
  description = "Resource group of the account."
  type        = string
}

variable "location" {
  description = "Azure region of the account. The root passes application.live_region."
  type        = string
}

variable "tags" {
  description = "Tags applied to the account."
  type        = map(string)
  default     = {}
}

variable "text" {
  description = "Text deployment: deployment name, model, exact version, deployment type and capacity units."
  type = object({
    deployment = string
    model      = string
    version    = string
    sku        = string
    capacity   = number
  })

  validation {
    condition = (
      can(regex("^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$", var.text.deployment)) &&
      length(var.text.model) > 0 && length(var.text.version) > 0 &&
      contains(["GlobalStandard", "DataZoneStandard", "Standard"], var.text.sku) &&
      var.text.capacity >= 1 && floor(var.text.capacity) == var.text.capacity &&
      var.text.deployment != var.live.deployment
    )
    error_message = "app_environment.model.text needs a deployment name that differs from the live one, a model, a version, a deployment type of GlobalStandard, DataZoneStandard or Standard, and a whole capacity of at least 1."
  }
}

variable "live" {
  description = "Realtime deployment for speech. Deployment and model are gpt-live-1, the name the runtime's Live session requests."
  type = object({
    deployment = string
    model      = string
    version    = string
    sku        = string
    capacity   = number
  })

  validation {
    condition = (
      var.live.deployment == "gpt-live-1" && var.live.model == "gpt-live-1" &&
      length(var.live.version) > 0 &&
      contains(["GlobalStandard", "DataZoneStandard", "Standard"], var.live.sku) &&
      var.live.capacity >= 1 && floor(var.live.capacity) == var.live.capacity
    )
    error_message = "app_environment.model.live must use deployment and model gpt-live-1, a version, a deployment type of GlobalStandard, DataZoneStandard or Standard, and a whole capacity of at least 1."
  }
}

variable "runtime_principal_id" {
  description = "Object ID of the runtime's identity, which receives the least role that allows model calls."
  type        = string
}
