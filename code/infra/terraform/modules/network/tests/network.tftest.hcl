mock_provider "azurerm" {}

variables {
  resource_group_name          = "rg-network"
  location                     = "swedencentral"
  vnet_name                    = "vnet-test"
  address_space                = "10.60.0.0/22"
  container_apps_subnet_cidr   = "10.60.0.0/26"
  private_endpoint_subnet_cidr = "10.60.1.0/26"
}

run "container_apps_subnet_is_delegated" {
  command = plan

  assert {
    condition     = azurerm_subnet.container_apps.delegation[0].service_delegation[0].name == "Microsoft.App/environments"
    error_message = "The Container Apps subnet must be delegated to Microsoft.App/environments."
  }

  assert {
    condition     = azurerm_subnet.container_apps.address_prefixes == tolist(["10.60.0.0/26"]) && azurerm_subnet.private_endpoints.address_prefixes == tolist(["10.60.1.0/26"])
    error_message = "Subnets must use the configured CIDRs."
  }
}

run "default_rules_and_routes_only" {
  command = plan

  assert {
    condition     = length(azurerm_network_security_group.this.security_rule) == 0
    error_message = "The network security group must have no custom rule (FR-044)."
  }

  assert {
    condition     = length(azurerm_route_table.this.route) == 0
    error_message = "The route table must have no custom route (FR-044)."
  }
}

run "both_are_associated_to_the_container_apps_subnet" {
  command = plan

  assert {
    condition     = azurerm_subnet_network_security_group_association.container_apps != null && azurerm_subnet_route_table_association.container_apps != null
    error_message = "The group and the route table must be associated to the Container Apps subnet."
  }
}

run "rejects_container_apps_subnet_smaller_than_27" {
  command = plan

  variables {
    container_apps_subnet_cidr = "10.60.0.0/28"
  }

  expect_failures = [var.container_apps_subnet_cidr]
}

run "rejects_invalid_address_space" {
  command = plan

  variables {
    address_space = "not-a-cidr"
  }

  expect_failures = [var.address_space]
}
