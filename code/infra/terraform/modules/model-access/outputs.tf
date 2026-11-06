output "account_id" {
  description = "Resource ID of the existing model account."
  value       = var.account_id
}

output "endpoint" {
  description = "Endpoint of the model account."
  value       = data.azurerm_cognitive_account.this.endpoint
}

output "role_assignment_id" {
  description = "Resource ID of the runtime's role assignment on the account."
  value       = azurerm_role_assignment.runtime.id
}
