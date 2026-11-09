mock_provider "azurerm" {
  mock_data "azurerm_user_assigned_identity" {
    defaults = {
      id           = "/subscriptions/00000000-0000-4000-8000-000000000002/resourceGroups/rg-caller-int-identity/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-caller-int-runtime"
      client_id    = "00000000-0000-4000-8000-000000000020"
      principal_id = "00000000-0000-4000-8000-000000000021"
    }
  }
}

mock_provider "azapi" {}
mock_provider "tls" {}

override_data {
  target = data.terraform_remote_state.bootstrap
  values = {
    outputs = {
      network = {
        resource_group_name        = "rg-caller-int-network"
        vnet_id                    = "/subscriptions/00000000-0000-4000-8000-000000000002/resourceGroups/rg-caller-int-network/providers/Microsoft.Network/virtualNetworks/vnet-caller-int"
        container_apps_subnet_id   = "/subscriptions/00000000-0000-4000-8000-000000000002/resourceGroups/rg-caller-int-network/providers/Microsoft.Network/virtualNetworks/vnet-caller-int/subnets/snet-container-apps"
        private_endpoint_subnet_id = "/subscriptions/00000000-0000-4000-8000-000000000002/resourceGroups/rg-caller-int-network/providers/Microsoft.Network/virtualNetworks/vnet-caller-int/subnets/snet-private-endpoints"
        network_security_group_id  = "/subscriptions/00000000-0000-4000-8000-000000000002/resourceGroups/rg-caller-int-network/providers/Microsoft.Network/networkSecurityGroups/nsg-vnet-caller-int"
        route_table_id             = "/subscriptions/00000000-0000-4000-8000-000000000002/resourceGroups/rg-caller-int-network/providers/Microsoft.Network/routeTables/rt-vnet-caller-int"
      }
      private_dns_zone_ids = {
        cosmos      = "/subscriptions/00000000-0000-4000-8000-000000000002/resourceGroups/rg-caller-int-network/providers/Microsoft.Network/privateDnsZones/privatelink.documents.azure.com"
        vault       = "/subscriptions/00000000-0000-4000-8000-000000000002/resourceGroups/rg-caller-int-network/providers/Microsoft.Network/privateDnsZones/privatelink.vaultcore.azure.net"
        openai      = "/subscriptions/00000000-0000-4000-8000-000000000002/resourceGroups/rg-caller-int-network/providers/Microsoft.Network/privateDnsZones/privatelink.openai.azure.com"
        cognitive   = "/subscriptions/00000000-0000-4000-8000-000000000002/resourceGroups/rg-caller-int-network/providers/Microsoft.Network/privateDnsZones/privatelink.cognitiveservices.azure.com"
        ai_services = "/subscriptions/00000000-0000-4000-8000-000000000002/resourceGroups/rg-caller-int-network/providers/Microsoft.Network/privateDnsZones/privatelink.services.ai.azure.com"
      }
    }
  }
}

override_data {
  target = data.azurerm_resources.runtime_identity
  values = {
    resources = [{ name = "id-caller-int-runtime", id = "x", type = "Microsoft.ManagedIdentity/userAssignedIdentities", location = "swedencentral", tags = {} }]
  }
}

