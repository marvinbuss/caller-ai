variable "name" {
  description = "Name of the user-assigned managed identity."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group of the identity."
  type        = string
}

variable "location" {
  description = "Azure region of the identity."
  type        = string
}

variable "role_assignments" {
  description = "Azure role assignments for the identity by name: the scope and the built-in role."
  type = map(object({
    scope                = string
    role_definition_name = string
  }))
  default = {}
}

variable "tags" {
  description = "Tags applied to the identity."
  type        = map(string)
  default     = {}
}
