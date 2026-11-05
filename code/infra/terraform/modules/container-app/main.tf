locals {
  image = var.image_digest != null ? "${var.image_repository}@${var.image_digest}" : "${var.image_repository}:${var.image_tag}"

  secret_names = { for env_name in keys(var.secret_references) : env_name => lower(replace(env_name, "_", "-")) }
}

resource "azurerm_container_app" "this" {
  name                         = var.name
  resource_group_name          = var.resource_group_name
  container_app_environment_id = var.container_app_environment_id
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"
  tags                         = var.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [var.user_assigned_identity_id]
  }

  dynamic "secret" {
    for_each = var.secret_references

    content {
      name                = local.secret_names[secret.key]
      identity            = var.user_assigned_identity_id
      key_vault_secret_id = secret.value
    }
  }

  ingress {
    external_enabled = var.public_access_enabled
    target_port      = var.target_port
    transport        = "auto"

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }

    dynamic "ip_security_restriction" {
      for_each = var.public_access_enabled ? { for index, cidr in var.allowed_cidrs : tostring(index) => cidr } : {}

      content {
        name             = "allow-${ip_security_restriction.key}"
        action           = "Allow"
        ip_address_range = ip_security_restriction.value
      }
    }
  }

  template {
    min_replicas = var.min_replicas
    max_replicas = var.max_replicas

    container {
      name   = var.name
      image  = local.image
      cpu    = var.cpu
      memory = var.memory

      dynamic "env" {
        for_each = var.settings

        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = local.secret_names

        content {
          name        = env.key
          secret_name = env.value
        }
      }

      startup_probe {
        transport = "HTTP"
        port      = var.target_port
        path      = "/readyz"
      }

      readiness_probe {
        transport = "HTTP"
        port      = var.target_port
        path      = "/readyz"
      }

      liveness_probe {
        transport = "HTTP"
        port      = var.target_port
        path      = "/healthz"
      }
    }
  }

  lifecycle {
    precondition {
      condition     = (var.image_tag == null) != (var.image_digest == null)
      error_message = "Exactly one of the image tag and the image digest must be set."
    }

    precondition {
      condition     = !var.public_access_enabled || length(var.allowed_cidrs) > 0
      error_message = "Public access needs at least one allowed source range."
    }
  }
}
