resource "azurerm_cognitive_account" "this" {
  name                = var.name
  resource_group_name = var.resource_group_name
  location            = var.location
  kind                = "AIServices"
  sku_name            = "S0"
  tags                = var.tags

  custom_subdomain_name         = var.name
  project_management_enabled    = true
  local_auth_enabled            = false
  public_network_access_enabled = false

  identity {
    type = "SystemAssigned"
  }

  network_acls {
    default_action = "Deny"
  }
}

resource "azurerm_cognitive_deployment" "text" {
  name                   = var.text.deployment
  cognitive_account_id   = azurerm_cognitive_account.this.id
  version_upgrade_option = "NoAutoUpgrade"

  model {
    format  = "OpenAI"
    name    = var.text.model
    version = var.text.version
  }

  sku {
    name     = var.text.sku
    capacity = var.text.capacity
  }
}

# The account accepts one deployment change at a time.
resource "azurerm_cognitive_deployment" "live" {
  name                   = var.live.deployment
  cognitive_account_id   = azurerm_cognitive_account.this.id
  version_upgrade_option = "NoAutoUpgrade"

  model {
    format  = "OpenAI"
    name    = var.live.model
    version = var.live.version
  }

  sku {
    name     = var.live.sku
    capacity = var.live.capacity
  }

  depends_on = [azurerm_cognitive_deployment.text]
}

resource "azurerm_role_assignment" "runtime" {
  scope                = azurerm_cognitive_account.this.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = var.runtime_principal_id
}
