output "resource_group_name" {
  description = "Resource group that holds the state storage account."
  value       = azurerm_resource_group.state.name
}

output "storage_account_name" {
  description = "Name of the state storage account."
  value       = azurerm_storage_account.state.name
}

output "storage_account_id" {
  description = "Resource ID of the state storage account."
  value       = azurerm_storage_account.state.id
}

output "container_name" {
  description = "Container that holds the Terraform state blobs."
  value       = azurerm_storage_container.state.name
}

output "plan_container_name" {
  description = "Container that holds the saved plans."
  value       = azurerm_storage_container.plans.name
}
