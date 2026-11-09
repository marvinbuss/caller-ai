# The environment is created external so that the public access flag changes only its
# public_network_access setting in place (FR-020). No outbound rule is configured (FR-044).
resource "azurerm_container_app_environment" "this" {
  name                       = var.name
  resource_group_name        = var.resource_group_name
  location                   = var.location
  logs_destination           = "log-analytics"
  log_analytics_workspace_id = var.log_analytics_workspace_id
  infrastructure_subnet_id   = var.infrastructure_subnet_id

  internal_load_balancer_enabled = false
  public_network_access          = var.public_access_enabled ? "Enabled" : "Disabled"

  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }

  tags = var.tags
}
