variable "security" {
  description = "Explicit reviewed identity, existing network/DNS, API-role and public egress references."
  type = object({
    vnet_id                         = string
    network_resource_group_name     = string
    vnet_name                       = string
    node_subnet_cidr                = string
    private_endpoint_subnet_cidr    = string
    firewall_subnet_cidr            = string
    approved_external_fqdns         = set(string)
    private_dns_zone_ids            = object({ cosmos = string, vault = string, acr = string, monitor = set(string) })
    workloads                       = map(object({ namespace = string, service_account = string }))
    management_secret_names         = set(string)
    model_account_id                = string
    gateway_service_principal_id    = string
    gateway_app_role_id             = string
    operations_service_principal_id = string
    operations_app_role_id          = string
  })

  validation {
    condition = (
      toset(keys(var.security.workloads)) == toset(["runtime", "management", "operations"]) &&
      length(toset([for binding in values(var.security.workloads) : "${binding.namespace}/${binding.service_account}"])) == 3 &&
      alltrue([for binding in values(var.security.workloads) : can(regex("^[a-z][a-z0-9-]{0,61}[a-z0-9]$", binding.namespace)) && can(regex("^[a-z][a-z0-9-]{0,61}[a-z0-9]$", binding.service_account))])
    )
    error_message = "Require exactly three distinct explicitly named runtime/management/operations workload subjects, without wildcards."
  }

  validation {
    condition = (
      length(var.security.approved_external_fqdns) > 0 && length(var.security.approved_external_fqdns) <= 32 &&
      alltrue([for name in var.security.approved_external_fqdns : can(regex("^[a-z0-9][a-z0-9-]*(\\.[a-z0-9][a-z0-9-]*)+$", name)) && can(regex("\\.[a-z][a-z0-9-]*$", name)) && !endswith(name, ".local") && !endswith(name, ".internal")]) &&
      length(var.security.management_secret_names) <= 32 &&
      alltrue([for name in var.security.management_secret_names : can(regex("^[a-z][a-z0-9-]{2,62}$", name))])
    )
    error_message = "Require exact reviewed external FQDNs without wildcard/scheme/path and explicit bounded management secret names."
  }

  validation {
    condition     = alltrue([for id in [var.security.gateway_service_principal_id, var.security.gateway_app_role_id, var.security.operations_service_principal_id, var.security.operations_app_role_id] : can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", id))]) && var.security.gateway_service_principal_id != var.security.operations_service_principal_id
    error_message = "Require explicitly reviewed distinct gateway/operations service principals and exact app-role UUIDs."
  }

  validation {
    condition = (
      alltrue([for cidr in [var.security.node_subnet_cidr, var.security.private_endpoint_subnet_cidr, var.security.firewall_subnet_cidr] : can(cidrnetmask(cidr)) && try(tonumber(split("/", cidr)[1]) >= 16 && tonumber(split("/", cidr)[1]) <= 27 && can(regex("^(10\\.|172\\.(1[6-9]|2[0-9]|3[01])\\.|192\\.168\\.)", cidrhost(cidr, 0))), false)]) &&
      length(toset([var.security.node_subnet_cidr, var.security.private_endpoint_subnet_cidr, var.security.firewall_subnet_cidr])) == 3 &&
      try(tonumber(split("/", var.security.firewall_subnet_cidr)[1]) <= 26, false) &&
      var.security.vnet_id == "${join("/", slice(split("/", var.services.aks_subnet_id), 0, 5))}/providers/Microsoft.Network/virtualNetworks/${var.security.vnet_name}" &&
      var.security.network_resource_group_name == split("/", var.services.aks_subnet_id)[4] &&
      can(regex("^[A-Za-z0-9][A-Za-z0-9_-]{0,89}$", var.security.network_resource_group_name)) &&
      can(regex("^/subscriptions/[0-9a-fA-F-]+/resourceGroups/[^/]+/providers/Microsoft.CognitiveServices/accounts/[a-z0-9-]+$", var.security.model_account_id))
    )
    error_message = "Require reviewed private IPv4 subnet ranges, the AKS VNet and a specific approved Azure model account."
  }
}

locals {
  container_owners = {
    configuration       = "management"
    configuration-audit = "management"
    call-sessions       = "runtime"
    call-audit          = "runtime"
    text-conversations  = "runtime"
  }
}

resource "azurerm_user_assigned_identity" "workload" {
  for_each            = var.security.workloads
  name                = "${var.services.aks_name}-${each.key}"
  resource_group_name = azurerm_resource_group.platform.name
  location            = azurerm_resource_group.platform.location
  tags                = azurerm_resource_group.platform.tags
}

resource "azurerm_federated_identity_credential" "workload" {
  for_each                  = var.security.workloads
  name                      = "aks-${each.key}"
  user_assigned_identity_id = azurerm_user_assigned_identity.workload[each.key].id
  issuer                    = azurerm_kubernetes_cluster.platform.oidc_issuer_url
  audience                  = ["api://AzureADTokenExchange"]
  subject                   = "system:serviceaccount:${each.value.namespace}:${each.value.service_account}"
}

resource "azurerm_cosmosdb_sql_role_definition" "items" {
  for_each            = toset(["runtime", "management"])
  name                = "caller-${each.key}-owned-items"
  resource_group_name = azurerm_resource_group.platform.name
  account_name        = azurerm_cosmosdb_account.platform.name
  type                = "CustomRole"
  assignable_scopes   = [azurerm_cosmosdb_account.platform.id]
  permissions {
    data_actions = [
      "Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/items/read",
      "Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/items/create",
      "Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/items/replace"
    ]
  }
}

resource "azurerm_cosmosdb_sql_role_assignment" "service" {
  for_each            = local.container_owners
  resource_group_name = azurerm_resource_group.platform.name
  account_name        = azurerm_cosmosdb_account.platform.name
  principal_id        = azurerm_user_assigned_identity.workload[each.value].principal_id
  role_definition_id  = azurerm_cosmosdb_sql_role_definition.items[each.value].id
  scope               = "${azurerm_cosmosdb_account.platform.id}/dbs/${azurerm_cosmosdb_sql_database.platform.name}/colls/${each.key}"
}

resource "azurerm_cosmosdb_sql_role_definition" "metadata" {
  name                = "caller-account-metadata-only"
  resource_group_name = azurerm_resource_group.platform.name
  account_name        = azurerm_cosmosdb_account.platform.name
  type                = "CustomRole"
  assignable_scopes   = [azurerm_cosmosdb_account.platform.id]
  permissions {
    data_actions = ["Microsoft.DocumentDB/databaseAccounts/readMetadata"]
  }
}

resource "azurerm_cosmosdb_sql_role_assignment" "metadata" {
  for_each            = toset(["runtime", "management"])
  resource_group_name = azurerm_resource_group.platform.name
  account_name        = azurerm_cosmosdb_account.platform.name
  principal_id        = azurerm_user_assigned_identity.workload[each.value].principal_id
  role_definition_id  = azurerm_cosmosdb_sql_role_definition.metadata.id
  scope               = azurerm_cosmosdb_account.platform.id
}

resource "azurerm_role_assignment" "management_secret" {
  for_each             = var.security.management_secret_names
  scope                = "${azurerm_key_vault.platform.id}/secrets/${each.value}"
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.workload["management"].principal_id
}

resource "azurerm_role_assignment" "runtime_model" {
  scope                = var.security.model_account_id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = azurerm_user_assigned_identity.workload["runtime"].principal_id
}

locals {
  application_roles = {
    runtime    = { principal = var.security.gateway_service_principal_id, role = var.security.gateway_app_role_id }
    operations = { principal = var.security.operations_service_principal_id, role = var.security.operations_app_role_id }
  }
}

resource "azuread_app_role_assignment" "service" {
  for_each            = local.application_roles
  principal_object_id = azurerm_user_assigned_identity.workload[each.key].principal_id
  resource_object_id  = each.value.principal
  app_role_id         = each.value.role
}

output "workload_identity_client_ids" {
  description = "Nonsecret deployment references; not identity activation or a readiness assertion."
  value       = { for name, identity in azurerm_user_assigned_identity.workload : name => identity.client_id }
}

resource "azurerm_user_assigned_identity" "control" {
  for_each            = toset(["control-plane", "kubelet"])
  name                = "${var.services.aks_name}-${each.key}"
  resource_group_name = azurerm_resource_group.platform.name
  location            = azurerm_resource_group.platform.location
  tags                = azurerm_resource_group.platform.tags
}

resource "azurerm_private_dns_zone" "aks" {
  name                = "privatelink.${var.location}.azmk8s.io"
  resource_group_name = azurerm_resource_group.platform.name
  tags                = azurerm_resource_group.platform.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "aks" {
  name                 = "caller-aks"
  private_dns_zone_id  = azurerm_private_dns_zone.aks.id
  virtual_network_id   = var.security.vnet_id
  registration_enabled = false
}

resource "azurerm_role_assignment" "control_network" {
  scope                = var.security.vnet_id
  role_definition_name = "Network Contributor"
  principal_id         = azurerm_user_assigned_identity.control["control-plane"].principal_id
}

resource "azurerm_role_assignment" "control_dns" {
  scope                = azurerm_private_dns_zone.aks.id
  role_definition_name = "Private DNS Zone Contributor"
  principal_id         = azurerm_user_assigned_identity.control["control-plane"].principal_id
}

resource "azurerm_role_assignment" "control_kubelet" {
  scope                = azurerm_user_assigned_identity.control["kubelet"].id
  role_definition_name = "Managed Identity Operator"
  principal_id         = azurerm_user_assigned_identity.control["control-plane"].principal_id
}

resource "azurerm_role_assignment" "kubelet_pull" {
  scope                = azurerm_container_registry.platform.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.control["kubelet"].principal_id
}

resource "azurerm_subnet" "private" {
  name                              = "caller-private-endpoints"
  resource_group_name               = var.security.network_resource_group_name
  virtual_network_name              = var.security.vnet_name
  address_prefixes                  = [var.security.private_endpoint_subnet_cidr]
  private_endpoint_network_policies = "Enabled"
}

resource "azurerm_network_security_group" "private" {
  name                = "${var.services.aks_name}-private-data"
  resource_group_name = azurerm_resource_group.platform.name
  location            = var.location
  tags                = azurerm_resource_group.platform.tags
  security_rule {
    name                       = "nodes-https"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "443"
    source_address_prefix      = var.security.node_subnet_cidr
    destination_address_prefix = "*"
  }
  security_rule {
    name                       = "deny-other-inbound"
    priority                   = 200
    direction                  = "Inbound"
    access                     = "Deny"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }
}

resource "azurerm_subnet_network_security_group_association" "private" {
  subnet_id                 = azurerm_subnet.private.id
  network_security_group_id = azurerm_network_security_group.private.id
}

resource "azurerm_monitor_private_link_scope" "platform" {
  name                  = "${var.services.monitor_name}-private"
  resource_group_name   = azurerm_resource_group.platform.name
  ingestion_access_mode = "PrivateOnly"
  query_access_mode     = "PrivateOnly"
  tags                  = azurerm_resource_group.platform.tags
}

resource "azurerm_monitor_private_link_scoped_service" "workspace" {
  name                = "workspace"
  resource_group_name = azurerm_resource_group.platform.name
  scope_name          = azurerm_monitor_private_link_scope.platform.name
  linked_resource_id  = azurerm_log_analytics_workspace.platform.id
}

locals {
  private_services = {
    cosmos  = { id = azurerm_cosmosdb_account.platform.id, group = "Sql", zones = [var.security.private_dns_zone_ids.cosmos] }
    vault   = { id = azurerm_key_vault.platform.id, group = "vault", zones = [var.security.private_dns_zone_ids.vault] }
    acr     = { id = azurerm_container_registry.platform.id, group = "registry", zones = [var.security.private_dns_zone_ids.acr] }
    monitor = { id = azurerm_monitor_private_link_scope.platform.id, group = "azuremonitor", zones = tolist(var.security.private_dns_zone_ids.monitor) }
  }
  service_dns_zones = setunion(
    toset([var.security.private_dns_zone_ids.cosmos, var.security.private_dns_zone_ids.vault, var.security.private_dns_zone_ids.acr]),
    var.security.private_dns_zone_ids.monitor
  )
}

resource "azurerm_private_dns_zone_virtual_network_link" "service" {
  for_each             = local.service_dns_zones
  name                 = "caller-${var.services.aks_name}"
  private_dns_zone_id  = each.value
  virtual_network_id   = var.security.vnet_id
  registration_enabled = false
}

resource "azurerm_private_endpoint" "data" {
  for_each            = local.private_services
  name                = "${var.services.aks_name}-${each.key}"
  resource_group_name = azurerm_resource_group.platform.name
  location            = var.location
  subnet_id           = azurerm_subnet.private.id
  tags                = azurerm_resource_group.platform.tags
  private_service_connection {
    name                           = each.key
    private_connection_resource_id = each.value.id
    subresource_names              = [each.value.group]
    is_manual_connection           = false
  }
  private_dns_zone_group {
    name                 = each.key
    private_dns_zone_ids = each.value.zones
  }
  depends_on = [azurerm_subnet_network_security_group_association.private, azurerm_monitor_private_link_scoped_service.workspace]
}

resource "azurerm_subnet" "firewall" {
  name                 = "AzureFirewallSubnet"
  resource_group_name  = var.security.network_resource_group_name
  virtual_network_name = var.security.vnet_name
  address_prefixes     = [var.security.firewall_subnet_cidr]
}

resource "azurerm_public_ip" "firewall" {
  name                = "${var.services.aks_name}-egress"
  resource_group_name = azurerm_resource_group.platform.name
  location            = var.location
  allocation_method   = "Static"
  sku                 = "Standard"
  tags                = azurerm_resource_group.platform.tags
}

resource "azurerm_firewall_policy" "platform" {
  name                     = "${var.services.aks_name}-egress"
  resource_group_name      = azurerm_resource_group.platform.name
  location                 = var.location
  sku                      = "Standard"
  threat_intelligence_mode = "Deny"
  tags                     = azurerm_resource_group.platform.tags
  dns {
    proxy_enabled = true
  }
}

resource "azurerm_firewall" "platform" {
  name                = "${var.services.aks_name}-egress"
  resource_group_name = azurerm_resource_group.platform.name
  location            = var.location
  sku_name            = "AZFW_VNet"
  sku_tier            = "Standard"
  firewall_policy_id  = azurerm_firewall_policy.platform.id
  tags                = azurerm_resource_group.platform.tags
  ip_configuration {
    name                 = "egress"
    subnet_id            = azurerm_subnet.firewall.id
    public_ip_address_id = azurerm_public_ip.firewall.id
  }
}

resource "azurerm_firewall_policy_rule_collection_group" "egress" {
  name               = "caller-egress"
  firewall_policy_id = azurerm_firewall_policy.platform.id
  priority           = 200
  application_rule_collection {
    name     = "approved-https"
    priority = 200
    action   = "Allow"
    rule {
      name              = "approved-external"
      source_addresses  = [var.security.node_subnet_cidr]
      destination_fqdns = var.security.approved_external_fqdns
      protocols {
        type = "Https"
        port = 443
      }
    }
    rule {
      name                  = "aks-platform-dependencies"
      source_addresses      = [var.security.node_subnet_cidr]
      destination_fqdn_tags = ["AzureKubernetesService"]
      protocols {
        type = "Https"
        port = 443
      }
    }
  }
}

resource "azurerm_route_table" "egress" {
  name                = "${var.services.aks_name}-egress"
  resource_group_name = azurerm_resource_group.platform.name
  location            = var.location
  tags                = azurerm_resource_group.platform.tags
}

resource "azurerm_route" "egress" {
  name                   = "approved-firewall"
  resource_group_name    = azurerm_resource_group.platform.name
  route_table_name       = azurerm_route_table.egress.name
  address_prefix         = "0.0.0.0/0"
  next_hop_type          = "VirtualAppliance"
  next_hop_in_ip_address = azurerm_firewall.platform.ip_configuration[0].private_ip_address
}

resource "azurerm_subnet_route_table_association" "nodes" {
  subnet_id      = var.services.aks_subnet_id
  route_table_id = azurerm_route_table.egress.id
  depends_on     = [azurerm_route.egress, azurerm_firewall_policy_rule_collection_group.egress]
}
