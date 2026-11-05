mock_provider "azurerm" {}

variables {
  name                       = "cae-test"
  resource_group_name        = "rg-apps"
  location                   = "swedencentral"
  infrastructure_subnet_id   = "/subscriptions/00000000-0000-4000-8000-000000000002/resourceGroups/rg-network/providers/Microsoft.Network/virtualNetworks/vnet-test/subnets/snet-container-apps"
  log_analytics_workspace_id = "/subscriptions/00000000-0000-4000-8000-000000000002/resourceGroups/rg-apps/providers/Microsoft.OperationalInsights/workspaces/log-test"
}

run "private_by_default_with_consumption_profile" {
  command = plan

  assert {
    condition     = azurerm_container_app_environment.this.public_network_access == "Disabled"
    error_message = "Public network access must default to disabled."
  }

  assert {
    condition     = length([for p in azurerm_container_app_environment.this.workload_profile : p if p.workload_profile_type == "Consumption" && p.name == "Consumption"]) == 1
    error_message = "The Consumption workload profile must be declared at creation."
  }
}

run "environment_is_external_so_the_flag_toggles_in_place" {
  command = plan

  variables {
    public_access_enabled = true
  }

  assert {
    condition     = azurerm_container_app_environment.this.public_network_access == "Enabled" && azurerm_container_app_environment.this.internal_load_balancer_enabled == false
    error_message = "The environment must be external with public access following the flag."
  }
}
