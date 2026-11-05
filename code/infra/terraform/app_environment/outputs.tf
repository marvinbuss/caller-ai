output "environment_manifest" {
  description = "Non-secret description of the deployed environment for the environment check. It holds no secret value."
  value = {
    environment         = var.environment.name
    resource_group_name = azurerm_resource_group.apps.name
    container_apps = {
      runtime = {
        name         = local.apps.runtime.app_name
        host         = local.runtime_host
        image        = coalesce(local.apps.runtime.image.tag, local.apps.runtime.image.digest)
        id           = module.runtime_app.id
        min_replicas = 1
        max_replicas = 1
      }
      management = {
        name         = local.apps.management.app_name
        host         = local.management_host
        image        = coalesce(local.apps.management.image.tag, local.apps.management.image.digest)
        id           = module.management_app.id
        min_replicas = 1
        max_replicas = 1
      }
    }
    public_access = {
      enabled       = var.environment.public_access.enabled
      allowed_cidrs = var.environment.public_access.allowed_cidrs
    }
    services = {
      data_store_id         = module.data_store.id
      secret_store_id       = module.secret_store.id
      workspace_id          = module.monitoring.workspace_id
      action_group_id       = module.monitoring.action_group_id
      model_account_id      = var.app_environment.model.account_id
      container_environment = module.container_app_environment.id
    }
    private_endpoints = [
      module.data_store_endpoint.id,
      module.secret_store_endpoint.id,
      module.model_endpoint.id,
    ]
    identity = {
      tenant_id                = var.environment.tenant_id
      management_app_client_id = var.app_environment.identity.management_app_client_id
      runtime_identity_name    = var.app_environment.identity.runtime_identity_name
      runtime_principal_id     = data.azurerm_user_assigned_identity.runtime.principal_id
      roles                    = var.app_environment.identity.roles
    }
    redirect_uri       = "${local.management_origin}/auth/callback"
    vault_secret_names = ["management-signin-secret", "permit-private-key"]
    network = {
      network_security_group_id = local.network.network_security_group_id
      route_table_id            = local.network.route_table_id
    }
  }
}

output "workload_identity_client_ids" {
  description = "Client IDs of the user-assigned identities of the two applications."
  value = {
    runtime    = data.azurerm_user_assigned_identity.runtime.client_id
    management = module.management_identity.client_id
  }
}
