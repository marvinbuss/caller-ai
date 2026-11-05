mock_provider "azurerm" {}

variables {
  name                         = "ca-test-runtime"
  resource_group_name          = "rg-apps"
  container_app_environment_id = "/subscriptions/00000000-0000-4000-8000-000000000002/resourceGroups/rg-apps/providers/Microsoft.App/managedEnvironments/cae-test"
  user_assigned_identity_id    = "/subscriptions/00000000-0000-4000-8000-000000000002/resourceGroups/rg-apps/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-test"
  image_repository             = "ghcr.io/marvinbuss/caller-ai/runtime"
  image_tag                    = "sha-0000000000000000000000000000000000000000"
  target_port                  = 8000
  settings                     = { CALLER_MODE = "live" }
}

run "private_single_replica_with_probes" {
  command = plan

  assert {
    condition     = azurerm_container_app.this.template[0].min_replicas == 1 && azurerm_container_app.this.template[0].max_replicas == 1
    error_message = "Exactly one replica is required."
  }

  assert {
    condition     = azurerm_container_app.this.ingress[0].external_enabled == false && length(azurerm_container_app.this.ingress[0].ip_security_restriction) == 0
    error_message = "Ingress must be internal with no rule when public access is off."
  }

  assert {
    condition     = azurerm_container_app.this.template[0].container[0].image == "ghcr.io/marvinbuss/caller-ai/runtime:sha-0000000000000000000000000000000000000000" && azurerm_container_app.this.template[0].container[0].cpu == 0.5 && azurerm_container_app.this.template[0].container[0].memory == "1Gi"
    error_message = "The image and the default sizing must match."
  }

  assert {
    condition     = azurerm_container_app.this.template[0].container[0].readiness_probe[0].path == "/readyz" && azurerm_container_app.this.template[0].container[0].startup_probe[0].path == "/readyz" && azurerm_container_app.this.template[0].container[0].liveness_probe[0].path == "/healthz"
    error_message = "Startup and readiness use /readyz and liveness uses /healthz."
  }
}

run "digest_is_accepted" {
  command = plan

  variables {
    image_tag    = null
    image_digest = "sha256:0000000000000000000000000000000000000000000000000000000000000000"
  }

  assert {
    condition     = endswith(azurerm_container_app.this.template[0].container[0].image, "@sha256:0000000000000000000000000000000000000000000000000000000000000000")
    error_message = "A digest reference must use @."
  }
}

run "public_access_adds_one_allow_rule_per_range" {
  command = plan

  variables {
    public_access_enabled = true
    allowed_cidrs         = ["203.0.113.0/24", "198.51.100.0/24"]
  }

  assert {
    condition     = azurerm_container_app.this.ingress[0].external_enabled && length(azurerm_container_app.this.ingress[0].ip_security_restriction) == 2
    error_message = "Each range needs one rule."
  }

  assert {
    condition     = alltrue([for r in azurerm_container_app.this.ingress[0].ip_security_restriction : r.action == "Allow"])
    error_message = "Only Allow rules are supported."
  }
}

run "open_range_is_a_single_allow_rule" {
  command = plan

  variables {
    public_access_enabled = true
    allowed_cidrs         = ["0.0.0.0/0"]
  }

  assert {
    condition     = length(azurerm_container_app.this.ingress[0].ip_security_restriction) == 1
    error_message = "An explicit open range is one Allow rule."
  }
}

run "secrets_are_key_vault_references_only" {
  command = plan

  variables {
    secret_references = { CALLER_ENTRA_CLIENT_SECRET = "https://kv-test.vault.azure.net/secrets/management-signin-secret" }  # pragma: allowlist secret
  }

  assert {
    condition     = length(azurerm_container_app.this.secret) == 1 && alltrue([for s in azurerm_container_app.this.secret : s.key_vault_secret_id != null])
    error_message = "Secrets must be Key Vault references."
  }
}

run "public_access_without_range_fails" {
  command = plan

  variables {
    public_access_enabled = true
    allowed_cidrs         = []
  }

  expect_failures = [azurerm_container_app.this]
}

run "both_tag_and_digest_fail" {
  command = plan

  variables {
    image_digest = "sha256:0000000000000000000000000000000000000000000000000000000000000000"
  }

  expect_failures = [azurerm_container_app.this]
}

run "latest_tag_is_rejected" {
  command = plan

  variables {
    image_tag = "latest"
  }

  expect_failures = [var.image_tag]
}

run "more_than_one_replica_is_rejected" {
  command = plan

  variables {
    max_replicas = 2
  }

  expect_failures = [var.max_replicas]
}

run "invalid_name_is_rejected" {
  command = plan

  variables {
    name = "A"
  }

  expect_failures = [var.name]
}
