resource "azurerm_resource_group" "state" {
  name     = var.resource_group_name
  location = var.location
  tags     = merge(var.tags, { application = "caller-ai", managed_by = "terraform" }, var.label == null ? {} : { label = var.label })

  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_storage_account" "state" {
  name                              = var.storage_account_name
  resource_group_name               = azurerm_resource_group.state.name
  location                          = azurerm_resource_group.state.location
  account_tier                      = "Standard"
  account_replication_type          = "ZRS"
  account_kind                      = "StorageV2"
  public_network_access             = "Disabled"
  shared_access_key_enabled         = false
  default_to_oauth_authentication   = true
  https_traffic_only_enabled        = true
  min_tls_version                   = "TLS1_2"
  infrastructure_encryption_enabled = true
  allow_nested_items_to_be_public   = false
  cross_tenant_replication_enabled  = false
  tags                              = azurerm_resource_group.state.tags

  network_rules {
    default_action = "Deny"
    bypass         = ["None"]
  }

  blob_properties {
    versioning_enabled  = true
    change_feed_enabled = true
    delete_retention_policy {
      days = var.state_retention_days
    }
    container_delete_retention_policy {
      days = var.state_retention_days
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_private_endpoint" "state" {
  name                = "${var.storage_account_name}-blob"
  resource_group_name = azurerm_resource_group.state.name
  location            = azurerm_resource_group.state.location
  subnet_id           = var.private_endpoint_subnet_id
  tags                = azurerm_resource_group.state.tags

  private_service_connection {
    name                           = "state-blob"
    private_connection_resource_id = azurerm_storage_account.state.id
    subresource_names              = ["blob"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "state-blob"
    private_dns_zone_ids = var.private_dns_zone_ids
  }
}

resource "azurerm_storage_container" "state" {
  name                  = "tfstate"
  storage_account_id    = azurerm_storage_account.state.id
  container_access_type = "private"
  depends_on            = [azurerm_private_endpoint.state]

  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_storage_container" "plans" {
  name                  = var.plan_container_name
  storage_account_id    = azurerm_storage_account.state.id
  container_access_type = "private"
  depends_on            = [azurerm_private_endpoint.state]

  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_role_assignment" "plan_writer" {
  for_each             = var.state_writer_principal_ids
  scope                = azurerm_storage_container.plans.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = each.value
}

resource "azurerm_role_assignment" "state_writer" {
  for_each             = var.state_writer_principal_ids
  scope                = azurerm_storage_container.state.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = each.value
}

resource "azurerm_management_lock" "state" {
  name       = "protect-terraform-state"
  scope      = azurerm_storage_account.state.id
  lock_level = "CanNotDelete"
  notes      = "State deletion requires a separately reviewed recovery procedure."
}

output "state_backend" {
  description = "Nonsecret backend coordinates, not credentials or a readiness assertion."
  value = {
    resource_group_name  = azurerm_resource_group.state.name
    storage_account_name = azurerm_storage_account.state.name
    container_name       = azurerm_storage_container.state.name
    use_azuread_auth     = true
    use_oidc             = true
    plan_container_name  = azurerm_storage_container.plans.name
  }
}
