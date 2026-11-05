# network

Creates the virtual network, the Container Apps subnet (delegated to `Microsoft.App/environments`),
the private-endpoint subnet, one network security group and one route table.

## Purpose

The bootstrap deployment is the only owner of network resources (FR-005). This module is composed
only by the bootstrap root.

## Inputs

See [variables.tf](variables.tf). `container_apps_subnet_cidr` must be at least a /27.

## Outputs

See [outputs.tf](outputs.tf): resource group, virtual network, both subnets, the group and the route
table.

## Behavior and failure

- The group has only the platform default rules and the route table has no routes. No NAT gateway,
  firewall or custom rule is created (FR-044). A validation failure names the setting.
- Both are associated to the Container Apps subnet.

## Security assumptions

Outbound policy belongs to the network owner. A customer network can replace this module without
changing the app environment deployment (FR-045).

## References

- [Container Apps networking](https://learn.microsoft.com/azure/container-apps/networking)
- [Subnet delegation](https://learn.microsoft.com/azure/virtual-network/subnet-delegation-overview)
