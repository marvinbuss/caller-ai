output "resource_group_name" {
  description = "Resource group that holds the network resources."
  value       = azurerm_resource_group.network.name
}

output "vnet_id" {
  description = "Resource ID of the virtual network."
  value       = azurerm_virtual_network.this.id
}

output "container_apps_subnet_id" {
  description = "Resource ID of the subnet delegated to Azure Container Apps."
  value       = azurerm_subnet.container_apps.id
}

output "private_endpoint_subnet_id" {
  description = "Resource ID of the subnet that holds private endpoints."
  value       = azurerm_subnet.private_endpoints.id
}

output "network_security_group_id" {
  description = "Resource ID of the network security group (default rules only)."
  value       = azurerm_network_security_group.this.id
}

output "route_table_id" {
  description = "Resource ID of the route table (no custom routes)."
  value       = azurerm_route_table.this.id
}
