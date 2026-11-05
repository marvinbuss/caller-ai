variable "key_id" {
  description = "Key ID of the permit signing key pair, 1 to 128 characters of letters, digits, underscore or hyphen."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$", var.key_id))
    error_message = "signing_key.key_id must match [A-Za-z0-9][A-Za-z0-9_-]{0,127}."
  }
}
