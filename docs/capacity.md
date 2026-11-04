# Capacity and Cost Qualification

## Current State

Actual model cost per turn and per Live voice second, Cosmos RU demand, deployed instance and
session ceilings and safe admission limits are **pending live measurement**. No invented cost,
region, legal retention or quota default is accepted. Local control runs with synthetic latencies
are not production capacity or a billing estimate.

| Required measurement | State | Release obligation |
|----------------------|-------|--------------------|
| Text model cost per turn | Pending | Actual approved Foundry deployment usage and cost |
| Live voice cost per second and context-window usage | Pending | Approved GPT-Live usage records (`voice_seconds` snapshots are control evidence only) |
| Cosmos RU per authority and history read/write, throttling and retention | Pending | Owned-container Strong authority operations and failure behavior under measured load |
| Text history storage growth | Pending | Measured item size and the 30-day expiry effect on the `text-conversations` container |
| Instance and long-lived socket limits | Pending | Measured voice sessions per instance against the authority's five occupied slots |
| Voice and text turn latency and admission | Pending | Approved live samples, not HTTP means or virtual timing |
| Monitoring, firewall and egress storage budgets | Pending | Approved caps, actual ingestion/egress, retention and on-call delivery |

Admission allows one active test per manager and at most five occupied slots in the shared
authority item; an ambiguous write keeps a slot occupied until cleanup is verified. See
[conversation endpoints](conversation-endpoints.md) and [text history](runbooks/text-history.md).

Measure only after separately authorized qualification and live execution. Record approved
environment, image and review references and safe aggregate results without conversation
content, credentials or invented prices. Missing measurements block release but do not prevent
local deterministic development. [Live environment status](acceptance/live-environment.md) and
[release gates](acceptance/release-gates.md) remain the authoritative pending classifications.
