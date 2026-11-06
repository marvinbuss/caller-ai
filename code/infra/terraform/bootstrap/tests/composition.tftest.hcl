mock_provider "azurerm" {}

variables {
  environment = {
    name            = "int"
    tenant_id       = "00000000-0000-4000-8000-000000000001"
    subscription_id = "00000000-0000-4000-8000-000000000002"
    client_id       = "00000000-0000-4000-8000-000000000003"
    location        = "swedencentral"
    tags            = { owner = "platform-team" }
  }
  bootstrap = {
    resource_group_name          = "rg-caller-int-network"
    vnet_name                    = "vnet-caller-int"
    address_space                = "10.60.0.0/22"
    container_apps_subnet_cidr   = "10.60.0.0/26"
    private_endpoint_subnet_cidr = "10.60.1.0/26"
  }
}

run "bootstrap_owns_the_network_with_default_rules_only" {
  command = plan

  assert {
    condition     = module.network.resource_group_name == "rg-caller-int-network"
    error_message = "The network resource group must follow the configuration."
  }

  assert {
    condition     = module.private_dns.zone_ids != null && length(output.private_dns_zone_ids) == 10
    error_message = "Private DNS must be composed with one zone per purpose."
  }
}

run "publishes_the_identifiers_the_app_environment_needs" {
  command = plan

  assert {
    condition     = toset(keys(output.private_dns_zone_ids)) == toset(["cosmos", "vault", "openai", "cognitive", "ai_services", "monitor", "oms", "ods", "agentsvc", "blob"])
    error_message = "Every private DNS zone must be published (FR-008)."
  }
}

run "subnet_smaller_than_27_names_the_setting" {
  command = plan

  variables {
    bootstrap = {
      resource_group_name          = "rg-caller-int-network"
      vnet_name                    = "vnet-caller-int"
      address_space                = "10.60.0.0/22"
      container_apps_subnet_cidr   = "10.60.0.0/28"
      private_endpoint_subnet_cidr = "10.60.1.0/26"
    }
  }

  expect_failures = [var.bootstrap]
}
