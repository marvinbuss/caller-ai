locals {
  # Purpose to private DNS zone name for the services that the environment exposes privately.
  zones = {
    cosmos      = "privatelink.documents.azure.com"
    vault       = "privatelink.vaultcore.azure.net"
    openai      = "privatelink.openai.azure.com"
    cognitive   = "privatelink.cognitiveservices.azure.com"
    ai_services = "privatelink.services.ai.azure.com"
    monitor     = "privatelink.monitor.azure.com"
    oms         = "privatelink.oms.opinsights.azure.com"
    ods         = "privatelink.ods.opinsights.azure.com"
    agentsvc    = "privatelink.agentsvc.azure-automation.net"
    blob        = "privatelink.blob.core.windows.net"
  }
}

resource "azurerm_private_dns_zone" "this" {
  for_each = local.zones

  name                = each.value
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "this" {
  for_each = local.zones

  name                 = "link-${each.key}"
  private_dns_zone_id  = azurerm_private_dns_zone.this[each.key].id
  virtual_network_id   = var.vnet_id
  registration_enabled = false
  tags                 = var.tags
}
