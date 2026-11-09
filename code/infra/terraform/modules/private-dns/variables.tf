variable "resource_group_name" {
  description = "Resource group that holds the private DNS zones."
  type        = string
}

variable "vnet_id" {
  description = "Resource ID of the virtual network that the zones are linked to."
  type        = string
}

variable "tags" {
  description = "Tags applied to every resource of the module."
  type        = map(string)
  default     = {}
}
