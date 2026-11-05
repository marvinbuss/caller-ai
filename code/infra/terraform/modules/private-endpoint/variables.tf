variable "name" {
  description = "Name of the private endpoint."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group of the private endpoint."
  type        = string
}

variable "location" {
  description = "Azure region of the private endpoint."
  type        = string
}

variable "subnet_id" {
  description = "Resource ID of the subnet that hosts the endpoint."
  type        = string
}

variable "target_resource_id" {
  description = "Resource ID of the service the endpoint exposes."
  type        = string
}

variable "subresource_names" {
  description = "Private-link sub-resources to connect, for example Sql or vault."
  type        = list(string)
}

variable "private_dns_zone_ids" {
  description = "Private DNS zone IDs that receive the endpoint records."
  type        = list(string)
}

variable "tags" {
  description = "Tags applied to the endpoint."
  type        = map(string)
  default     = {}
}
