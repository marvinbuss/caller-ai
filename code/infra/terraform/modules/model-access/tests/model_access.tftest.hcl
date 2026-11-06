mock_provider "azurerm" {}
mock_provider "azapi" {}

variables {
  account_id           = "/subscriptions/00000000-0000-4000-8000-000000000006/resourceGroups/rg-model/providers/Microsoft.CognitiveServices/accounts/model-test"
  text_deployment      = "text-dep"
  live_deployment      = "live-dep"
  runtime_principal_id = "00000000-0000-4000-8000-000000000010"
}

override_data {
  target = data.azapi_resource_list.deployments
  values = {
    output = {
      names = ["text-dep", "live-dep", "other"]
    }
  }
}

run "grants_the_runtime_least_privilege_model_access" {
  command = plan

  assert {
    condition     = azurerm_role_assignment.runtime.role_definition_name == "Cognitive Services OpenAI User" && azurerm_role_assignment.runtime.principal_id == "00000000-0000-4000-8000-000000000010"
    error_message = "The runtime needs the OpenAI user role on the account only."
  }
}

run "missing_text_deployment_fails_naming_the_setting" {
  command = plan

  variables {
    text_deployment = "absent-dep"
  }

  expect_failures = [terraform_data.deployment_check]
}

run "missing_live_deployment_fails_naming_the_setting" {
  command = plan

  variables {
    live_deployment = "absent-live"
  }

  expect_failures = [terraform_data.deployment_check]
}

run "identical_deployments_are_rejected" {
  command = plan

  variables {
    live_deployment = "text-dep"
  }

  expect_failures = [var.live_deployment]
}

run "rejects_a_non_account_resource_id" {
  command = plan

  variables {
    account_id = "/subscriptions/x/resourceGroups/y/providers/Microsoft.Storage/storageAccounts/z"
  }

  expect_failures = [var.account_id]
}
