mock_provider "azurerm" {}

variables {
  name                    = "cosmos-test"
  resource_group_name     = "rg-apps"
  location                = "swedencentral"
  configuration_audit_ttl = 86400
  call_audit_ttl          = 86400
  call_session_ttl        = 86400
  data_roles = {
    runtime    = { principal_id = "00000000-0000-4000-8000-000000000010", containers = ["call-sessions", "text-conversations"] }
    management = { principal_id = "00000000-0000-4000-8000-000000000011", containers = ["configuration", "configuration-audit", "call-audit"] }
  }
}

run "serverless_identity_only_and_private" {
  command = plan

  assert {
    condition     = azurerm_cosmosdb_account.this.local_authentication_enabled == false && azurerm_cosmosdb_account.this.public_network_access_enabled == false
    error_message = "Local authentication must be off and public access must be disabled."
  }

  assert {
    condition     = length([for c in azurerm_cosmosdb_account.this.capabilities : c if c.name == "EnableServerless"]) == 1 && azurerm_cosmosdb_account.this.backup[0].type == "Continuous" && azurerm_cosmosdb_account.this.backup[0].tier == "Continuous7Days"
    error_message = "The account must be serverless with continuous backup."
  }
}

run "containers_and_partition_keys_match_the_repositories" {
  command = plan

  assert {
    condition     = toset(keys(azurerm_cosmosdb_sql_container.this)) == toset(["configuration", "configuration-audit", "call-sessions", "call-audit", "text-conversations"])
    error_message = "The container list must match the repositories exactly."
  }

  assert {
    condition     = azurerm_cosmosdb_sql_container.this["configuration"].partition_key_paths == tolist(["/organizationId"]) && azurerm_cosmosdb_sql_container.this["configuration-audit"].partition_key_paths == tolist(["/dayBucket"]) && azurerm_cosmosdb_sql_container.this["call-audit"].partition_key_paths == tolist(["/dayBucket"]) && azurerm_cosmosdb_sql_container.this["text-conversations"].partition_key_paths == tolist(["/ownerKey"]) && azurerm_cosmosdb_sql_container.this["call-sessions"].partition_key_paths == tolist(["/id"])
    error_message = "Partition keys must match the repositories."
  }

  assert {
    condition     = azurerm_cosmosdb_sql_container.this["configuration"].default_ttl == -1 && azurerm_cosmosdb_sql_container.this["text-conversations"].default_ttl == -1 && azurerm_cosmosdb_sql_container.this["call-audit"].default_ttl == 86400
    error_message = "TTLs must follow the repositories (item TTL on, no default expiry, for configuration and text history)."
  }
}

run "roles_are_scoped_to_each_principals_own_containers" {
  command = plan

  assert {
    condition     = toset(keys(azurerm_cosmosdb_sql_role_assignment.this)) == toset(["runtime/call-sessions", "runtime/text-conversations", "management/configuration", "management/configuration-audit", "management/call-audit"])
    error_message = "Each principal may only have roles on its own containers."
  }
}

run "role_on_an_unknown_container_is_rejected" {
  command = plan

  variables {
    data_roles = { runtime = { principal_id = "00000000-0000-4000-8000-000000000010", containers = ["other"] } }
  }

  expect_failures = [var.data_roles]
}

run "invalid_backup_tier_is_rejected" {
  command = plan

  variables {
    backup_tier = "Periodic"
  }

  expect_failures = [var.backup_tier]
}