variables {
  management_signin_secret = "super-secret-signin-value" # pragma: allowlist secret

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
  }

  app_environment = {
    resource_group_name = "rg-caller-int-apps"
    container_apps = {
      environment_name = "cae-caller-int"
      runtime = {
        app_name = "ca-caller-int-runtime"
        image    = { repository = "ghcr.io/marvinbuss/caller-ai/runtime", tag = "sha-0000000000000000000000000000000000000000" }
      }
      management = {
        app_name = "ca-caller-int-management"
        image    = { repository = "ghcr.io/marvinbuss/caller-ai/management", tag = "sha-0000000000000000000000000000000000000000" }
      }
    }
    identity = {
      resource_group_name      = "rg-caller-int-identity"
      management_app_client_id = "00000000-0000-4000-8000-000000000005"
      runtime_app_client_id    = "00000000-0000-4000-8000-000000000008"
      runtime_identity_name    = "id-caller-int-runtime"
      roles                    = { editor = "Configuration.Manager", tester = "Configuration.Tester", runtime = "Runtime.Caller", management = "Management.Caller" }
      user_group_id            = "00000000-0000-4000-8000-000000000007"
    }
    model = {
      account_name = "aif-caller-int"
      text         = { deployment = "gpt-6.1-sol", model = "gpt-6.1-sol", version = "2026-09-29", sku = "GlobalStandard", capacity = 50 }
      live         = { deployment = "gpt-live-1", model = "gpt-live-1", version = "2026-09-10", sku = "GlobalStandard", capacity = 10 }
    }
    data_store = {
      name                    = "cosmos-caller-int"
      configuration_audit_ttl = 86400
      call_session_ttl        = 86400
      call_audit_ttl          = 86400
    }
    secret_store = { name = "kv-caller-int" }
    signing_key  = { key_id = "int-1" }
    application = {
      review_reference        = "int-live-review-1"
      reviewed_fingerprint    = "replace-after-review"
      available_live_sessions = 5
      max_input_tokens        = 8000
      max_output_tokens       = 1024
      voice                   = "replace-voice"
      live_region             = "swedencentral"
    }
    monitoring = {
      workspace_name = "log-caller-int"
      retention_days = 30
      daily_cap_gb   = 1
      action_group = {
        name            = "ag-caller-int"
        short_name      = "callerint"
        email_receivers = { operations = "operations@example.invalid" }
      }
      alerts = {
        owner          = "platform-team"
        runbook_url    = "https://example.invalid/docs/runbooks/monitoring.md"
        http_5xx_count = 5
        restart_count  = 3
        cpu_percent    = 80
        memory_percent = 80
      }
    }
  }
}

run "every_service_is_wired_with_the_configured_images" {
  command = plan

  assert {
    condition     = output.environment_manifest.container_apps.runtime.image == "sha-0000000000000000000000000000000000000000" && output.environment_manifest.container_apps.management.name == "ca-caller-int-management"
    error_message = "The manifest must describe the configured apps and images."
  }

  assert {
    condition     = output.environment_manifest.container_apps.runtime.min_replicas == 1 && output.environment_manifest.container_apps.runtime.max_replicas == 1
    error_message = "Exactly one replica per app."
  }

  assert {
    condition     = toset(output.environment_manifest.vault_secret_names) == toset(["management-signin-secret", "permit-private-key"])
    error_message = "Exactly the two approved secrets are stored."
  }
}

run "settings_match_the_application_contract" {
  command = plan

  assert {
    condition     = module.runtime_app.settings["CALLER_MODE"] == "live" && module.management_app.settings["CALLER_MODE"] == "live"
    error_message = "Both apps start in live mode."
  }

  assert {
    condition     = module.runtime_app.settings["CALLER_TEXT_DEPLOYMENT"] == "gpt-6.1-sol" && module.runtime_app.settings["CALLER_LIVE_REGION"] == "swedencentral" && module.runtime_app.settings["CALLER_AVAILABLE_LIVE_SESSIONS"] == "5"
    error_message = "Runtime model settings must follow the variable file."
  }

  assert {
    condition     = module.runtime_app.settings["CALLER_TEXT_ENDPOINT"] == "https://aif-caller-int.openai.azure.com" && module.runtime_app.settings["CALLER_LIVE_ENDPOINT"] == "https://aif-caller-int.services.ai.azure.com"
    error_message = "The model endpoints follow the created Foundry account's name."
  }

  assert {
    condition     = module.model_access.name == "aif-caller-int" && module.model_access.live_deployment == "gpt-live-1"
    error_message = "The environment creates the Foundry account and the realtime deployment (FR-039)."
  }

  assert {
    condition     = module.management_app.settings["CALLER_MANAGER_ROLE"] == "Configuration.Manager" && module.management_app.settings["CALLER_TEST_ROLE"] == "Configuration.Tester" && module.management_app.settings["CALLER_RUNTIME_ROLE"] == "Runtime.Caller"
    error_message = "Role names must follow the variable file."
  }

  assert {
    condition     = module.management_app.settings["CALLER_MANAGER_SCOPE"] == "api://00000000-0000-4000-8000-000000000005/.default" && module.management_app.settings["CALLER_API_AUDIENCE"] == "00000000-0000-4000-8000-000000000005"
    error_message = "Audience and scope derive from the management registration."
  }

  assert {
    condition     = module.runtime_app.settings["CALLER_WORKLOAD_AUDIENCE"] == "00000000-0000-4000-8000-000000000008" && module.runtime_app.settings["CALLER_WORKLOAD_ROLE"] == "Management.Caller" && module.runtime_app.settings["CALLER_TENANT_ID"] == "00000000-0000-4000-8000-000000000001"
    error_message = "The runtime verifies the management workload token against its own registration and the management role."
  }

  assert {
    condition     = module.management_app.settings["CALLER_RUNTIME_AUDIENCE"] == "00000000-0000-4000-8000-000000000008"
    error_message = "The management app requests tokens for the runtime registration."
  }
}

