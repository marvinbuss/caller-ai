module "container_app_environment" {
  source = "../modules/container-app-environment"

  name                       = local.apps.environment_name
  resource_group_name        = azurerm_resource_group.apps.name
  location                   = var.environment.location
  infrastructure_subnet_id   = local.network.container_apps_subnet_id
  log_analytics_workspace_id = module.monitoring.workspace_id
  public_access_enabled      = var.environment.public_access.enabled
  tags                       = local.tags
}

module "runtime_app" {
  source = "../modules/container-app"

  name                         = local.apps.runtime.app_name
  resource_group_name          = azurerm_resource_group.apps.name
  container_app_environment_id = module.container_app_environment.id
  user_assigned_identity_id    = data.azurerm_user_assigned_identity.runtime.id
  image_repository             = local.apps.runtime.image.repository
  image_tag                    = local.apps.runtime.image.tag
  image_digest                 = local.apps.runtime.image.digest
  cpu                          = local.apps.runtime.cpu
  memory                       = local.apps.runtime.memory
  target_port                  = 8000
  public_access_enabled        = var.environment.public_access.enabled
  allowed_cidrs                = var.environment.public_access.allowed_cidrs
  tags                         = local.tags

  settings = {
    CALLER_MODE                     = "live"
    AZURE_CLIENT_ID                 = data.azurerm_user_assigned_identity.runtime.client_id
    CALLER_REVIEW_REFERENCE         = var.app_environment.application.review_reference
    CALLER_COSMOS_ENDPOINT          = module.data_store.endpoint
    CALLER_COSMOS_DATABASE          = module.data_store.database_name
    CALLER_TEXT_HISTORY_TTL_SECONDS = tostring(var.app_environment.application.text_history_ttl)
    CALLER_MANAGEMENT_ENDPOINT      = local.management_origin
    CALLER_MANAGEMENT_AUDIENCE      = var.app_environment.identity.management_app_client_id
    CALLER_TEXT_ENDPOINT            = local.text_endpoint
    CALLER_TEXT_DEPLOYMENT          = var.app_environment.model.text_deployment
    CALLER_LIVE_ENDPOINT            = local.live_endpoint
    CALLER_LIVE_REGION              = var.app_environment.application.live_region
    CALLER_LIVE_VOICE               = var.app_environment.application.voice
    CALLER_APPROVED_HOSTS           = join(",", [local.management_host, "${local.model_account_name}.openai.azure.com", "${local.model_account_name}.services.ai.azure.com"])
    CALLER_MAX_INPUT_TOKENS         = tostring(var.app_environment.application.max_input_tokens)
    CALLER_MAX_OUTPUT_TOKENS        = tostring(var.app_environment.application.max_output_tokens)
    CALLER_AVAILABLE_LIVE_SESSIONS  = tostring(var.app_environment.application.available_live_sessions)
    CALLER_MANAGER_ISSUER           = local.management_origin
    CALLER_MANAGER_AUDIENCE         = local.runtime_origin
    CALLER_PERMIT_VERIFICATION_KEYS = jsonencode(merge(var.app_environment.signing_key.previous_public_keys, { (module.permit_key.key_id) = module.permit_key.public_key_pem }))
    CALLER_MAX_CLOCK_SKEW_SECONDS   = "1"
    CALLER_TENANT_ID                = var.environment.tenant_id
    CALLER_WORKLOAD_AUDIENCE        = var.app_environment.identity.runtime_app_client_id
    CALLER_WORKLOAD_ROLE            = var.app_environment.identity.roles.management
    CALLER_WORKLOAD_PRINCIPAL_ID    = module.management_identity.principal_id
  }

  depends_on = [module.secret_store, module.data_store, module.model_access]
}

module "management_app" {
  source = "../modules/container-app"

  name                         = local.apps.management.app_name
  resource_group_name          = azurerm_resource_group.apps.name
  container_app_environment_id = module.container_app_environment.id
  user_assigned_identity_id    = module.management_identity.id
  image_repository             = local.apps.management.image.repository
  image_tag                    = local.apps.management.image.tag
  image_digest                 = local.apps.management.image.digest
  cpu                          = local.apps.management.cpu
  memory                       = local.apps.management.memory
  target_port                  = 3000
  public_access_enabled        = var.environment.public_access.enabled
  allowed_cidrs                = var.environment.public_access.allowed_cidrs
  tags                         = local.tags

  settings = {
    CALLER_MODE                    = "live"
    AZURE_CLIENT_ID                = module.management_identity.client_id
    CALLER_REVIEW_REFERENCE        = var.app_environment.application.review_reference
    CALLER_REVIEWED_FINGERPRINT    = var.app_environment.application.reviewed_fingerprint
    CALLER_COSMOS_ENDPOINT         = module.data_store.endpoint
    CALLER_COSMOS_DATABASE         = module.data_store.database_name
    CALLER_AUDIT_RETENTION_SECONDS = tostring(var.app_environment.data_store.configuration_audit_ttl)
    CALLER_TENANT_ID               = var.environment.tenant_id
    CALLER_ENTRA_CLIENT_ID         = var.app_environment.identity.management_app_client_id
    CALLER_API_AUDIENCE            = var.app_environment.identity.management_app_client_id
    CALLER_MANAGER_ROLE            = var.app_environment.identity.roles.editor
    CALLER_MANAGER_SCOPE           = "api://${var.app_environment.identity.management_app_client_id}/.default"
    CALLER_TEST_ROLE               = var.app_environment.identity.roles.tester
    CALLER_RUNTIME_ROLE            = var.app_environment.identity.roles.runtime
    CALLER_MANAGEMENT_ORIGIN       = local.management_origin
    CALLER_PUBLIC_ORIGIN           = local.runtime_origin
    CALLER_RUNTIME_ENDPOINT        = local.runtime_origin
    CALLER_RUNTIME_AUDIENCE        = var.app_environment.identity.runtime_app_client_id
    CALLER_PERMIT_ISSUER           = local.management_origin
    CALLER_PERMIT_AUDIENCE         = local.runtime_origin
    CALLER_SIGNING_KEY_ID          = module.permit_key.key_id
  }

  # Secrets reach the container only as Key Vault references.
  secret_references = {
    CALLER_ENTRA_CLIENT_SECRET = module.signin_secret.secret_id
    CALLER_SIGNING_KEY_PEM     = module.permit_private_key_secret.secret_id
  }

  depends_on = [module.secret_store, module.data_store]
}
