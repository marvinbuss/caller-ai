mock_provider "azurerm" {}

variables {
  resource_group_name = "rg-network"
  vnet_id             = "/subscriptions/00000000-0000-4000-8000-000000000002/resourceGroups/rg-network/providers/Microsoft.Network/virtualNetworks/vnet-test"
}

run "one_linked_zone_per_purpose" {
  command = plan

  assert {
    condition     = toset(keys(azurerm_private_dns_zone.this)) == toset(["cosmos", "vault", "openai", "cognitive", "ai_services", "monitor", "oms", "ods", "agentsvc", "blob"])
    error_message = "Every purpose needs a zone."
  }

  assert {
    condition     = length(azurerm_private_dns_zone_virtual_network_link.this) == 10 && alltrue([for l in azurerm_private_dns_zone_virtual_network_link.this : l.registration_enabled == false])
    error_message = "Each zone needs a link without auto-registration."
  }

  assert {
    condition     = azurerm_private_dns_zone.this["vault"].name == "privatelink.vaultcore.azure.net" && azurerm_private_dns_zone.this["cosmos"].name == "privatelink.documents.azure.com"
    error_message = "Zone names must match the Azure private link zones."
  }
}
