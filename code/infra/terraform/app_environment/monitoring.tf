module "monitoring" {
  source = "../modules/monitoring"

  workspace_name      = var.app_environment.monitoring.workspace_name
  resource_group_name = azurerm_resource_group.apps.name
  location            = var.environment.location
  retention_days      = var.app_environment.monitoring.retention_days
  daily_cap_gb        = var.app_environment.monitoring.daily_cap_gb
  action_group        = var.app_environment.monitoring.action_group
  alerts              = var.app_environment.monitoring.alerts
  tags                = local.tags

  app_ids = {
    runtime    = module.runtime_app.id
    management = module.management_app.id
  }
}
