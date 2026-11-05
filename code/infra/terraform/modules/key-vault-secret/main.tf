# The value is written through the management API (ARM), not the vault data plane: a GitHub-hosted
# runner has no network path to a private vault. sensitive_body keeps the value out of plan output.
resource "azapi_resource" "secret" {
  type      = "Microsoft.KeyVault/vaults/secrets@2023-07-01"
  name      = var.name
  parent_id = var.vault_id

  body = {
    properties = {
      attributes = {
        enabled = true
      }
    }
  }

  sensitive_body = {
    properties = {
      value = var.value
    }
  }
}
