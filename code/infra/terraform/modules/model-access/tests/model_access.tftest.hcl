mock_provider "azurerm" {}

variables {
  name                 = "aif-caller-test"
  resource_group_name  = "rg-caller-test-apps"
  location             = "swedencentral"
  tags                 = { owner = "platform-team" }
  runtime_principal_id = "00000000-0000-4000-8000-000000000010"

  text = {
    deployment = "gpt-6.1-sol"
    model      = "gpt-6.1-sol"
    version    = "2026-09-29"
    sku        = "GlobalStandard"
    capacity   = 50
  }

  live = {
    deployment = "gpt-live-1"
    model      = "gpt-live-1"
    version    = "2026-09-10"
    sku        = "GlobalStandard"
    capacity   = 10
  }
}

run "creates_a_private_foundry_account" {
  command = plan

  assert {
    condition     = azurerm_cognitive_account.this.kind == "AIServices" && azurerm_cognitive_account.this.sku_name == "S0" && azurerm_cognitive_account.this.project_management_enabled
    error_message = "The account must be a Foundry (AIServices) account with project management."
  }

  assert {
    condition     = azurerm_cognitive_account.this.custom_subdomain_name == "aif-caller-test" && azurerm_cognitive_account.this.location == "swedencentral"
    error_message = "The custom subdomain equals the name, so the endpoints follow the name."
  }

  assert {
    condition     = azurerm_cognitive_account.this.local_auth_enabled == false && azurerm_cognitive_account.this.public_network_access_enabled == false
    error_message = "Key access and public network access must be off."
  }

  assert {
    condition     = azurerm_cognitive_account.this.network_acls[0].default_action == "Deny" && azurerm_cognitive_account.this.identity[0].type == "SystemAssigned"
    error_message = "Network rules deny by default and the account has its own identity."
  }
}

run "creates_both_deployments_pinned" {
  command = plan

  assert {
    condition     = azurerm_cognitive_deployment.text.name == "gpt-6.1-sol" && azurerm_cognitive_deployment.text.model[0].name == "gpt-6.1-sol" && azurerm_cognitive_deployment.text.model[0].version == "2026-09-29" && azurerm_cognitive_deployment.text.model[0].format == "OpenAI"
    error_message = "The text deployment follows the variable file."
  }

  assert {
    condition     = azurerm_cognitive_deployment.text.sku[0].name == "GlobalStandard" && azurerm_cognitive_deployment.text.sku[0].capacity == 50
    error_message = "The text deployment type and capacity follow the variable file."
  }

  assert {
    condition     = azurerm_cognitive_deployment.live.name == "gpt-live-1" && azurerm_cognitive_deployment.live.model[0].version == "2026-09-10" && azurerm_cognitive_deployment.live.sku[0].capacity == 10
    error_message = "The realtime deployment follows the variable file."
  }

  assert {
    condition     = azurerm_cognitive_deployment.text.version_upgrade_option == "NoAutoUpgrade" && azurerm_cognitive_deployment.live.version_upgrade_option == "NoAutoUpgrade"
    error_message = "The reviewed model versions must not change underneath the runtime."
  }
}

run "grants_the_runtime_least_privilege_model_access" {
  command = plan

  assert {
    condition     = azurerm_role_assignment.runtime.role_definition_name == "Cognitive Services OpenAI User" && azurerm_role_assignment.runtime.principal_id == "00000000-0000-4000-8000-000000000010"
    error_message = "The runtime needs the OpenAI user role on the account only."
  }

  assert {
    condition     = output.name == "aif-caller-test" && output.text_deployment == "gpt-6.1-sol" && output.live_deployment == "gpt-live-1"
    error_message = "The outputs name the account and both deployments."
  }
}

run "rejects_an_invalid_account_name" {
  command = plan

  variables {
    name = "Not_A_Valid_Name"
  }

  expect_failures = [var.name]
}

run "identical_deployments_are_rejected" {
  command = plan

  variables {
    text = {
      deployment = "gpt-live-1"
      model      = "gpt-6.1-sol"
      version    = "2026-09-29"
      sku        = "GlobalStandard"
      capacity   = 50
    }
  }

  expect_failures = [var.text]
}

run "realtime_deployment_must_be_the_name_the_runtime_requests" {
  command = plan

  variables {
    live = {
      deployment = "realtime"
      model      = "gpt-live-1"
      version    = "2026-09-10"
      sku        = "GlobalStandard"
      capacity   = 10
    }
  }

  expect_failures = [var.live]
}

run "unknown_deployment_type_is_rejected" {
  command = plan

  variables {
    text = {
      deployment = "gpt-6.1-sol"
      model      = "gpt-6.1-sol"
      version    = "2026-09-29"
      sku        = "Premium"
      capacity   = 50
    }
  }

  expect_failures = [var.text]
}

run "capacity_below_one_is_rejected" {
  command = plan

  variables {
    live = {
      deployment = "gpt-live-1"
      model      = "gpt-live-1"
      version    = "2026-09-10"
      sku        = "GlobalStandard"
      capacity   = 0
    }
  }

  expect_failures = [var.live]
}