run "secrets_are_key_vault_references_only" {
  command = plan

  assert {
    condition     = toset(module.management_app.secret_setting_names) == toset(["CALLER_ENTRA_CLIENT_SECRET", "CALLER_SIGNING_KEY_PEM"]) && length(module.runtime_app.secret_setting_names) == 0
    error_message = "Only the management app holds secret references, and only the two approved ones."
  }

  assert {
    condition     = !contains(keys(module.management_app.settings), "CALLER_ENTRA_CLIENT_SECRET") && !contains(keys(module.management_app.settings), "CALLER_SIGNING_KEY_PEM")
    error_message = "Secrets must never be plain settings."
  }
}

run "no_secret_value_appears_in_outputs" {
  command = plan

  assert {
    condition     = !strcontains(jsonencode([output.environment_manifest.identity.roles, output.environment_manifest.public_access, output.environment_manifest.vault_secret_names]), "super-secret-signin-value")
    error_message = "No output may carry a secret value."
  }
}

run "missing_runtime_identity_fails_naming_the_setting" {
  command = plan

  override_data {
    target = data.azurerm_resources.runtime_identity
    values = {
      resources = []
    }
  }

  expect_failures = [terraform_data.identity_check]
}

run "public_access_off_by_default_keeps_everything_private" {
  command = plan

  variables {
    environment = {
      name            = "int"
      tenant_id       = "00000000-0000-4000-8000-000000000001"
      subscription_id = "00000000-0000-4000-8000-000000000002"
      client_id       = "00000000-0000-4000-8000-000000000003"
      location        = "swedencentral"
      tags            = { owner = "platform-team" }
    }
  }

  assert {
    condition     = module.container_app_environment.public_network_access == "Disabled" && module.runtime_app.public_access_enabled == false && module.management_app.public_access_enabled == false
    error_message = "An absent flag means disabled for the environment and both apps (FR-016, FR-017)."
  }

  assert {
    condition     = length(module.runtime_app.allowed_cidrs) == 0 && length(module.management_app.allowed_cidrs) == 0
    error_message = "No allow-list rule exists when public access is off."
  }
}

run "public_access_on_exposes_both_entry_points_with_the_allow_list" {
  command = plan

  variables {
    environment = {
      name            = "int"
      tenant_id       = "00000000-0000-4000-8000-000000000001"
      subscription_id = "00000000-0000-4000-8000-000000000002"
      client_id       = "00000000-0000-4000-8000-000000000003"
      location        = "swedencentral"
      tags            = { owner = "platform-team" }
      public_access   = { enabled = true, allowed_cidrs = ["203.0.113.0/24", "198.51.100.0/24"] }
    }
  }

  assert {
    condition     = module.container_app_environment.public_network_access == "Enabled" && module.runtime_app.public_access_enabled && module.management_app.public_access_enabled
    error_message = "The environment and both designated entry points follow the flag (FR-018)."
  }

  assert {
    condition     = module.runtime_app.allowed_cidrs == tolist(["203.0.113.0/24", "198.51.100.0/24"]) && module.management_app.allowed_cidrs == tolist(["203.0.113.0/24", "198.51.100.0/24"])
    error_message = "Both apps use the same allow-list."
  }
}

