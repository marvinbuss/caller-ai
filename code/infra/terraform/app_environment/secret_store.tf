module "secret_store" {
  source = "../modules/secret-store"

  name                = var.app_environment.secret_store.name
  resource_group_name = azurerm_resource_group.apps.name
  location            = var.environment.location
  tenant_id           = var.environment.tenant_id
  soft_delete_days    = var.app_environment.secret_store.soft_delete_days
  tags                = local.tags

  # Only the management app reads secrets; the runtime holds no secret (FR-051).
  reader_principal_ids = {
    management = module.management_identity.principal_id
  }
}

module "secret_store_endpoint" {
  source = "../modules/private-endpoint"

  name                 = "pe-${var.app_environment.secret_store.name}"
  resource_group_name  = azurerm_resource_group.apps.name
  location             = var.environment.location
  subnet_id            = local.network.private_endpoint_subnet_id
  target_resource_id   = module.secret_store.id
  subresource_names    = ["vault"]
  private_dns_zone_ids = [local.zones["vault"]]
  tags                 = local.tags
}
