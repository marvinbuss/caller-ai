resource "azurerm_resource_group" "state" {
  name     = var.resource_group_name
  location = var.location
  tags     = var.tags
}

# The state account keeps a public endpoint on purpose so GitHub-hosted runners can reach it (FR-031).
# Access is by identity only: shared keys and anonymous access are off. Accepted finding, reviewed.
#trivy:ignore:AZU-0012
resource "azurerm_storage_account" "state" {
  name                     = var.storage_account_name
  resource_group_name      = azurerm_resource_group.state.name
  location                 = azurerm_resource_group.state.location
  account_tier             = "Standard"
  account_replication_type = "GRS"
  account_kind             = "StorageV2"

  # Identity-only access: state is reachable over the public endpoint without any shared key.
  shared_access_key_enabled         = false
  default_to_oauth_authentication   = true
  min_tls_version                   = "TLS1_2"
  https_traffic_only_enabled        = true
  allow_nested_items_to_be_public   = false
  public_network_access             = "Enabled"
  infrastructure_encryption_enabled = true

  blob_properties {
    versioning_enabled = true

    delete_retention_policy {
      days = var.state_retention_days
    }

    container_delete_retention_policy {
      days = var.state_retention_days
    }
  }

  tags = var.tags
}

resource "azurerm_storage_container" "state" {
  name                  = "tfstate"
  storage_account_id    = azurerm_storage_account.state.id
  container_access_type = "private"
}

resource "azurerm_storage_container" "plans" {
  name                  = "tfplans"
  storage_account_id    = azurerm_storage_account.state.id
  container_access_type = "private"
}

resource "azurerm_management_lock" "state" {
  name       = "protect-state"
  scope      = azurerm_storage_account.state.id
  lock_level = "CanNotDelete"
  notes      = "Holds Terraform state and plans. Remove the lock deliberately before deleting."
}

locals {
  containers = {
    state = azurerm_storage_container.state.id
    plans = azurerm_storage_container.plans.id
  }

  writer_assignments = {
    for pair in setproduct(var.writer_principal_ids, keys(local.containers)) :
    "${pair[0]}/${pair[1]}" => { principal_id = pair[0], container = pair[1] }
  }
}

resource "azurerm_role_assignment" "writer" {
  for_each = local.writer_assignments

  scope                = local.containers[each.value.container]
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = each.value.principal_id
}
