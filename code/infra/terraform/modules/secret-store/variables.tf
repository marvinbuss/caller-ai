variable "name" {
  description = "Key Vault name, globally unique."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group of the vault."
  type        = string
}

variable "location" {
  description = "Azure region of the vault."
  type        = string
}

variable "tenant_id" {
  description = "Microsoft Entra tenant that authenticates the vault."
  type        = string
}

variable "soft_delete_days" {
  description = "Days that a deleted vault or secret stays recoverable, 7 to 90."
  type        = number
  default     = 30

  validation {
    condition     = var.soft_delete_days >= 7 && var.soft_delete_days <= 90
    error_message = "The soft delete retention must be between 7 and 90 days."
  }
}

variable "reader_principal_ids" {
  description = "Principals that may read secret values (Key Vault Secrets User), by name."
  type        = map(string)
  default     = {}
}

variable "tags" {
  description = "Tags applied to the vault."
  type        = map(string)
  default     = {}
}
