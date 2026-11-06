output "id" {
  description = "Resource ID of the private endpoint."
  value       = azurerm_private_endpoint.this.id
}

output "private_ip_address" {
  description = "Private IP address of the endpoint. The environment check reports the connection approval state."
  value       = azurerm_private_endpoint.this.private_service_connection[0].private_ip_address
}
