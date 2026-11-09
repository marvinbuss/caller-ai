mock_provider "azurerm" {}

variables {
  name                 = "pe-test"
  resource_group_name  = "rg-apps"
  location             = "swedencentral"
  subnet_id            = "/subscriptions/00000000-0000-4000-8000-000000000002/resourceGroups/rg-network/providers/Microsoft.Network/virtualNetworks/vnet-test/subnets/snet-private-endpoints"
  target_resource_id   = "/subscriptions/00000000-0000-4000-8000-000000000002/resourceGroups/rg-apps/providers/Microsoft.KeyVault/vaults/kv-test"
  subresource_names    = ["vault"]
  private_dns_zone_ids = ["/subscriptions/00000000-0000-4000-8000-000000000002/resourceGroups/rg-network/providers/Microsoft.Network/privateDnsZones/privatelink.vaultcore.azure.net"]
}

run "connects_automatically_with_a_zone_group" {
  command = plan

  assert {
    condition     = azurerm_private_endpoint.this.private_service_connection[0].is_manual_connection == false
    error_message = "The endpoint must not use a manual (cross-subscription) connection."
  }

  assert {
    condition     = azurerm_private_endpoint.this.private_service_connection[0].subresource_names == tolist(["vault"]) && length(azurerm_private_endpoint.this.private_dns_zone_group) == 1
    error_message = "The endpoint needs the sub-resource and a DNS zone group."
  }
}
