# Text history runbook

The backend stores text conversations so a follow-up turn can continue where the last one ended,
on any backend instance. This is the only conversation content the platform persists. Voice
sessions store nothing; audio, transcripts and provider bodies stay volatile. The endpoint
behavior is in [conversation endpoints](../conversation-endpoints.md) and the threat assessment
is in [security](../security.md#stored-text-conversation-history).

## What is stored

One item per conversation in the Cosmos container `text-conversations`:

| Field | Content |
|-------|---------|
| `id` | Conversation id (random, returned to the caller in the first `conversation` event) |
| `ownerKey` | Partition key, a digest of the signed permit's actor key; only this manager can address the item |
| `turns` | User and assistant text in order; at most 256 KiB in total and 64 KiB per turn |
| `activeAgentId`, `activeCommunityId` | The agent position to resume at, revalidated against the pinned revision |
| `createdAt`, `updatedAt` | UTC timestamps |
| `ttl` | 2,592,000 seconds (30 days), written on every update so expiry counts from the last turn |

Tool calls, transfer payloads, prompts, tokens and provider bodies are never stored. The container
default expiry is `-1` so only items that carry their own `ttl` expire.

## Who can read it

Only the backend runtime identity has data-plane access, and only item read, create and replace
on this container (no delete, no query across partitions). The management service never reads it
and the browser never sees an owner key or stored turns. Requests address an item by owner key and
conversation id, so an unknown, expired or another manager's id all look the same to the caller.
Local key authentication is disabled on the account; access is by managed identity over a private
endpoint.

## Retention and expiry

- Each conversation expires 30 days after its last turn. Expiry is the only deletion path.
- Cosmos removes expired items in the background, and an expired item is not returned even before
  removal. A follow-up after expiry fails with `conversation_not_found` exactly like an unknown id.
- Backups are continuous; the account backup retention applies to stored text like any other
  item. Logical expiry is not a physical-erasure guarantee for backups.

## Emergency purge

Use only for a confirmed exposure or a legal request, with written approval from the security
owner and the service owner.

1. Identify the affected owner key from the permit actor key (never from conversation text) and
   record the request, approvers and time in the change record without copying any content.
2. Grant a named operator a temporary Cosmos data-plane role that allows delete on the single
   container. The runtime identity must never receive delete.
3. Delete the items in that owner's partition, then confirm a follow-up with an affected id fails
   with `conversation_not_found`.
4. Remove the temporary role assignment and record completion. Backups age out on their normal
   schedule.

## Failure and recovery

| Symptom | Behavior | Response |
|---------|----------|----------|
| Store unreachable or throttled | Follow-up requests fail closed with `history_unavailable` and no reply; a new conversation streams a reply and then ends with the same error as its last event, storing nothing | Restore the store or its private endpoint and role assignment; no data repair is needed |
| `/readyz` reports `history_store_unavailable` | The background check (about every 10 seconds) is failing or older than 30 seconds, so the instance leaves rotation | Check the history-store signals below, then the private endpoint and the runtime identity's role |
| Concurrent update on one conversation | `capacity_unavailable` with reason `work_busy` and the stored conversation unchanged | The caller retries the turn |
| Conversation exceeds 256 KiB | `limit_exceeded` with reason `conversation_context_limit` | Start a new conversation |
| Poisoned or unwanted conversation | Stored turns are only ever replayed as conversation turns, never as system instructions | Start a new conversation; the old one expires after 30 days |

## Signals and alert thresholds

Backend telemetry carries a content-free `history.store` event with outcome and `store_seconds`.
These are the thresholds to configure when alert delivery is approved. No alert definition is
created by feature 003, and the owner is the platform on-call named in the monitor configuration.

| Signal | Threshold | Response |
|--------|-----------|----------|
| History-store failure rate | Above 5% of `history.store` events for 5 minutes (warning); above 20% for 5 minutes (page) | Check Cosmos availability, throttling (429) and the private endpoint; confirm `/readyz` behavior on affected instances |
| History-store latency | p95 of `store_seconds` above 1 s for 5 minutes (the per-call budget is 2 s) | Check Cosmos autoscale ceiling and regional health; scale the ceiling only through the reviewed platform change |
| Readiness | Any instance reporting `history_store_unavailable` for more than 2 minutes | Treat as a store or identity fault, not an application fault |

Never copy conversation text into tickets, chat or logs while diagnosing. Use outcomes, counts and
durations only.
