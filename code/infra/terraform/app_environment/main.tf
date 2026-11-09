locals {
  tags = merge(var.environment.tags, { environment = var.environment.name })

  apps = var.app_environment.container_apps

  # Origins are computed inside the deployment so no address is copied by hand (FR-049).
  runtime_host      = "${local.apps.runtime.app_name}.${module.container_app_environment.default_domain}"
  management_host   = "${local.apps.management.app_name}.${module.container_app_environment.default_domain}"
  runtime_origin    = "https://${local.runtime_host}"
  management_origin = "https://${local.management_host}"

  network = data.terraform_remote_state.bootstrap.outputs.network
  zones   = data.terraform_remote_state.bootstrap.outputs.private_dns_zone_ids

  model_account_name = var.app_environment.model.account_name
  text_endpoint      = "https://${local.model_account_name}.openai.azure.com"
  live_endpoint      = "https://${local.model_account_name}.services.ai.azure.com"
}

# The network comes only from the bootstrap outputs, so bootstrap can be replaced later (FR-045).
data "terraform_remote_state" "bootstrap" {
  backend = "azurerm"

  config = {
    resource_group_name  = var.state.resource_group_name
    storage_account_name = var.state.storage_account_name
    container_name       = "tfstate"
    key                  = "${var.environment.name}/bootstrap.tfstate"
    use_azuread_auth     = true
  }
}

resource "azurerm_resource_group" "apps" {
  name     = var.app_environment.resource_group_name
  location = var.environment.location
  tags     = local.tags
}

# The runtime identity is created by the identity script. It is read by name and checked here.
data "azurerm_resources" "runtime_identity" {
  name                = var.app_environment.identity.runtime_identity_name
  resource_group_name = var.app_environment.identity.resource_group_name
  type                = "Microsoft.ManagedIdentity/userAssignedIdentities"
}

resource "terraform_data" "identity_check" {
  lifecycle {
    precondition {
      condition     = length(data.azurerm_resources.runtime_identity.resources) == 1
      error_message = "app_environment.identity.runtime_identity_name does not exist. Run the identity script first."
    }
  }
}

data "azurerm_user_assigned_identity" "runtime" {
  name                = var.app_environment.identity.runtime_identity_name
  resource_group_name = var.app_environment.identity.resource_group_name

  depends_on = [terraform_data.identity_check]
}
