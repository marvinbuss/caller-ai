mock_provider "azurerm" {}

variables {
  subscription_id            = "00000000-0000-4000-8000-000000000001"
  tenant_id                  = "00000000-0000-4000-8000-000000000002"
  client_id                  = "00000000-0000-4000-8000-000000000003"
  location                   = "westus3"
  resource_group_name        = "rg-caller-state-fixture"
  storage_account_name       = "stcallerstatefixture01"
  state_retention_days       = 30
  state_writer_principal_ids = ["00000000-0000-4000-8000-000000000004"]
  private_endpoint_subnet_id = "/subscriptions/00000000-0000-4000-8000-000000000001/resourceGroups/rg-network-fixture/providers/Microsoft.Network/virtualNetworks/vnet-fixture/subnets/private-endpoints"
  private_dns_zone_ids       = ["/subscriptions/00000000-0000-4000-8000-000000000001/resourceGroups/rg-network-fixture/providers/Microsoft.Network/privateDnsZones/privatelink.blob.core.windows.net"]
  tags                       = { owner = "fixture-operator" }
}

run "private_encrypted_entra_state" {
  command = plan

  assert {
    condition = (
      azurerm_storage_account.state.public_network_access == "Disabled" &&
      azurerm_storage_account.state.shared_access_key_enabled == false &&
      azurerm_storage_account.state.default_to_oauth_authentication == true &&
      azurerm_storage_account.state.https_traffic_only_enabled == true &&
      azurerm_storage_account.state.min_tls_version == "TLS1_2" &&
      azurerm_storage_account.state.infrastructure_encryption_enabled == true
    )
    error_message = "State storage must deny public/key access and require encrypted Entra-authenticated transport."
  }

  assert {
    condition = (
      azurerm_storage_container.state.container_access_type == "private" &&
      azurerm_storage_account.state.blob_properties[0].versioning_enabled == true &&
      azurerm_storage_account.state.blob_properties[0].delete_retention_policy[0].days == var.state_retention_days
    )
    error_message = "State must be private and recoverable under an explicitly supplied retention policy."
  }

  assert {
    condition = (
      length(azurerm_role_assignment.state_writer) == 1 &&
      alltrue([for assignment in azurerm_role_assignment.state_writer : assignment.role_definition_name == "Storage Blob Data Contributor"]) &&
      toset(azurerm_private_endpoint.state.private_service_connection[0].subresource_names) == toset(["blob"]) &&
      azurerm_management_lock.state.lock_level == "CanNotDelete"
    )
    error_message = "State requires explicit least-scope writers, private Blob connectivity and deletion protection."
  }
}

run "empty_writer_set_rejected" {
  command = plan
  variables {
    state_writer_principal_ids = []
  }
  expect_failures = [var.state_writer_principal_ids]
}

run "unapproved_retention_rejected" {
  command = plan
  variables {
    state_retention_days = 0
  }
  expect_failures = [var.state_retention_days]
}

run "malformed_identity_rejected" {
  command = plan
  variables {
    tenant_id = "------------------------------------"
  }
  expect_failures = [var.tenant_id]
}
