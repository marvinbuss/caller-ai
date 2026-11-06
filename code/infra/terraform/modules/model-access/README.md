# model-access

Reads the existing AI model account and its deployments, and grants the runtime's identity the least
role that allows model calls.

## Purpose

The owner supplies an existing account with a text deployment and a realtime deployment (FR-039). The
module never creates the account or a deployment.

## Inputs

See [variables.tf](variables.tf). The text and realtime deployments must differ.

## Outputs

See [outputs.tf](outputs.tf): account ID, endpoint and the role assignment ID.

## Behavior and failure

It fails with the setting name when the account is not a Cognitive Services account ID, or when either
deployment does not exist. The private connection approval and the role are reported by the environment
check, because neither is known before apply. The private endpoint is created by the root.

## Security assumptions

The role is `Cognitive Services OpenAI User` on the account. The runtime has no management rights.

## References

- [Azure OpenAI role-based access](https://learn.microsoft.com/azure/ai-services/openai/how-to/role-based-access-control)
