variable "name" {
  description = "Cosmos DB account name, globally unique."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group of the account."
  type        = string
}

variable "location" {
  description = "Azure region of the account."
  type        = string
}

variable "database_name" {
  description = "Name of the SQL database that holds the containers."
  type        = string
  default     = "caller"
}

variable "backup_tier" {
  description = "Continuous backup tier, Continuous7Days or Continuous30Days."
  type        = string
  default     = "Continuous7Days"

  validation {
    condition     = contains(["Continuous7Days", "Continuous30Days"], var.backup_tier)
    error_message = "The backup tier must be Continuous7Days or Continuous30Days."
  }
}

variable "configuration_audit_ttl" {
  description = "Default time to live in seconds for configuration audit items."
  type        = number
}

variable "call_audit_ttl" {
  description = "Default time to live in seconds for call audit items."
  type        = number
}

variable "call_session_ttl" {
  description = "Default time to live in seconds for call session items."
  type        = number
}

variable "data_roles" {
  description = "Data role assignments by name. Each principal may use only the listed containers."
  type = map(object({
    principal_id = string
    containers   = list(string)
  }))
  default = {}

  validation {
    condition = alltrue([
      for role in values(var.data_roles) : alltrue([
        for c in role.containers : contains(["configuration", "configuration-audit", "call-sessions", "call-audit", "text-conversations"], c)
      ])
    ])
    error_message = "A data role may name only the containers that the module creates."
  }
}

variable "tags" {
  description = "Tags applied to the account."
  type        = map(string)
  default     = {}
}
