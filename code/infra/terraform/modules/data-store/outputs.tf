output "id" {
  description = "Resource ID of the Cosmos DB account."
  value       = azurerm_cosmosdb_account.this.id
}

output "name" {
  description = "Name of the Cosmos DB account."
  value       = azurerm_cosmosdb_account.this.name
}

output "endpoint" {
  description = "Document endpoint of the account."
  value       = azurerm_cosmosdb_account.this.endpoint
}

output "database_name" {
  description = "Name of the SQL database."
  value       = azurerm_cosmosdb_sql_database.this.name
}
