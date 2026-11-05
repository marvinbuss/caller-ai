variable "resource_group_name" {
  description = "Name of the resource group that holds the state storage account."
  type        = string
}

variable "location" {
  description = "Azure region of the resource group and the storage account."
  type        = string
}

variable "storage_account_name" {
  description = "Globally unique storage account name, 3 to 24 lowercase letters or digits."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]{3,24}$", var.storage_account_name))
    error_message = "state.storage_account_name must be 3 to 24 lowercase letters or digits."
  }
}

variable "state_retention_days" {
  description = "Days that deleted blobs and containers stay recoverable, 7 to 365."
  type        = number

  validation {
    condition     = var.state_retention_days >= 7 && var.state_retention_days <= 365
    error_message = "state.state_retention_days must be between 7 and 365."
  }
}

variable "writer_principal_ids" {
  description = "Object IDs of the deployment identities that may read and write the state and plan containers."
  type        = list(string)
}

variable "tags" {
  description = "Tags applied to every resource of the module."
  type        = map(string)
  default     = {}
}
