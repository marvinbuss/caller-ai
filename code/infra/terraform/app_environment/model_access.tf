module "model_access" {
  source = "../modules/model-access"

  # The account sits in the Live region; its private endpoint sits in the environment's network.
  name                 = var.app_environment.model.account_name
  resource_group_name  = azurerm_resource_group.apps.name
  location             = var.app_environment.application.live_region
  tags                 = local.tags
  text                 = var.app_environment.model.text
  live                 = var.app_environment.model.live
  runtime_principal_id = data.azurerm_user_assigned_identity.runtime.principal_id
}

module "model_endpoint" {
  source = "../modules/private-endpoint"

  name                = "pe-${local.model_account_name}"
  resource_group_name = azurerm_resource_group.apps.name
  location            = var.environment.location
  subnet_id           = local.network.private_endpoint_subnet_id
  target_resource_id  = module.model_access.account_id
  subresource_names   = ["account"]
  tags                = local.tags

  private_dns_zone_ids = [
    local.zones["openai"],
    local.zones["cognitive"],
    local.zones["ai_services"],
  ]
}
