output "id" {
  description = "Resource ID of the Container Apps environment."
  value       = azurerm_container_app_environment.this.id
}

output "default_domain" {
  description = "Default domain of the environment. Each app's address is <app name>.<default domain>."
  value       = azurerm_container_app_environment.this.default_domain
}

output "public_network_access" {
  description = "Public network access setting of the environment (Enabled or Disabled)."
  value       = azurerm_container_app_environment.this.public_network_access
}
