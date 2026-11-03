mock_provider "azurerm" {}
mock_provider "azuread" {}

override_resource {
  target          = azurerm_application_insights.service["runtime"]
  override_during = plan
  values = {
    id = "/subscriptions/00000000-0000-4000-8000-000000000001/resourceGroups/rg-caller-platform-fixture/providers/Microsoft.Insights/components/app-log-caller-fixture-runtime"
  }
}

override_resource {
  target          = azurerm_application_insights.service["management"]
  override_during = plan
  values = {
    id = "/subscriptions/00000000-0000-4000-8000-000000000001/resourceGroups/rg-caller-platform-fixture/providers/Microsoft.Insights/components/app-log-caller-fixture-management"
  }
}

variables {
  monitoring          = jsondecode(file("../../test-fixtures/monitoring.json"))
  security            = jsondecode(file("../../test-fixtures/security.json"))
  location            = "westus3"
  resource_group_name = "rg-caller-platform-fixture"
  tenant_id           = "00000000-0000-4000-8000-000000000002"
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

run "private_cluster_and_fixed_call_pool" {
  command = plan
  assert {
    condition = (
      azurerm_kubernetes_cluster.platform.private_cluster_enabled &&
      !azurerm_kubernetes_cluster.platform.private_cluster_public_fqdn_enabled &&
      azurerm_kubernetes_cluster.platform.local_account_disabled &&
      azurerm_kubernetes_cluster.platform.role_based_access_control_enabled &&
      azurerm_kubernetes_cluster.platform.oidc_issuer_enabled &&
      azurerm_kubernetes_cluster.platform.workload_identity_enabled &&
      azurerm_kubernetes_cluster.platform.node_provisioning_profile[0].mode == "Manual" &&
      !azurerm_kubernetes_cluster.platform.default_node_pool[0].auto_scaling_enabled &&
      !azurerm_kubernetes_cluster_node_pool.runtime.auto_scaling_enabled
    )
    error_message = "AKS must be private, Entra-controlled and must not independently scale in active call nodes."
  }
}

run "retirement_guard_default_is_disabled" {
  command = plan
  assert {
    condition     = output.retirement_guard_manifests == null
    error_message = "A default plan must not enable or install retirement enforcement."
  }
}

run "explicit_retirement_guard_requires_controller_uid_and_deny_binding" {
  command = plan
  variables {
    retirement_guard = {
      namespace   = "runtime"
      controller  = "retirement-controller"
      policy_name = "retirement-guard"
    }
  }
  assert {
    condition = (
      output.retirement_guard_manifests[0].spec.failurePolicy == "Fail" &&
      output.retirement_guard_manifests[0].spec.matchConditions[0].expression == "request.namespace == 'runtime'" &&
      strcontains(output.retirement_guard_manifests[0].spec.validations[0].expression, "system:serviceaccount:runtime:retirement-controller") &&
      strcontains(output.retirement_guard_manifests[0].spec.validations[0].expression, "request.options.preconditions.uid != ''") &&
      output.retirement_guard_manifests[1].spec.validationActions[0] == "Deny"
    )
    error_message = "Definitions must fail closed for exact namespace/controller, UID-precondition deletion and denial rather than warning."
  }
}

run "both_service_alerts_are_explicit_private_and_disabled_pending_delivery" {
  command = plan
  assert {
    condition = (
      length(azurerm_application_insights.service) == 2 &&
      length(azurerm_monitor_private_link_scoped_service.application) == 2 &&
      length(azurerm_monitor_scheduled_query_rules_alert_v2.service) == 10 &&
      alltrue([for component in azurerm_application_insights.service : !component.internet_ingestion_enabled && !component.internet_query_enabled && !component.local_authentication_enabled && component.ip_masking_enabled && component.daily_data_cap_in_gb == var.monitoring.daily_cap_gb]) &&
      alltrue([for name, rule in azurerm_monitor_scheduled_query_rules_alert_v2.service : !rule.enabled && !rule.skip_query_validation && rule.evaluation_frequency == "PT1M" && rule.window_duration == "PT5M" && rule.criteria[0].metric_measure_column == "Observed" && length(rule.criteria[0].dimension) == 0 && rule.action[0].custom_properties["owner"] != "" && rule.action[0].custom_properties["runbook"] != "" && length(rule.action[0].action_groups) == 1]) &&
      azurerm_monitor_scheduled_query_rules_alert_v2.service["runtime-latency"].criteria[0].threshold == var.monitoring.services.runtime.mean_latency_seconds &&
      azurerm_monitor_scheduled_query_rules_alert_v2.service["management-errors"].criteria[0].threshold == var.monitoring.services.management.error_ratio &&
      strcontains(azurerm_monitor_scheduled_query_rules_alert_v2.service["runtime-errors"].criteria[0].query, "caller-runtime") &&
      strcontains(azurerm_monitor_scheduled_query_rules_alert_v2.service["management-errors"].criteria[0].query, "management")
    )
    error_message = "Both services need private bounded metrics/explicit owners, runbooks, thresholds and targets; disabled definitions do not prove live collection or delivery."
  }
}

run "managed_action_group_wires_disabled_service_alerts" {
  command = plan
  variables {
    monitoring = merge(var.monitoring, {
      action_group = {
        name            = "ag-caller-fixture"
        short_name      = "caller-dev"
        email_receivers = { operations = "operations@example.invalid" }
      }
      services = { for name, service in var.monitoring.services : name => merge(service, { action_group_id = "" }) }
    })
  }
  override_resource {
    target          = azurerm_monitor_action_group.application[0]
    override_during = plan
    values = {
      id = "/subscriptions/00000000-0000-4000-8000-000000000001/resourceGroups/rg-caller-platform-fixture/providers/Microsoft.Insights/actionGroups/ag-caller-fixture"
    }
  }
  assert {
    condition = (
      length(azurerm_monitor_action_group.application) == 1 &&
      length(azurerm_monitor_action_group.application[0].email_receiver) == 1 &&
      alltrue([for rule in azurerm_monitor_scheduled_query_rules_alert_v2.service : !rule.enabled && toset(rule.action[0].action_groups) == toset([azurerm_monitor_action_group.application[0].id])])
    )
    error_message = "Managed recipients must wire the exact group without enabling unqualified alerts."
  }
}

run "invalid_managed_receiver_rejected" {
  command = plan
  variables {
    monitoring = merge(var.monitoring, {
      action_group = {
        name            = "ag-caller-fixture"
        short_name      = "caller-dev"
        email_receivers = { operations = "not-an-address" }
      }
    })
  }
  expect_failures = [var.monitoring]
}

run "missing_monitor_owner_rejected" {
  command = plan
  variables {
    monitoring = merge(var.monitoring, { services = merge(var.monitoring.services, { management = merge(var.monitoring.services.management, { owner = "" }) }) })
  }
  expect_failures = [var.monitoring]
}

run "all_alert_queries_pin_the_exact_service_resource" {
  command = plan
  assert {
    condition = alltrue([for name, rule in azurerm_monitor_scheduled_query_rules_alert_v2.service :
      strcontains(rule.criteria[0].query, "_ResourceId =~ '${azurerm_application_insights.service[local.service_alerts[name].service].id}'")
    ])
    error_message = "Every signal, including missing telemetry, must filter its exact Application Insights source, not just a forgeable or colliding service label."
  }
}

run "invalid_monitor_threshold_rejected" {
  command = plan
  variables {
    monitoring = merge(var.monitoring, { services = merge(var.monitoring.services, { runtime = merge(var.monitoring.services.runtime, { saturation_ratio = 1.5 }) }) })
  }
  expect_failures = [var.monitoring]
}

run "inline_monitor_credentials_rejected" {
  command = plan
  variables {
    monitoring = merge(var.monitoring, { services = merge(var.monitoring.services, { management = merge(var.monitoring.services.management, { runbook_url = "https://secret:password@example.invalid/runbook" }) }) })
  }
  expect_failures = [var.monitoring]
}

run "incomplete_service_monitoring_rejected" {
  command = plan
  variables {
    monitoring = merge(var.monitoring, { services = { runtime = var.monitoring.services.runtime } })
  }
  expect_failures = [var.monitoring]
}

run "strong_single_write_service_owned_containers" {
  command = plan
  assert {
    condition = (
      azurerm_cosmosdb_account.platform.consistency_policy[0].consistency_level == "Strong" &&
      !azurerm_cosmosdb_account.platform.multiple_write_locations_enabled &&
      length(azurerm_cosmosdb_account.platform.geo_location) == 1 &&
      !azurerm_cosmosdb_account.platform.local_authentication_enabled &&
      !azurerm_cosmosdb_account.platform.public_network_access_enabled &&
      toset(azurerm_cosmosdb_sql_container.service["configuration"].partition_key_paths) == toset(["/organizationId"]) &&
      toset(azurerm_cosmosdb_sql_container.service["configuration-audit"].partition_key_paths) == toset(["/dayBucket"]) &&
      toset(azurerm_cosmosdb_sql_container.service["call-sessions"].partition_key_paths) == toset(["/sessionId"]) &&
      toset(azurerm_cosmosdb_sql_container.service["call-audit"].partition_key_paths) == toset(["/dayBucket"])
    )
    error_message = "Cosmos authority and service ownership require Strong consistency, one write region, Entra and exact private-container partitions."
  }
}

run "private_registry_vault_monitor_and_explicit_retention" {
  command = plan
  assert {
    condition = (
      !azurerm_container_registry.platform.admin_enabled &&
      !azurerm_container_registry.platform.public_network_access_enabled &&
      azurerm_container_registry.platform.sku == "Premium" &&
      azurerm_key_vault.platform.rbac_authorization_enabled &&
      azurerm_key_vault.platform.purge_protection_enabled &&
      !azurerm_key_vault.platform.public_network_access_enabled &&
      azurerm_key_vault.platform.soft_delete_retention_days == var.services.vault_soft_delete_days &&
      azurerm_log_analytics_workspace.platform.local_authentication_enabled == false &&
      azurerm_log_analytics_workspace.platform.internet_ingestion_access_type == "Disabled" &&
      azurerm_log_analytics_workspace.platform.internet_query_access_type == "Disabled" &&
      azurerm_log_analytics_workspace.platform.retention_in_days == var.services.log_retention_days &&
      azurerm_cosmosdb_sql_container.service["call-sessions"].default_ttl == var.services.call_session_ttl &&
      azurerm_cosmosdb_sql_container.service["configuration"].default_ttl == -1
    )
    error_message = "Registry/vault/monitor access must fail closed and metadata retention must be explicitly supplied."
  }
}

run "empty_admin_groups_rejected" {
  command = plan
  variables {
    services = merge(var.services, { admin_group_ids = [] })
  }
  expect_failures = [var.services]
}

run "invalid_metadata_retention_rejected" {
  command = plan
  variables {
    services = merge(var.services, { call_session_ttl = 0 })
  }
  expect_failures = [var.services]
}

run "unsafe_node_count_rejected" {
  command = plan
  variables {
    services = merge(var.services, { runtime_node_count = 0 })
  }
  expect_failures = [var.services]
}

run "inconsistent_dns_rejected" {
  command = plan
  variables {
    services = merge(var.services, { dns_service_ip = "8.8.8.8" })
  }
  expect_failures = [var.services]
}

run "service_data_and_secret_boundaries" {
  command = plan
  assert {
    condition = (
      length(azurerm_cosmosdb_sql_role_assignment.service) == 4 &&
      local.container_owners["configuration"] == "management" &&
      local.container_owners["configuration-audit"] == "management" &&
      local.container_owners["call-sessions"] == "runtime" &&
      local.container_owners["call-audit"] == "runtime" &&
      length(azurerm_user_assigned_identity.workload) == 3 &&
      alltrue([for grant in azurerm_role_assignment.management_secret : grant.role_definition_name == "Key Vault Secrets User"]) &&
      length(azurerm_role_assignment.management_secret) == length(var.security.management_secret_names)
    )
    error_message = "Workloads need distinct identities, only their owned containers and exact management-secret grants."
  }
}

run "private_paths_and_controlled_egress" {
  command = plan
  assert {
    condition = (
      length(azurerm_private_endpoint.data) == 4 &&
      length(azurerm_private_dns_zone_virtual_network_link.service) == 8 &&
      azurerm_monitor_private_link_scope.platform.ingestion_access_mode == "PrivateOnly" &&
      azurerm_monitor_private_link_scope.platform.query_access_mode == "PrivateOnly" &&
      azurerm_route.egress.address_prefix == "0.0.0.0/0" &&
      azurerm_route.egress.next_hop_type == "VirtualAppliance" &&
      azurerm_firewall_policy.platform.threat_intelligence_mode == "Deny" &&
      toset(azurerm_firewall_policy_rule_collection_group.egress.application_rule_collection[0].rule[0].destination_fqdns) == var.security.approved_external_fqdns
    )
    error_message = "Private service paths and dedicated deny-by-default reviewed HTTPS egress are required."
  }
}

run "exact_federation_and_operator_roles" {
  command = plan
  assert {
    condition = (
      length(azurerm_federated_identity_credential.workload) == 3 &&
      alltrue([for name, credential in azurerm_federated_identity_credential.workload : credential.subject == "system:serviceaccount:${var.security.workloads[name].namespace}:${var.security.workloads[name].service_account}" && toset(credential.audience) == toset(["api://AzureADTokenExchange"])]) &&
      toset(keys(azuread_app_role_assignment.service)) == toset(["runtime", "operations"]) &&
      azuread_app_role_assignment.service["runtime"].app_role_id == var.security.gateway_app_role_id &&
      azuread_app_role_assignment.service["operations"].app_role_id == var.security.operations_app_role_id &&
      azuread_app_role_assignment.service["operations"].resource_object_id != azuread_app_role_assignment.service["runtime"].resource_object_id &&
      length(azurerm_cosmosdb_sql_role_assignment.metadata) == 2 &&
      alltrue([for role in azurerm_cosmosdb_sql_role_definition.items : length(role.permissions) == 1 && length(one(role.permissions).data_actions) == 3 && !anytrue([for action in one(role.permissions).data_actions : strcontains(action, "*")])])
    )
    error_message = "Exact federation, separate API roles and non-wildcard owned item permissions are required."
  }
}

run "wildcard_egress_rejected" {
  command = plan
  variables {
    security = merge(var.security, { approved_external_fqdns = ["*.example.invalid"] })
  }
  expect_failures = [var.security]
}

run "shared_workload_subject_rejected" {
  command = plan
  variables {
    security = merge(var.security, { workloads = merge(var.security.workloads, { operations = var.security.workloads.runtime }) })
  }
  expect_failures = [var.security]
}

run "actual_container_secret_and_api_grant_bindings" {
  command = plan
  override_resource {
    target          = azurerm_cosmosdb_account.platform
    values          = { id = "/subscriptions/00000000-0000-4000-8000-000000000001/resourceGroups/rg-caller-platform-fixture/providers/Microsoft.DocumentDB/databaseAccounts/cosmos-caller-fixture" }
    override_during = plan
  }
  override_resource {
    target          = azurerm_user_assigned_identity.workload["runtime"]
    values          = { principal_id = "00000000-0000-4000-8000-000000000030", id = "/subscriptions/00000000-0000-4000-8000-000000000001/resourceGroups/rg-caller-platform-fixture/providers/Microsoft.ManagedIdentity/userAssignedIdentities/runtime" }
    override_during = plan
  }
  override_resource {
    target          = azurerm_user_assigned_identity.workload["management"]
    values          = { principal_id = "00000000-0000-4000-8000-000000000031", id = "/subscriptions/00000000-0000-4000-8000-000000000001/resourceGroups/rg-caller-platform-fixture/providers/Microsoft.ManagedIdentity/userAssignedIdentities/management" }
    override_during = plan
  }
  override_resource {
    target          = azurerm_user_assigned_identity.workload["operations"]
    values          = { principal_id = "00000000-0000-4000-8000-000000000032", id = "/subscriptions/00000000-0000-4000-8000-000000000001/resourceGroups/rg-caller-platform-fixture/providers/Microsoft.ManagedIdentity/userAssignedIdentities/operations" }
    override_during = plan
  }
  override_resource {
    target          = azurerm_key_vault.platform
    values          = { id = "/subscriptions/00000000-0000-4000-8000-000000000001/resourceGroups/rg-caller-platform-fixture/providers/Microsoft.KeyVault/vaults/kv-caller-fixture" }
    override_during = plan
  }
  assert {
    condition = (
      alltrue([for name, grant in azurerm_cosmosdb_sql_role_assignment.service : grant.scope == "${azurerm_cosmosdb_account.platform.id}/dbs/caller-ai/colls/${name}" && grant.principal_id == (startswith(name, "configuration") ? "00000000-0000-4000-8000-000000000031" : "00000000-0000-4000-8000-000000000030")]) &&
      toset(keys(azurerm_cosmosdb_sql_role_assignment.metadata)) == toset(["runtime", "management"]) &&
      alltrue([for name, grant in azurerm_role_assignment.management_secret : grant.scope == "${azurerm_key_vault.platform.id}/secrets/${name}" && grant.principal_id == "00000000-0000-4000-8000-000000000031"]) &&
      azuread_app_role_assignment.service["runtime"].principal_object_id == "00000000-0000-4000-8000-000000000030" &&
      azuread_app_role_assignment.service["operations"].principal_object_id == "00000000-0000-4000-8000-000000000032"
    )
    error_message = "Actual role assignments must bind only the intended identity to exact owned containers, secret names and API roles."
  }
}

run "numeric_external_destination_rejected" {
  command = plan
  variables {
    security = merge(var.security, { approved_external_fqdns = ["169.254.169.254"] })
  }
  expect_failures = [var.security]
}

run "undersized_firewall_subnet_rejected" {
  command = plan
  variables {
    security = merge(var.security, { firewall_subnet_cidr = "10.50.3.0/27" })
  }
  expect_failures = [var.security]
}
