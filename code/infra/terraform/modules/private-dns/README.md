# private-dns

Creates one private DNS zone for each private-link purpose the environment uses, and links each zone to
the virtual network.

## Purpose

Private endpoints resolve through these zones. Bootstrap owns the zones so a customer network can supply
its own (FR-005, FR-045).

## Inputs

See [variables.tf](variables.tf): the resource group and the virtual network ID.

## Outputs

See [outputs.tf](outputs.tf): a map from purpose to zone ID.

## Behavior and failure

The module creates no DNS record. Records are created by the zone groups of the private endpoints.

## Security assumptions

Zones are private to the linked network. Auto-registration is off.

## References

- [Private endpoint DNS zone values](https://learn.microsoft.com/azure/private-link/private-endpoint-dns)
