output "account_id" {
  description = "Resource ID of the Foundry account."
  value       = azurerm_cognitive_account.this.id
}

output "name" {
  description = "Name and custom subdomain of the Foundry account."
  value       = azurerm_cognitive_account.this.name
}

output "endpoint" {
  description = "Endpoint of the Foundry account."
  value       = azurerm_cognitive_account.this.endpoint
}

output "text_deployment" {
  description = "Name of the text deployment."
  value       = azurerm_cognitive_deployment.text.name
}

output "live_deployment" {
  description = "Name of the realtime deployment."
  value       = azurerm_cognitive_deployment.live.name
}

output "role_assignment_id" {
  description = "Resource ID of the runtime's role assignment on the account."
  value       = azurerm_role_assignment.runtime.id
}
