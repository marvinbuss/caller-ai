variable "subscription_id" {
  type        = string
  description = "Explicit approved development subscription, never inferred from a CLI session."
  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", var.subscription_id))
    error_message = "Supply an approved subscription UUID."
  }
}

variable "tenant_id" {
  type        = string
  description = "Explicit approved Entra tenant for platform administration."
  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", var.tenant_id))
    error_message = "Supply an approved tenant UUID."
  }
}

variable "client_id" {
  type        = string
  description = "Approved federated provisioning client ID; no secret/token variable is accepted."
  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", var.client_id))
    error_message = "Supply an approved federated client UUID."
  }
}

variable "location" {
  type        = string
  description = "Approved Azure region; test fixtures and syntax checks confer no residency approval."
}

variable "resource_group_name" {
  type        = string
  description = "Reviewed dedicated platform resource group, separate from state administration."
}

variable "tags" {
  type        = map(string)
  description = "Nonsecret governance metadata including an accountable opaque owner reference."
}

variable "services" {
  description = "Explicit approved development resource names, sizes, network references and retention."
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
}

variable "security" {
  description = "Explicit reviewed private network, workload, exact API-role and egress references; never credentials."
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
}

variable "monitoring" {
  description = "Required reviewed per-service owners, published runbooks, existing action-group IDs, bounded thresholds and cost cap; module validates and leaves alerts disabled."
  type = object({
    daily_cap_gb = number
    services = map(object({
      owner                = string
      runbook_url          = string
      action_group_id      = string
      error_ratio          = number
      mean_latency_seconds = number
      dependency_failures  = number
      saturation_ratio     = number
      minimum_requests     = number
    }))
  })
}
