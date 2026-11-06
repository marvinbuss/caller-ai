mock_provider "azurerm" {}

variables {
  name                = "id-test"
  resource_group_name = "rg-apps"
  location            = "swedencentral"
  role_assignments = {
    vault = { scope = "/subscriptions/00000000-0000-4000-8000-000000000002/resourceGroups/rg-apps/providers/Microsoft.KeyVault/vaults/kv-test", role_definition_name = "Key Vault Secrets User" }
  }
}

run "creates_an_identity_with_the_requested_roles_only" {
  command = plan

  assert {
    condition     = azurerm_user_assigned_identity.this.name == "id-test" && toset(keys(azurerm_role_assignment.this)) == toset(["vault"])
    error_message = "The identity and exactly the requested role assignments must be planned."
  }

  assert {
    condition     = azurerm_role_assignment.this["vault"].role_definition_name == "Key Vault Secrets User"
    error_message = "The role must be the one requested."
  }
}

run "no_role_assignments_by_default" {
  command = plan

  variables {
    role_assignments = {}
  }

  assert {
    condition     = length(azurerm_role_assignment.this) == 0
    error_message = "Without a request the identity holds no role."
  }
}
