# Contract: Configuration schema 2.1.0

Delta to [specs/002-multimodal-agent-management/contracts/configuration.md](../../002-multimodal-agent-management/contracts/configuration.md). Everything not listed here is unchanged: endpoints, ETag and `If-Match` rules, validation error shape, secret scanning, audit, and the runtime projection's exclusion of layout and drafts.

## Versions

| Writer | Writes | Reader | Reads |
|--------|--------|--------|-------|
| Management app (this feature) | `2.1.0` | Runtime | `2.0.0` and `2.1.0` |

Deploy the runtime first. A management rollback alone is safe; a runtime rollback requires that no `2.1.0` revision is active (republish the last `2.0.0` revision first).

## Additions

### `communities[].routable`

```json
{ "communityId": "billing", "routable": false, "...": "..." }
```

- Boolean, optional on read, default `true`; always written by the management app.
- Runtime: the Routing agent's view lists only communities that are enabled, routable and have an enabled entry agent.
- Management validation: unchanged rules; an enabled community that is neither routable nor reached through community connections is a **warning** in the publish response, not an error.

### `conversationSettings`

```json
{
  "conversationSettings": {
    "voice": "marin",
    "maxOutputTokens": 800,
    "idleTimeoutSeconds": 90
  }
}
```

| Field | Rule | Error code on violation |
|-------|------|-------------------------|
| `voice` | An `id` from `supportedVoices` in the runtime status (the reviewed GPT-Live catalog) | `invalid_configuration`, rule `conversation_voice` |
| `maxOutputTokens` | Integer, 1 to `limits.maxOutputTokens` | rule `conversation_limit` |
| `idleTimeoutSeconds` | Integer, 30 to 120 | rule `conversation_limit` |
| unknown fields | Not allowed | rule `schema` |

Runtime projection (`GET /v2/runtime/configuration`) includes `conversationSettings` and `routable`; the policy hash covers both.

### Reserved identifiers

Custom community ids must not start with `default-`, and custom community names must not equal `Welcome`, `Routing` or `Goodbye` (case-insensitive). Rule `reserved_name`.

## Deprecation

`layout` is still accepted and validated on read. The management app writes `{"communities": [], "agentsByCommunity": {}}` from this feature on. Removal is a later breaking change.

## Publish response addition

`PUT /v2/configuration` success body gains:

```json
{ "warnings": [ { "code": "community_unreachable", "communityId": "billing" } ] }
```

Warnings never block publication.

## Publication metadata (management store only)

Stored next to each revision and returned by `GET /v2/configuration` to editors only; never part of the runtime projection or the hashes:

```json
{
  "publication": {
    "publishedAt": "2026-11-11T10:00:00Z",
    "publishedBy": "actor-3f2a",
    "publishedByName": "Example Editor",
    "agentStamps": {
      "agent-123": {
        "revisionNumber": 41,
        "changedAt": "2026-11-11T10:00:00Z",
        "changedBy": "actor-3f2a",
        "changedByName": "Example Editor"
      }
    }
  }
}
```
