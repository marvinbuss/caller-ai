# Seeded misconfiguration for the security-scan test: open storage network rules. Never deployed.
resource "azurerm_storage_account" "bad" {
  name                     = "seededbadexample"
  resource_group_name      = "rg-seeded"
  location                 = "westeurope"
  account_tier             = "Standard"
  account_replication_type = "LRS"
}
