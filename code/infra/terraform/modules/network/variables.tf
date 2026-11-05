variable "resource_group_name" {
  description = "Name of the resource group that holds the network resources."
  type        = string
}

variable "location" {
  description = "Azure region of the network resources."
  type        = string
}

variable "vnet_name" {
  description = "Name of the virtual network."
  type        = string
}

variable "address_space" {
  description = "Address space of the virtual network in CIDR notation."
  type        = string

  validation {
    condition     = can(cidrhost(var.address_space, 0))
    error_message = "bootstrap.address_space must be a valid CIDR."
  }
}

variable "container_apps_subnet_cidr" {
  description = "CIDR of the subnet delegated to Azure Container Apps, at least a /27 and inside the address space."
  type        = string

  validation {
    condition     = can(cidrhost(var.container_apps_subnet_cidr, 0)) && tonumber(split("/", var.container_apps_subnet_cidr)[1]) <= 27
    error_message = "bootstrap.container_apps_subnet_cidr must be a valid CIDR that is at least a /27."
  }
}

variable "private_endpoint_subnet_cidr" {
  description = "CIDR of the subnet that holds private endpoints, inside the address space."
  type        = string

  validation {
    condition     = can(cidrhost(var.private_endpoint_subnet_cidr, 0))
    error_message = "bootstrap.private_endpoint_subnet_cidr must be a valid CIDR."
  }
}

variable "tags" {
  description = "Tags applied to every resource of the module."
  type        = map(string)
  default     = {}
}
