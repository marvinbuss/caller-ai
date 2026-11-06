output "id" {
  description = "Resource ID of the container app."
  value       = azurerm_container_app.this.id
}

output "name" {
  description = "Name of the container app."
  value       = azurerm_container_app.this.name
}

output "fqdn" {
  description = "Default fully qualified domain name of the app ingress."
  value       = azurerm_container_app.this.ingress[0].fqdn
}

output "settings" {
  description = "Plain environment settings given to the container. It never holds a secret."
  value       = var.settings
}

output "secret_setting_names" {
  description = "Names of the settings that are Key Vault references."
  value       = keys(var.secret_references)
}

output "public_access_enabled" {
  description = "Whether the ingress is reachable from outside the environment."
  value       = var.public_access_enabled
}

output "allowed_cidrs" {
  description = "Source ranges that the ingress allows when public access is enabled."
  value       = var.public_access_enabled ? var.allowed_cidrs : []
}
