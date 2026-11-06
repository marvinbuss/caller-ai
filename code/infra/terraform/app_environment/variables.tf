variable "environment" {
  description = "Environment identity, subscription, region, tags and public access setting from the environment variable file."
  type = object({
    name            = string
    tenant_id       = string
    subscription_id = string
    client_id       = string
    location        = string
    tags            = map(string)
    public_access = optional(object({
      enabled       = optional(bool, false)
      allowed_cidrs = optional(list(string), [])
    }), {})
  })

  validation {
    condition     = contains(["int", "dev", "tst", "prd"], var.environment.name)
    error_message = "environment.name must be one of int, dev, tst or prd."
  }

  validation {
    condition = alltrue([
      for id in [var.environment.tenant_id, var.environment.subscription_id, var.environment.client_id] :
      can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", id))
    ])
    error_message = "environment.tenant_id, environment.subscription_id and environment.client_id must be GUIDs."
  }

  validation {
    condition     = can(regex("^[a-z0-9]{3,30}$", var.environment.location))
    error_message = "environment.location must be an Azure region name such as swedencentral."
  }

  validation {
    condition     = contains(keys(var.environment.tags), "owner")
    error_message = "environment.tags must include owner."
  }

  validation {
    condition     = !var.environment.public_access.enabled || length(var.environment.public_access.allowed_cidrs) > 0
    error_message = "environment.public_access.allowed_cidrs is required when public access is enabled."
  }

  validation {
    condition     = alltrue([for c in var.environment.public_access.allowed_cidrs : can(cidrhost(c, 0))])
    error_message = "environment.public_access.allowed_cidrs must contain valid CIDR ranges."
  }
}

variable "state" {
  description = "Coordinates of the remote state storage, used to read the bootstrap outputs. The state object of the environment variable file."
  type = object({
    resource_group_name  = string
    storage_account_name = string
  })
}

variable "app_environment" {
  description = "Services, images, identities and references from the app_environment object of the environment variable file."
  type = object({
    resource_group_name = string

    container_apps = object({
      environment_name = string
      runtime = object({
        app_name = string
        image = object({
          repository = string
          tag        = optional(string)
          digest     = optional(string)
        })
        cpu    = optional(number, 0.5)
        memory = optional(string, "1Gi")
      })
      management = object({
        app_name = string
        image = object({
          repository = string
          tag        = optional(string)
          digest     = optional(string)
        })
        cpu    = optional(number, 0.5)
        memory = optional(string, "1Gi")
      })
    })

    identity = object({
      resource_group_name      = string
      management_app_client_id = string
      runtime_app_client_id    = string
      runtime_identity_name    = string
      roles = object({
        editor     = string
        tester     = string
        runtime    = string
        management = string
      })
      user_group_id = string
    })

    model = object({
      account_id      = string
      text_deployment = string
      live_deployment = string
    })

    data_store = object({
      name                    = string
      backup_tier             = optional(string, "Continuous7Days")
      configuration_audit_ttl = number
      call_session_ttl        = number
      call_audit_ttl          = number
    })

    secret_store = object({
      name             = string
      soft_delete_days = optional(number, 30)
    })

    signing_key = object({
      key_id = string
      # Public keys of earlier pairs the runtime still accepts during a rotation overlap (FR-051).
      previous_public_keys = optional(map(string), {})
    })

    application = object({
      review_reference        = string
      reviewed_fingerprint    = string
      available_live_sessions = number
      max_input_tokens        = number
      max_output_tokens       = number
      voice                   = string
      live_region             = string
      text_history_ttl        = optional(number, 86400)
    })

    monitoring = object({
      workspace_name = string
      retention_days = number
      daily_cap_gb   = number
      action_group = object({
        name            = string
        short_name      = string
        email_receivers = map(string)
      })
      alerts = object({
        owner          = string
        runbook_url    = string
        http_5xx_count = number
        restart_count  = number
        cpu_percent    = number
        memory_percent = number
      })
    })
  })

  validation {
    condition = alltrue([
      for app in [var.app_environment.container_apps.runtime, var.app_environment.container_apps.management] :
      (app.image.tag == null) != (app.image.digest == null)
    ])
    error_message = "app_environment.container_apps image must set exactly one of tag and digest."
  }

  validation {
    condition     = var.app_environment.model.text_deployment != var.app_environment.model.live_deployment
    error_message = "app_environment.model.live_deployment must differ from text_deployment."
  }

  validation {
    condition     = contains(["canadacentral", "centralus", "eastus2", "swedencentral"], var.app_environment.application.live_region)
    error_message = "app_environment.application.live_region must be canadacentral, centralus, eastus2 or swedencentral."
  }

  validation {
    condition     = var.app_environment.application.available_live_sessions >= 5 && var.app_environment.application.available_live_sessions <= 10000
    error_message = "app_environment.application.available_live_sessions must be between 5 and 10000."
  }

  validation {
    condition = (
      length(var.app_environment.signing_key.previous_public_keys) <= 3 &&
      !contains(keys(var.app_environment.signing_key.previous_public_keys), var.app_environment.signing_key.key_id) &&
      alltrue([for k in keys(var.app_environment.signing_key.previous_public_keys) : can(regex("^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$", k))])
    )
    error_message = "app_environment.signing_key.previous_public_keys must hold at most three entries with valid key IDs that differ from key_id."
  }

  validation {
    condition = (
      can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", var.app_environment.identity.runtime_app_client_id)) &&
      var.app_environment.identity.runtime_app_client_id != var.app_environment.identity.management_app_client_id
    )
    error_message = "app_environment.identity.runtime_app_client_id must be a GUID that differs from management_app_client_id."
  }

  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", var.app_environment.identity.user_group_id))
    error_message = "app_environment.identity.user_group_id must be a GUID."
  }
}

variable "management_signin_secret" {
  description = "Client secret of the management sign-in registration. Supplied through TF_VAR_management_signin_secret and stored in the Key Vault."
  type        = string
  sensitive   = true
}
