module "model_access" {
  source = "../modules/model-access"

  account_id           = var.app_environment.model.account_id
  text_deployment      = var.app_environment.model.text_deployment
  live_deployment      = var.app_environment.model.live_deployment
  runtime_principal_id = data.azurerm_user_assigned_identity.runtime.principal_id
}

module "model_endpoint" {
  source = "../modules/private-endpoint"

  name                = "pe-${local.model_account_name}"
  resource_group_name = azurerm_resource_group.apps.name
  location            = var.environment.location
  subnet_id           = local.network.private_endpoint_subnet_id
  target_resource_id  = var.app_environment.model.account_id
  subresource_names   = ["account"]
  tags                = local.tags

  private_dns_zone_ids = [
    local.zones["openai"],
    local.zones["cognitive"],
    local.zones["ai_services"],
  ]
}
