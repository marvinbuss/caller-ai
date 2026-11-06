# permit-key

Generates the RSA 2048 key pair that signs and verifies test-conversation start permits.

## Purpose

The management application signs permits with the private key (read from the vault) and the runtime
verifies them with the public key (a setting). See FR-051.

## Inputs

See [variables.tf](variables.tf): `key_id` matching `[A-Za-z0-9][A-Za-z0-9_-]{0,127}`.

## Outputs

See [outputs.tf](outputs.tf): key ID, public key and the sensitive private key.

## Behavior and failure

The module writes no file. The private key is only for the `key-vault-secret` module. Changing `key_id`
replaces the key pair (a `terraform_data` resource triggers the replacement), which is how a rotation starts.

## Security assumptions

The private key lives in the protected remote state and the vault only. This is an owner-approved
exception. Rotation keeps the earlier public keys accepted during the overlap: the root passes them to the
runtime from `signing_key.previous_public_keys`.

## References

- [tls_private_key](https://registry.terraform.io/providers/hashicorp/tls/latest/docs/resources/private_key)
