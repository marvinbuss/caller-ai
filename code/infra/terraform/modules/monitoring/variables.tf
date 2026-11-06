variable "workspace_name" {
  description = "Name of the Log Analytics workspace."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group of the monitoring resources."
  type        = string
}

variable "location" {
  description = "Azure region of the monitoring resources."
  type        = string
}

variable "retention_days" {
  description = "Log retention in days, 30 to 730."
  type        = number

  validation {
    condition     = var.retention_days >= 30 && var.retention_days <= 730
    error_message = "monitoring.retention_days must be between 30 and 730."
  }
}

variable "daily_cap_gb" {
  description = "Daily ingestion cap in GB."
  type        = number
}

variable "action_group" {
  description = "Action group name, short name (at most 12 characters) and e-mail receivers by name."
  type = object({
    name            = string
    short_name      = string
    email_receivers = map(string)
  })

  validation {
    condition     = length(var.action_group.short_name) <= 12 && length(var.action_group.email_receivers) > 0
    error_message = "The action group needs a short name of at most 12 characters and at least one receiver."
  }
}

variable "alerts" {
  description = "Alert owner, runbook link and thresholds. An alert without an owner and a runbook URL is rejected."
  type = object({
    owner            = string
    runbook_url      = string
    http_5xx_count   = number
    restart_count    = number
    cpu_percent      = number
    memory_percent   = number
    latency_ms       = optional(number, 3000)
    dependency_count = optional(number, 3)
  })

  validation {
    condition     = length(trimspace(var.alerts.owner)) > 0 && startswith(var.alerts.runbook_url, "https://")
    error_message = "Every alert needs an owner and an https runbook URL."
  }
}

variable "app_ids" {
  description = "Resource IDs of the container apps to alert on, by app name."
  type        = map(string)
}

variable "tags" {
  description = "Tags applied to every resource of the module."
  type        = map(string)
  default     = {}
}
