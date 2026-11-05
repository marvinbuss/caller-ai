# workload-identity

Creates a user-assigned managed identity and its Azure role assignments.

## Purpose

The management application's identity reads the vault and the data store. The runtime's identity is
created by the identity script (FR-040, FR-052) and attached by the root, so this module serves the
management side.

## Inputs

See [variables.tf](variables.tf): name, location and the role assignments by name.

## Outputs

See [outputs.tf](outputs.tf): identity ID, client ID and principal ID.

## Behavior and failure

The module creates no directory object (FR-042). It assigns Azure roles only.

## Security assumptions

Roles are least privilege and scoped by the caller.

## References

- [Managed identities](https://learn.microsoft.com/entra/identity/managed-identities-azure-resources/overview)
