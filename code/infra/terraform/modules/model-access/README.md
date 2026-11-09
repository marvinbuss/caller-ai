# model-access

Creates the Microsoft Foundry account with its text and realtime model deployments, and grants the
runtime's identity the least role that allows model calls.

## Purpose

The environment is complete from its files (FR-039): the module creates an `AIServices` account with
project management, a text deployment and the `gpt-live-1` realtime deployment that the runtime's Live
session requests.

## Inputs

See [variables.tf](variables.tf). The account name is also the custom subdomain, so the text endpoint is
`https://<name>.openai.azure.com` and the live endpoint is `https://<name>.services.ai.azure.com`.

## Outputs

See [outputs.tf](outputs.tf): account ID, name, endpoint, both deployment names and the role assignment ID.

## Behavior and failure

It fails, naming the setting, for an invalid account name, equal deployment names, a realtime deployment
or model other than `gpt-live-1`, an unknown deployment type or a capacity below 1. The deployments are
created one after the other because the account accepts one deployment change at a time. Model versions
never upgrade automatically. Missing quota in the subscription fails the apply with the provider's reason.
The private endpoint is created by the root; its approval and the role are reported by the environment
check.

## Security assumptions

Key-based access and public network access are off, so the account is reached only with an Entra token
through the private endpoint. The runtime holds `Cognitive Services OpenAI User` on the account and has no
management rights.

## References

- [Use Terraform to create Microsoft Foundry](https://learn.microsoft.com/azure/foundry/how-to/create-resource-terraform)
- [Azure OpenAI role-based access](https://learn.microsoft.com/azure/ai-services/openai/how-to/role-based-access-control)
- [Deployment types](https://learn.microsoft.com/azure/foundry/foundry-models/concepts/deployment-types)