run "enabling_without_a_range_is_rejected" {
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

run "invalid_live_region_is_rejected_naming_the_setting" {
  command = plan

  variables {
    app_environment = {
      resource_group_name = "rg-caller-int-apps"
      container_apps = {
        environment_name = "cae-caller-int"
        runtime = {
          app_name = "ca-caller-int-runtime"
          image    = { repository = "ghcr.io/marvinbuss/caller-ai/runtime", tag = "sha-0000000000000000000000000000000000000000" }
        }
        management = {
          app_name = "ca-caller-int-management"
          image    = { repository = "ghcr.io/marvinbuss/caller-ai/management", digest = "sha256:0000000000000000000000000000000000000000000000000000000000000000", tag = "sha-0000000000000000000000000000000000000000" }
        }
      }
      identity = {
        resource_group_name      = "rg-caller-int-identity"
        management_app_client_id = "00000000-0000-4000-8000-000000000005"
        runtime_app_client_id    = "00000000-0000-4000-8000-000000000008"
        runtime_identity_name    = "id-caller-int-runtime"
        roles                    = { editor = "Configuration.Manager", tester = "Configuration.Tester", runtime = "Runtime.Caller", management = "Management.Caller" }
        user_group_id            = "00000000-0000-4000-8000-000000000007"
      }
      model = {
        account_name = "aif-caller-int"
        text         = { deployment = "gpt-6.1-sol", model = "gpt-6.1-sol", version = "2026-09-29", sku = "GlobalStandard", capacity = 50 }
        live         = { deployment = "gpt-live-1", model = "gpt-live-1", version = "2026-09-10", sku = "GlobalStandard", capacity = 10 }
      }
      data_store = {
        name                    = "cosmos-caller-int"
        configuration_audit_ttl = 86400
        call_session_ttl        = 86400
        call_audit_ttl          = 86400
      }
      secret_store = { name = "kv-caller-int" }
      signing_key  = { key_id = "int-1" }
      application = {
        review_reference        = "int-live-review-1"
        reviewed_fingerprint    = "replace-after-review"
        available_live_sessions = 5
        max_input_tokens        = 8000
        max_output_tokens       = 1024
        voice                   = "replace-voice"
        live_region             = "westus"
      }
      monitoring = {
        workspace_name = "log-caller-int"
        retention_days = 30
        daily_cap_gb   = 1
        action_group = {
          name            = "ag-caller-int"
          short_name      = "callerint"
          email_receivers = { operations = "operations@example.invalid" }
        }
        alerts = {
          owner          = "platform-team"
          runbook_url    = "https://example.invalid/docs/runbooks/monitoring.md"
          http_5xx_count = 5
          restart_count  = 3
          cpu_percent    = 80
          memory_percent = 80
        }
      }
    }
  }

  expect_failures = [var.app_environment]
}

run "too_many_previous_signing_keys_are_rejected" {
  command = plan

  variables {
    app_environment = merge(var.app_environment, {
      signing_key = {
        key_id               = "int-5"
        previous_public_keys = { a = "x", b = "x", c = "x", d = "x" }
      }
    })
  }

  expect_failures = [var.app_environment]
}

run "the_current_key_cannot_also_be_a_previous_key" {
  command = plan

  variables {
    app_environment = merge(var.app_environment, {
      signing_key = {
        key_id               = "int-1"
        previous_public_keys = { int-1 = "x" }
      }
    })
  }

  expect_failures = [var.app_environment]
}
