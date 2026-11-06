# secret-store

Creates a Key Vault with RBAC authorization, purge protection and no public access, plus read-only
secret roles for the named principals.

## Purpose

Holds the two approved secrets: the management sign-in client secret and the permit private key (FR-041,
FR-051). Applications read them through Key Vault references and their managed identities.

## Inputs

See [variables.tf](variables.tf). `soft_delete_days` is 7 to 90.

## Outputs

See [outputs.tf](outputs.tf): vault ID, name and URI.

## Behavior and failure

The module creates no secret. The `key-vault-secret` module writes secrets over the management API. A
private endpoint is added by the root.

## Security assumptions

Public access is disabled and the network default is deny. Readers receive Key Vault Secrets User only.

## References

- [Key Vault RBAC guide](https://learn.microsoft.com/azure/key-vault/general/rbac-guide)
