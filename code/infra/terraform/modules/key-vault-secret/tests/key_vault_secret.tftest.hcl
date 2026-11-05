mock_provider "azapi" {}

variables {
  name      = "management-signin-secret"
  vault_id  = "/subscriptions/00000000-0000-4000-8000-000000000002/resourceGroups/rg-apps/providers/Microsoft.KeyVault/vaults/kv-test"
  vault_uri = "https://kv-test.vault.azure.net/"
  value     = "super-secret-test-value"
}

run "writes_over_the_management_api_with_a_sensitive_body" {
  command = plan

  assert {
    condition     = azapi_resource.secret.type == "Microsoft.KeyVault/vaults/secrets@2023-07-01"
    error_message = "The secret must be written as an ARM resource, not through the data plane."
  }

  assert {
    condition     = !strcontains(jsonencode(azapi_resource.secret.body), "super-secret-test-value")
    error_message = "The plain body must not carry the secret value."
  }
}

run "output_is_a_versionless_reference_without_the_value" {
  command = plan

  assert {
    condition     = output.secret_id == "https://kv-test.vault.azure.net/secrets/management-signin-secret"
    error_message = "The output must be the versionless secret ID."
  }

  assert {
    condition     = !strcontains(output.secret_id, "super-secret-test-value")
    error_message = "The output must not contain the value."
  }
}

run "rejects_invalid_secret_name" {
  command = plan

  variables {
    name = "bad_name"
  }

  expect_failures = [var.name]
}
