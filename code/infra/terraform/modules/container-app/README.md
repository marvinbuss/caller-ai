# container-app

Creates one Azure Container App in a single-revision, exactly-one-replica configuration with a
user-assigned identity, Key Vault secret references and health probes.

## Purpose

Hosts the runtime or the management application from a public GitHub container registry image.

## Inputs

See [variables.tf](variables.tf). The image is a repository plus exactly one of a tag (`sha-<40 hex>` or
`v<semver>`) or a digest (`sha256:<64 hex>`); `latest` is rejected. `cpu` and `memory` default to `0.5`
and `1Gi`. `min_replicas` and `max_replicas` are fixed to 1.

## Outputs

See [outputs.tf](outputs.tf): app ID, name and default domain name.

## Behavior and failure

- Startup and readiness probe `/readyz`, liveness probe `/healthz`.
- Ingress is internal unless `public_access_enabled`; then one `Allow` rule per allowed range. Enabling
  without a range fails.
- Plain settings go through `settings`. Secrets only as Key Vault references through `secret_references`.

## Security assumptions

The app resolves vault references with its user-assigned identity. No secret value is accepted as a
setting. Exactly one replica because sessions are kept in memory.

## References

- [Container Apps secrets](https://learn.microsoft.com/azure/container-apps/manage-secrets)
- [Ingress IP restrictions](https://learn.microsoft.com/azure/container-apps/ip-restrictions)
