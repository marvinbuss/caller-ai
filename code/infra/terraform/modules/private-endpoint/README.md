# private-endpoint

Creates one private endpoint for a target service with a private DNS zone group.

## Purpose

Services stay private. Each service module's root call adds the endpoint next to the service, in the
subnet and zones that bootstrap provides (FR-005, FR-045).

## Inputs

See [variables.tf](variables.tf): name, location, subnet, target resource and sub-resource, and the DNS
zone IDs.

## Outputs

See [outputs.tf](outputs.tf): endpoint ID and private IP address.

## Behavior and failure

The connection is automatic (`is_manual_connection = false`). A target in another subscription needs the
owner's approval, which the environment check reports as pending.

## Security assumptions

No public network path is opened. The endpoint carries the target's private-link sub-resource only.

## References

- [Private endpoints](https://learn.microsoft.com/azure/private-link/private-endpoint-overview)
