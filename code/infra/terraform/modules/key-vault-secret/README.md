# key-vault-secret

Writes one secret into a Key Vault through the Azure management API (`azapi`).

## Purpose

A GitHub-hosted runner cannot reach a private vault's data plane. The management-plane write needs no
network path to the vault (research R-07).

## Inputs

See [variables.tf](variables.tf). `value` is sensitive.

## Outputs

See [outputs.tf](outputs.tf): the versionless secret ID, which holds no value.

## Behavior and failure

The value is passed through the provider's sensitive body input, so it does not appear in plan output.
A second plan shows no change (FR-026).

## Security assumptions

The value is held in the protected remote state. This is an owner-approved exception (FR-041, FR-051).
The deployment identity needs a role that allows the management-plane write.

## References

- [Key Vault secrets REST API](https://learn.microsoft.com/azure/templates/microsoft.keyvault/vaults/secrets)
