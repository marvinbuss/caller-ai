mock_provider "azurerm" {}
mock_provider "azuread" {}

variables {
  environment         = "dev"
  monitoring          = jsondecode(file("../test-fixtures/monitoring.json"))
  security            = jsondecode(file("../test-fixtures/security.json"))
  subscription_id     = "00000000-0000-4000-8000-000000000001"
  tenant_id           = "00000000-0000-4000-8000-000000000002"
  client_id           = "00000000-0000-4000-8000-000000000003"
  location            = "westus3"
  resource_group_name = "rg-caller-platform-fixture"
  tags                = { owner = "fixture-operator" }
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

run "environment_module_is_composed_with_explicit_inputs" {
  command = plan
  assert {
    condition     = output.platform_resource_group_name == var.resource_group_name && output.environment == "dev"
    error_message = "The shared root must compose the reviewed module with explicit environment inputs."
  }
}

run "same_root_supports_another_environment" {
  command = plan
  variables {
    environment = "prod"
  }
  assert {
    condition     = output.environment == "prod"
    error_message = "Environments must differ by input values, not copied Terraform definitions."
  }
}

run "invalid_environment_rejected" {
  command = plan
  variables {
    environment = "../prod"
  }
  expect_failures = [var.environment]
}

run "invalid_provisioning_identity_rejected" {
  command = plan
  variables {
    client_id = "------------------------------------"
  }
  expect_failures = [var.client_id]
}
