module "data_store" {
  source = "../modules/data-store"

  name                    = var.app_environment.data_store.name
  resource_group_name     = azurerm_resource_group.apps.name
  location                = var.environment.location
  backup_tier             = var.app_environment.data_store.backup_tier
  configuration_audit_ttl = var.app_environment.data_store.configuration_audit_ttl
  call_audit_ttl          = var.app_environment.data_store.call_audit_ttl
  call_session_ttl        = var.app_environment.data_store.call_session_ttl
  tags                    = local.tags

  # Each application reaches only its own containers.
  data_roles = {
    runtime = {
      principal_id = data.azurerm_user_assigned_identity.runtime.principal_id
      containers   = ["call-sessions", "text-conversations"]
    }
    management = {
      principal_id = module.management_identity.principal_id
      containers   = ["configuration", "configuration-audit", "call-audit"]
    }
  }
}

module "data_store_endpoint" {
  source = "../modules/private-endpoint"

  name                 = "pe-${var.app_environment.data_store.name}"
  resource_group_name  = azurerm_resource_group.apps.name
  location             = var.environment.location
  subnet_id            = local.network.private_endpoint_subnet_id
  target_resource_id   = module.data_store.id
  subresource_names    = ["Sql"]
  private_dns_zone_ids = [local.zones["cosmos"]]
  tags                 = local.tags
}
