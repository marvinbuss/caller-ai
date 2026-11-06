variable "name" {
  description = "Secret name inside the vault."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9-]{1,127}$", var.name))
    error_message = "The secret name must be 1 to 127 letters, digits or hyphens."
  }
}

variable "vault_id" {
  description = "Resource ID of the Key Vault that receives the secret."
  type        = string
}

variable "vault_uri" {
  description = "URI of the Key Vault, used to build the versionless secret ID."
  type        = string
}

variable "value" {
  description = "Secret value. It reaches the vault over the management API and never appears in plan output."
  type        = string
  sensitive   = true
}
