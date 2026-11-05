output "id" {
  description = "Resource ID of the user-assigned identity."
  value       = azurerm_user_assigned_identity.this.id
}

output "client_id" {
  description = "Client ID of the identity, used as AZURE_CLIENT_ID by the application."
  value       = azurerm_user_assigned_identity.this.client_id
}

output "principal_id" {
  description = "Object ID of the identity's service principal."
  value       = azurerm_user_assigned_identity.this.principal_id
}
