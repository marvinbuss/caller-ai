mock_provider "azurerm" {}
mock_provider "azuread" {}

variables {
  monitoring          = jsondecode(file("../../test-fixtures/monitoring.json"))
  security            = jsondecode(file("../../test-fixtures/security.json"))
  location            = "westus3"
  resource_group_name = "rg-caller-platform-fixture"
  tags                = { owner = "fixture-operator" }
  tenant_id           = "00000000-0000-4000-8000-000000000002"
  services = {
    aks_name                = "aks-caller-fixture"
    acr_name                = "acrcallerfixture01"
    cosmos_name             = "cosmos-caller-fixture"
    key_vault_name          = "kv-caller-fixture"
    monitor_name            = "log-caller-fixture"
    kubernetes_version      = "1.35.3"
    aks_subnet_id           = "/subscriptions/00000000-0000-4000-8000-000000000001/resourceGroups/rg-network-fixture/providers/Microsoft.Network/virtualNetworks/vnet-fixture/subnets/aks"
    admin_group_ids         = ["00000000-0000-4000-8000-000000000010"]
    system_vm_size          = "Standard_D4s_v5"
    runtime_vm_size         = "Standard_D4s_v5"
    system_node_count       = 2
    runtime_node_count      = 2
    service_cidr            = "172.20.0.0/16"
    pod_cidr                = "10.244.0.0/16"
    dns_service_ip          = "172.20.0.10"
    log_retention_days      = 30
    vault_soft_delete_days  = 30
    acr_retention_days      = 30
    cosmos_max_throughput   = 1000
    cosmos_backup_tier      = "Continuous7Days"
    configuration_audit_ttl = 86400
    call_session_ttl        = 86400
    call_audit_ttl          = 86400
  }
}

run "explicit_owned_platform_foundation" {
  command = plan
  assert {
    condition = (
      azurerm_resource_group.platform.name == var.resource_group_name &&
      azurerm_resource_group.platform.location == var.location &&
      azurerm_resource_group.platform.tags["owner"] == "fixture-operator" &&
      azurerm_resource_group.platform.tags["managed_by"] == "terraform"
    )
    error_message = "The platform foundation must retain explicit location, ownership and management tags."
  }
}

run "missing_owner_rejected" {
  command = plan
  variables {
    tags = {}
  }
  expect_failures = [var.tags]
}

run "implicit_region_rejected" {
  command = plan
  variables {
    location = ""
  }
  expect_failures = [var.location]
}
