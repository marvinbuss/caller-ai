mock_provider "azurerm" {}

variables {
  environment = {
    name            = "int"
    tenant_id       = "00000000-0000-4000-8000-000000000001"
    subscription_id = "00000000-0000-4000-8000-000000000002"
    client_id       = "00000000-0000-4000-8000-000000000003"
    location        = "swedencentral"
    tags            = { owner = "platform-team" }
    public_access   = { enabled = true, allowed_cidrs = ["0.0.0.0/0"] }
  }
  state = {
    resource_group_name  = "rg-caller-int-state"
    storage_account_name = "callerintstate0001"
    state_retention_days = 30
    writer_principal_ids = ["00000000-0000-4000-8000-000000000004"]
  }
}

run "composes_the_state_backend" {
  command = plan

  assert {
    condition     = output.state_backend.container_name == "tfstate" && output.state_backend.plan_container_name == "tfplans" && output.state_backend.storage_account_name == "callerintstate0001"
    error_message = "The state root must publish the backend coordinates."
  }
}

run "tags_carry_owner_and_environment" {
  command = plan

  assert {
    condition     = module.state_backend.resource_group_name == "rg-caller-int-state"
    error_message = "The module must use the configured resource group."
  }
}

run "invalid_storage_account_name_names_the_setting" {
  command = plan

  variables {
    state = {
      resource_group_name  = "rg-caller-int-state"
      storage_account_name = "Bad_Name"
      state_retention_days = 30
      writer_principal_ids = []
    }
  }

  expect_failures = [var.state]
}

run "environment_without_owner_tag_is_rejected" {
  command = plan

  variables {
    environment = {
      name            = "int"
      tenant_id       = "00000000-0000-4000-8000-000000000001"
      subscription_id = "00000000-0000-4000-8000-000000000002"
      client_id       = "00000000-0000-4000-8000-000000000003"
      location        = "swedencentral"
      tags            = {}
    }
  }

  expect_failures = [var.environment]
}

run "enabled_public_access_without_ranges_is_rejected" {
  command = plan

  variables {
    environment = {
      name            = "int"
      tenant_id       = "00000000-0000-4000-8000-000000000001"
      subscription_id = "00000000-0000-4000-8000-000000000002"
      client_id       = "00000000-0000-4000-8000-000000000003"
      location        = "swedencentral"
      tags            = { owner = "platform-team" }
      public_access   = { enabled = true }
    }
  }

  expect_failures = [var.environment]
}
