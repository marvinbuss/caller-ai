# Contract: Configuration schema 2.2.0 (slice 2)

**Owner**: management (writes), runtime (reads the projection). **Schema file**: `code/contracts/configuration.schema.json` is updated in the same change.

## Additive fields

```json
{
  "schemaVersion": "2.2.0",
  "conversationSettings": {
    "voice": "marin",
    "voiceInstructions": "…",
    "voicePlatformRules": "…",
    "voiceGlobalPrompt": "…",
    "textPlatformRules": "…",
    "textGlobalPrompt": "…",
    "quietHandover": true
  },
  "agents": [
    {"agentId": "…", "prompt": "…", "voiceAddition": "…", "textAddition": "…"}
  ]
}
```

- Every new field is optional. Limits are in [data-model.md](../data-model.md).
- **Absent means default.** Management never writes a default value into a stored revision; "Restore default" removes the field.
- **Version rule.** Management writes `2.2.0` only when the draft contains at least one 2.2.0 field; otherwise it keeps the stored version. Both services accept `2.0.0`, `2.1.0` and `2.2.0`.
- **Unknown fields** are still refused (`additionalProperties: false`).

## Stored revision checks

- `verified_revision` keeps returning the stored shape (`exclude_unset`), so verifying a revision twice gives the same result (PR #43).
- The two-form graph hash check from PR #42 is unchanged.
- **Tests:** a revision written by each earlier release (`stored_by_earlier_release` helper for `2.0.0` and `2.1.0`) must pass all of these:
  - load and be served;
  - be projected for the runtime;
  - be re-published after one 2.2.0 edit (FR-034, SC-008).

## Runtime projection

`GET /v2/runtime/configuration` always carries all six settings and both agent additions, resolved from the stored value or the management default. `policyHash` covers the resolved projection. The runtime's `RevisionSnapshot.validate` accepts the resolved shape and the earlier shapes.

## Publication refusals

| Code | When |
|------|------|
| `configuration_text_too_large` | A field over its limit, or a composed agent instruction over 36 KiB; `path` names the field (FR-033) |
| `platform_rules_unconfirmed` | Platform rules or voice instructions changed without the confirmation flag in the Settings publish request (FR-030, FR-037) |

## Compatibility and rollback

- **Rolling back to slice 1 management** (2.1.0 writer) while 2.2.0 revisions exist: the 2.1.0 writer refuses to load them (`invalid_snapshot`). The rollback procedure is: before rolling back, publish the last 2.1.0 revision's content again as a new revision without any 2.2.0 field. This is documented in the configuration runbook.
- **Runtime rollback** while management serves 2.2.0 projections: the 2.1.0 runtime refuses unknown fields. Roll the runtime back only together with management.
