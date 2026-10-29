variable "tenant_id" {
  type        = string
  description = "Approved Entra tenant for cluster administration and Key Vault."
  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", var.tenant_id))
    error_message = "Supply an approved tenant UUID."
  }
}

variable "services" {
  description = "Required reviewed development sizing, network references and retention; no production defaults."
  type = object({
    aks_name                = string
    acr_name                = string
    cosmos_name             = string
    key_vault_name          = string
    monitor_name            = string
    kubernetes_version      = string
    aks_subnet_id           = string
    admin_group_ids         = set(string)
    system_vm_size          = string
    runtime_vm_size         = string
    system_node_count       = number
    runtime_node_count      = number
    service_cidr            = string
    pod_cidr                = string
    dns_service_ip          = string
    log_retention_days      = number
    vault_soft_delete_days  = number
    acr_retention_days      = number
    cosmos_max_throughput   = number
    cosmos_backup_tier      = string
    configuration_audit_ttl = number
    call_session_ttl        = number
    call_audit_ttl          = number
  })

  validation {
    condition = (
      can(regex("^[a-z][a-z0-9-]{2,47}$", var.services.aks_name)) &&
      can(regex("^[a-z0-9]{5,50}$", var.services.acr_name)) &&
      can(regex("^[a-z0-9][a-z0-9-]{1,42}[a-z0-9]$", var.services.cosmos_name)) &&
      can(regex("^[a-z][a-z0-9-]{1,22}[a-z0-9]$", var.services.key_vault_name)) &&
      can(regex("^[A-Za-z0-9][A-Za-z0-9-]{2,61}[A-Za-z0-9]$", var.services.monitor_name))
    )
    error_message = "Supply reviewed Azure resource names; global uniqueness is separately qualified."
  }

  validation {
    condition = (
      can(regex("^[0-9]+\\.[0-9]+\\.[0-9]+$", var.services.kubernetes_version)) &&
      alltrue([for size in [var.services.system_vm_size, var.services.runtime_vm_size] : can(regex("^Standard_[A-Za-z0-9_]+$", size))]) &&
      var.services.system_node_count >= 2 && var.services.system_node_count <= 10 && floor(var.services.system_node_count) == var.services.system_node_count &&
      var.services.runtime_node_count >= 1 && var.services.runtime_node_count <= 20 && floor(var.services.runtime_node_count) == var.services.runtime_node_count
    )
    error_message = "Supply a pinned Kubernetes patch, reviewed VM sizes and bounded integral fixed node counts."
  }

  validation {
    condition = (
      length(var.services.admin_group_ids) > 0 && length(var.services.admin_group_ids) <= 16 &&
      alltrue([for id in var.services.admin_group_ids : can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", id))]) &&
      can(regex("^/subscriptions/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/resourceGroups/[^/]+/providers/Microsoft.Network/virtualNetworks/[^/]+/subnets/[^/]+$", var.services.aks_subnet_id)) &&
      can(cidrnetmask(var.services.service_cidr)) && can(cidrnetmask(var.services.pod_cidr)) &&
      try(cidrhost(var.services.service_cidr, 10) == var.services.dns_service_ip, false) &&
      var.services.service_cidr != var.services.pod_cidr
    )
    error_message = "Require approved admin groups/subnet and explicit IPv4 service/pod ranges with DNS at service host 10."
  }

  validation {
    condition = (
      var.services.log_retention_days >= 30 && var.services.log_retention_days <= 730 && floor(var.services.log_retention_days) == var.services.log_retention_days &&
      var.services.vault_soft_delete_days >= 7 && var.services.vault_soft_delete_days <= 90 && floor(var.services.vault_soft_delete_days) == var.services.vault_soft_delete_days &&
      var.services.acr_retention_days >= 7 && var.services.acr_retention_days <= 365 && floor(var.services.acr_retention_days) == var.services.acr_retention_days &&
      alltrue([for ttl in [var.services.configuration_audit_ttl, var.services.call_session_ttl, var.services.call_audit_ttl] : ttl >= 3600 && ttl <= 2147483647 && floor(ttl) == ttl]) &&
      contains(["Continuous7Days", "Continuous30Days"], var.services.cosmos_backup_tier)
    )
    error_message = "Supply explicitly approved integral metadata/log/vault/registry retention and Cosmos continuous-backup tier."
  }

  validation {
    condition     = var.services.cosmos_max_throughput >= 1000 && var.services.cosmos_max_throughput <= 1000000 && var.services.cosmos_max_throughput % 1000 == 0
    error_message = "Supply a reviewed Cosmos autoscale RU ceiling from 1000 to 1000000 in 1000-RU increments."
  }
}

locals {
  service_containers = {
    configuration       = { partition = "/organizationId", ttl = -1 }
    configuration-audit = { partition = "/dayBucket", ttl = var.services.configuration_audit_ttl }
    call-sessions       = { partition = "/sessionId", ttl = var.services.call_session_ttl }
    call-audit          = { partition = "/dayBucket", ttl = var.services.call_audit_ttl }
  }
}

resource "azurerm_log_analytics_workspace" "platform" {
  name                           = var.services.monitor_name
  resource_group_name            = azurerm_resource_group.platform.name
  location                       = azurerm_resource_group.platform.location
  sku                            = "PerGB2018"
  retention_in_days              = var.services.log_retention_days
  local_authentication_enabled   = false
  internet_ingestion_access_type = "Disabled"
  internet_query_access_type     = "Disabled"
  tags                           = azurerm_resource_group.platform.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_container_registry" "platform" {
  name                          = var.services.acr_name
  resource_group_name           = azurerm_resource_group.platform.name
  location                      = azurerm_resource_group.platform.location
  sku                           = "Premium"
  admin_enabled                 = false
  anonymous_pull_enabled        = false
  public_network_access_enabled = false
  network_rule_bypass_option    = "None"
  retention_policy_in_days      = var.services.acr_retention_days
  tags                          = azurerm_resource_group.platform.tags
}

resource "azurerm_key_vault" "platform" {
  name                          = var.services.key_vault_name
  resource_group_name           = azurerm_resource_group.platform.name
  location                      = azurerm_resource_group.platform.location
  tenant_id                     = var.tenant_id
  sku_name                      = "standard"
  rbac_authorization_enabled    = true
  purge_protection_enabled      = true
  public_network_access_enabled = false
  soft_delete_retention_days    = var.services.vault_soft_delete_days
  tags                          = azurerm_resource_group.platform.tags

  network_acls {
    default_action = "Deny"
    bypass         = "None"
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_cosmosdb_account" "platform" {
  name                                  = var.services.cosmos_name
  resource_group_name                   = azurerm_resource_group.platform.name
  location                              = azurerm_resource_group.platform.location
  offer_type                            = "Standard"
  kind                                  = "GlobalDocumentDB"
  automatic_failover_enabled            = false
  multiple_write_locations_enabled      = false
  local_authentication_enabled          = false
  access_key_metadata_writes_enabled    = false
  public_network_access_enabled         = false
  network_acl_bypass_for_azure_services = false
  minimal_tls_version                   = "Tls12"
  tags                                  = azurerm_resource_group.platform.tags

  consistency_policy {
    consistency_level = "Strong"
  }

  geo_location {
    location          = var.location
    failover_priority = 0
    zone_redundant    = true
  }

  backup {
    type = "Continuous"
    tier = var.services.cosmos_backup_tier
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_cosmosdb_sql_database" "platform" {
  name                = "caller-ai"
  resource_group_name = azurerm_resource_group.platform.name
  account_name        = azurerm_cosmosdb_account.platform.name
  autoscale_settings {
    max_throughput = var.services.cosmos_max_throughput
  }
}

resource "azurerm_cosmosdb_sql_container" "service" {
  for_each              = local.service_containers
  name                  = each.key
  resource_group_name   = azurerm_resource_group.platform.name
  account_name          = azurerm_cosmosdb_account.platform.name
  database_name         = azurerm_cosmosdb_sql_database.platform.name
  partition_key_paths   = [each.value.partition]
  partition_key_kind    = "Hash"
  partition_key_version = 2
  default_ttl           = each.value.ttl

  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_kubernetes_cluster" "platform" {
  name                                = var.services.aks_name
  resource_group_name                 = azurerm_resource_group.platform.name
  location                            = azurerm_resource_group.platform.location
  dns_prefix                          = var.services.aks_name
  kubernetes_version                  = var.services.kubernetes_version
  sku_tier                            = "Standard"
  private_cluster_enabled             = true
  private_cluster_public_fqdn_enabled = false
  private_dns_zone_id                 = azurerm_private_dns_zone.aks.id
  local_account_disabled              = true
  role_based_access_control_enabled   = true
  oidc_issuer_enabled                 = true
  workload_identity_enabled           = true
  node_os_upgrade_channel             = "None"
  tags                                = azurerm_resource_group.platform.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.control["control-plane"].id]
  }

  kubelet_identity {
    client_id                 = azurerm_user_assigned_identity.control["kubelet"].client_id
    object_id                 = azurerm_user_assigned_identity.control["kubelet"].principal_id
    user_assigned_identity_id = azurerm_user_assigned_identity.control["kubelet"].id
  }

  depends_on = [azurerm_role_assignment.control_network, azurerm_role_assignment.control_dns,
    azurerm_role_assignment.control_kubelet, azurerm_private_dns_zone_virtual_network_link.aks,
  azurerm_subnet_route_table_association.nodes]

  node_provisioning_profile {
    mode = "Manual"
  }

  azure_active_directory_role_based_access_control {
    tenant_id              = var.tenant_id
    azure_rbac_enabled     = true
    admin_group_object_ids = var.services.admin_group_ids
  }

  default_node_pool {
    name                        = "system"
    vm_size                     = var.services.system_vm_size
    node_count                  = var.services.system_node_count
    auto_scaling_enabled        = false
    node_public_ip_enabled      = false
    vnet_subnet_id              = var.services.aks_subnet_id
    temporary_name_for_rotation = "sysrotate"
    node_labels                 = { "caller-ai/workload" = "management" }
  }

  network_profile {
    network_plugin      = "azure"
    network_plugin_mode = "overlay"
    network_data_plane  = "cilium"
    network_policy      = "cilium"
    outbound_type       = "userDefinedRouting"
    service_cidr        = var.services.service_cidr
    pod_cidr            = var.services.pod_cidr
    dns_service_ip      = var.services.dns_service_ip
  }
}

resource "azurerm_kubernetes_cluster_node_pool" "runtime" {
  name                   = "runtime"
  kubernetes_cluster_id  = azurerm_kubernetes_cluster.platform.id
  vm_size                = var.services.runtime_vm_size
  node_count             = var.services.runtime_node_count
  orchestrator_version   = var.services.kubernetes_version
  auto_scaling_enabled   = false
  node_public_ip_enabled = false
  vnet_subnet_id         = var.services.aks_subnet_id
  mode                   = "User"
  node_labels            = { "caller-ai/workload" = "runtime" }
  node_taints            = ["caller-ai/runtime=true:NoSchedule"]
  tags                   = azurerm_resource_group.platform.tags
}
