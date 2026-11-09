locals {
  containers = {
    "configuration"       = { partition_key = "/organizationId", ttl = -1 }
    "configuration-audit" = { partition_key = "/dayBucket", ttl = var.configuration_audit_ttl }
    "call-sessions"       = { partition_key = "/id", ttl = var.call_session_ttl }
    "call-audit"          = { partition_key = "/dayBucket", ttl = var.call_audit_ttl }
    # -1 turns item-level time to live on without a default expiry.
    "text-conversations" = { partition_key = "/ownerKey", ttl = -1 }
  }

  role_assignments = merge([
    for role_name, role in var.data_roles : {
      for container in role.containers : "${role_name}/${container}" => {
        principal_id = role.principal_id
        container    = container
      }
    }
  ]...)
}

resource "azurerm_cosmosdb_account" "this" {
  name                = var.name
  resource_group_name = var.resource_group_name
  location            = var.location
  offer_type          = "Standard"
  kind                = "GlobalDocumentDB"
  tags                = var.tags

  # Identity-only, private, serverless: no key and no public path.
  local_authentication_enabled  = false
  public_network_access_enabled = false

  capabilities {
    name = "EnableServerless"
  }

  consistency_policy {
    consistency_level = "Session"
  }

  geo_location {
    location          = var.location
    failover_priority = 0
  }

  backup {
    type = "Continuous"
    tier = var.backup_tier
  }
}

resource "azurerm_cosmosdb_sql_database" "this" {
  name                = var.database_name
  resource_group_name = var.resource_group_name
  account_name        = azurerm_cosmosdb_account.this.name
}

resource "azurerm_cosmosdb_sql_container" "this" {
  for_each = local.containers

  name                  = each.key
  resource_group_name   = var.resource_group_name
  account_name          = azurerm_cosmosdb_account.this.name
  database_name         = azurerm_cosmosdb_sql_database.this.name
  partition_key_paths   = [each.value.partition_key]
  partition_key_version = 2
  default_ttl           = each.value.ttl
}

resource "azurerm_cosmosdb_sql_role_assignment" "this" {
  for_each = local.role_assignments

  name                = uuidv5("dns", "${azurerm_cosmosdb_account.this.name}/${each.key}")
  resource_group_name = var.resource_group_name
  account_name        = azurerm_cosmosdb_account.this.name
  # Built-in Cosmos DB Built-in Data Contributor, scoped to one container.
  role_definition_id = "${azurerm_cosmosdb_account.this.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002"
  principal_id       = each.value.principal_id
  scope              = "${azurerm_cosmosdb_account.this.id}/dbs/${azurerm_cosmosdb_sql_database.this.name}/colls/${each.value.container}"
}
