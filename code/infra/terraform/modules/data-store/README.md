# data-store

Creates a serverless Cosmos DB for NoSQL account with one database and the five containers the
applications use, with identity-only data roles scoped per container.

## Purpose

Holds the agent configuration, its audit entries, call sessions, call audit entries and text
conversation history.

## Inputs

See [variables.tf](variables.tf). `data_roles` maps a name to a principal and the containers it may use.

## Outputs

See [outputs.tf](outputs.tf): account ID, name, endpoint and database name.

## Behavior and failure

- Containers: `configuration` (`/organizationId`, no default TTL), `configuration-audit` and `call-audit`
  (`/dayBucket`), `call-sessions` (`/id`), `text-conversations` (`/ownerKey`, item TTL).
- A role naming another container fails validation.

## Security assumptions

Local authentication is off and public access is disabled. A private endpoint is created by the root
with the `private-endpoint` module. Each application can reach only its own containers.

## References

- [Cosmos DB data-plane RBAC](https://learn.microsoft.com/azure/cosmos-db/how-to-setup-rbac)
