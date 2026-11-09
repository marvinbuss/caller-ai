# state-backend

Creates the remote Terraform state storage for one environment: a resource group, a storage account
and the `tfstate` and `tfplans` containers.

## Purpose

State and saved plans can hold two approved secrets (the sign-in client secret and the permit private
key), so the account is identity-only and protected.

## Inputs

See [variables.tf](variables.tf). `storage_account_name` must be 3 to 24 lowercase letters or digits
and globally unique. `state_retention_days` is 7 to 365. `writer_principal_ids` are the deployment
identities that receive blob data access on the two containers only.

## Outputs

See [outputs.tf](outputs.tf): resource group, account name and ID, and the two container names. The
module exposes no key, SAS token or connection string.

## Behavior and failure

- Shared keys are off, TLS 1.2 and HTTPS only are enforced, versioning and soft delete are on, and a
  delete lock protects the account. A validation failure names the setting.
- The public endpoint stays on so GitHub-hosted runners can reach the state with an OIDC identity.

## Security assumptions

Access is by Entra identity only. Writer roles are scoped to the containers, not the account.

## References

- [Azure Storage security recommendations](https://learn.microsoft.com/azure/storage/blobs/security-recommendations)
- [Terraform azurerm backend](https://developer.hashicorp.com/terraform/language/backend/azurerm)
