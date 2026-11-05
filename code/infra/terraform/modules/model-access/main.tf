locals {
  account_name           = element(split("/", var.account_id), 8)
  account_resource_group = element(split("/", var.account_id), 4)
}

# The account and its deployments already exist. The module reads them and never creates either.
data "azurerm_cognitive_account" "this" {
  name                = local.account_name
  resource_group_name = local.account_resource_group
}

data "azapi_resource_list" "deployments" {
  type      = "Microsoft.CognitiveServices/accounts/deployments@2024-10-01"
  parent_id = var.account_id

  response_export_values = {
    names = "value[].name"
  }
}

locals {
  deployment_names = try(data.azapi_resource_list.deployments.output.names, [])
}

resource "terraform_data" "deployment_check" {
  lifecycle {
    precondition {
      condition     = contains(local.deployment_names, var.text_deployment)
      error_message = "app_environment.model.text_deployment does not exist on the named account."
    }

    precondition {
      condition     = contains(local.deployment_names, var.live_deployment)
      error_message = "app_environment.model.live_deployment does not exist on the named account."
    }
  }
}

resource "azurerm_role_assignment" "runtime" {
  scope                = var.account_id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = var.runtime_principal_id

  depends_on = [terraform_data.deployment_check]
}
