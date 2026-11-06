module "management_identity" {
  source = "../modules/workload-identity"

  name                = "id-${var.app_environment.container_apps.management.app_name}"
  resource_group_name = azurerm_resource_group.apps.name
  location            = var.environment.location
  tags                = local.tags
}
