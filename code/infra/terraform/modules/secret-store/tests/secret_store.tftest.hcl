mock_provider "azurerm" {}

variables {
  name                 = "kv-test"
  resource_group_name  = "rg-apps"
  location             = "swedencentral"
  tenant_id            = "00000000-0000-4000-8000-000000000001"
  reader_principal_ids = { runtime = "00000000-0000-4000-8000-000000000010", management = "00000000-0000-4000-8000-000000000011" }
}

run "private_rbac_vault_with_purge_protection" {
  command = plan

  assert {
    condition     = azurerm_key_vault.this.rbac_authorization_enabled && azurerm_key_vault.this.purge_protection_enabled && azurerm_key_vault.this.public_network_access_enabled == false
    error_message = "The vault must use RBAC, purge protection and no public access."
  }

  assert {
    condition     = azurerm_key_vault.this.soft_delete_retention_days == 30 && azurerm_key_vault.this.network_acls[0].default_action == "Deny"
    error_message = "Soft delete and the deny-by-default network rule are required."
  }
}

run "readers_get_secret_read_access_only" {
  command = plan

  assert {
    condition     = length(azurerm_role_assignment.reader) == 2 && alltrue([for r in azurerm_role_assignment.reader : r.role_definition_name == "Key Vault Secrets User"])
    error_message = "Readers must receive Key Vault Secrets User only."
  }
}

run "rejects_soft_delete_outside_range" {
  command = plan

  variables {
    soft_delete_days = 3
  }

  expect_failures = [var.soft_delete_days]
}
