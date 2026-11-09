# container-app-environment

Creates the Azure Container Apps environment (workload profiles, Consumption profile) in the delegated
subnet from bootstrap, logging to the Log Analytics workspace.

## Purpose

Hosts the runtime and management container apps. The environment is external so that the public access
flag switches only `public_network_access` in place (FR-016, FR-020).

## Inputs

See [variables.tf](variables.tf). `public_access_enabled` defaults to false.

## Outputs

See [outputs.tf](outputs.tf): environment ID and default domain.

## Behavior and failure

No outbound rule, route or firewall is configured (FR-044). Per-app ingress and allow-list rules are set
by the `container-app` module.

## Security assumptions

Data stores, vault and monitoring stay private regardless of the flag. The flag changes exposure only.

## References

- [Container Apps environments](https://learn.microsoft.com/azure/container-apps/environment)
- [Workload profiles](https://learn.microsoft.com/azure/container-apps/workload-profiles-overview)
