variable "name" {
  description = "Name of the Container Apps environment."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group of the Container Apps environment."
  type        = string
}

variable "location" {
  description = "Azure region of the Container Apps environment."
  type        = string
}

variable "infrastructure_subnet_id" {
  description = "Resource ID of the delegated subnet from the bootstrap deployment."
  type        = string
}

variable "log_analytics_workspace_id" {
  description = "Resource ID of the Log Analytics workspace that receives container and ingress logs."
  type        = string
}

variable "public_access_enabled" {
  description = "Whether the environment accepts traffic from the public internet. Per-app ingress and allow-list rules still apply."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Tags applied to the environment."
  type        = map(string)
  default     = {}
}
