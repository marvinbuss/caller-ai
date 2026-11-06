variable "environment" {
  description = "Environment identity, subscription, region, tags and public access setting from the environment variable file."
  type = object({
    name            = string
    tenant_id       = string
    subscription_id = string
    client_id       = string
    location        = string
    tags            = map(string)
    public_access = optional(object({
      enabled       = optional(bool, false)
      allowed_cidrs = optional(list(string), [])
    }), {})
  })

  validation {
    condition     = contains(["int", "dev", "tst", "prd"], var.environment.name)
    error_message = "environment.name must be one of int, dev, tst or prd."
  }

  validation {
    condition = alltrue([
      for id in [var.environment.tenant_id, var.environment.subscription_id, var.environment.client_id] :
      can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", id))
    ])
    error_message = "environment.tenant_id, environment.subscription_id and environment.client_id must be GUIDs."
  }

  validation {
    condition     = can(regex("^[a-z0-9]{3,30}$", var.environment.location))
    error_message = "environment.location must be an Azure region name such as swedencentral."
  }

  validation {
    condition     = contains(keys(var.environment.tags), "owner")
    error_message = "environment.tags must include owner."
  }

  validation {
    condition     = !var.environment.public_access.enabled || length(var.environment.public_access.allowed_cidrs) > 0
    error_message = "environment.public_access.allowed_cidrs is required when public access is enabled."
  }

  validation {
    condition     = alltrue([for c in var.environment.public_access.allowed_cidrs : can(cidrhost(c, 0))])
    error_message = "environment.public_access.allowed_cidrs must contain valid CIDR ranges."
  }
}

variable "bootstrap" {
  description = "Network layout from the bootstrap object of the environment variable file."
  type = object({
    resource_group_name          = string
    vnet_name                    = string
    address_space                = string
    container_apps_subnet_cidr   = string
    private_endpoint_subnet_cidr = string
  })

  validation {
    condition     = can(cidrhost(var.bootstrap.address_space, 0))
    error_message = "bootstrap.address_space must be a valid CIDR."
  }

  validation {
    condition     = can(cidrhost(var.bootstrap.container_apps_subnet_cidr, 0)) && tonumber(split("/", var.bootstrap.container_apps_subnet_cidr)[1]) <= 27
    error_message = "bootstrap.container_apps_subnet_cidr must be a valid CIDR that is at least a /27."
  }

  validation {
    condition     = can(cidrhost(var.bootstrap.private_endpoint_subnet_cidr, 0))
    error_message = "bootstrap.private_endpoint_subnet_cidr must be a valid CIDR."
  }
}
