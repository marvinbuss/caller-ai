mock_provider "azurerm" {}

variables {
  resource_group_name  = "rg-state"
  location             = "swedencentral"
  storage_account_name = "callerstate0001"
  state_retention_days = 30
  writer_principal_ids = ["00000000-0000-4000-8000-000000000004", "00000000-0000-4000-8000-000000000008"]
}

run "defaults_are_identity_only_and_protected" {
  command = plan

  assert {
    condition     = azurerm_storage_account.state.shared_access_key_enabled == false
    error_message = "Shared keys must be disabled."
  }

  assert {
    condition     = azurerm_storage_account.state.min_tls_version == "TLS1_2" && azurerm_storage_account.state.https_traffic_only_enabled
    error_message = "TLS 1.2 and HTTPS only are required."
  }

  assert {
    condition     = azurerm_storage_account.state.blob_properties[0].versioning_enabled
    error_message = "Blob versioning must be on."
  }

  assert {
    condition     = azurerm_storage_account.state.blob_properties[0].delete_retention_policy[0].days == 30 && azurerm_storage_account.state.blob_properties[0].container_delete_retention_policy[0].days == 30
    error_message = "Soft delete must follow state_retention_days."
  }

  assert {
    condition     = azurerm_management_lock.state.lock_level == "CanNotDelete"
    error_message = "A delete lock is required."
  }

  assert {
    condition     = azurerm_storage_container.state.name == "tfstate" && azurerm_storage_container.plans.name == "tfplans"
    error_message = "Containers tfstate and tfplans are required."
  }
}

run "writers_are_scoped_to_the_two_containers" {
  command = plan

  assert {
    condition     = length(azurerm_role_assignment.writer) == 4
    error_message = "Each writer needs a role on exactly the two containers."
  }

  assert {
    condition     = alltrue([for r in azurerm_role_assignment.writer : r.role_definition_name == "Storage Blob Data Contributor"])
    error_message = "Writers get blob data access only."
  }
}

run "exposes_only_non_secret_coordinates" {
  command = plan

  assert {
    condition     = output.container_name == "tfstate" && output.plan_container_name == "tfplans" && output.storage_account_name == "callerstate0001"
    error_message = "The module exposes backend coordinates only."
  }
}

run "rejects_invalid_account_name" {
  command = plan

  variables {
    storage_account_name = "Bad_Name"
  }

  expect_failures = [var.storage_account_name]
}

run "rejects_retention_outside_range" {
  command = plan

  variables {
    state_retention_days = 6
  }

  expect_failures = [var.state_retention_days]
}
