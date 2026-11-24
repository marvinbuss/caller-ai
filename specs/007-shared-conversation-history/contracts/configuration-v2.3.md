# Contract: Configuration schema 2.3.0

**Owner**: management (`models/configuration.py`, `configuration_validation.py`, `code/contracts/configuration.schema.json`); runtime reads the projection (`domain/configuration.py`). Documented in `code/contracts/README.md`, `docs/configuration-guide.md` and `docs/runbooks/configuration.md`.

## Changes from 2.2.0

| Field | Change | Limit | Visible to |
|-------|--------|-------|------------|
| `agents[].description` | New, optional | 1 KiB UTF-8 | Editors only (removed from tester projections, like additions) |
| `conversationSettings.announceAgentChanges` | New, optional bool | | Editors only |
| `relationships[].allowedContextFields`, `communityConnections[].allowedContextFields` | Unchanged in the schema; ignored by the runtime; no longer shown in the editor | | |

## Rules

- **Version written**: `2.3.0` only when `description` or `announceAgentChanges` is present; otherwise the version the revision would have had (2.0.0, 2.1.0 or 2.2.0).
- **Absent values**: absent means default (`announceAgentChanges = false`, no description). Absent fields are never written.
- **Revisions from earlier releases**: they load, verify twice, project and re-publish (SC-008).
- **Runtime projection**: carries `description` and `announceAgentChanges`. The runtime accepts `2.3.0` and treats each community connection as a direct path (FR-008).
- **Validation error**: a description over the limit is refused with `configuration_text_too_large` and the path `agents[N].description`.

## Editor

| Page | Change |
|------|--------|
| Agents | "Description (used to choose handover destinations)" text area with a byte counter |
| Relationships and connections | The "Request summary" and "Transfer reason" checkboxes are removed. Stored values stay unchanged (FR-021). Connections are labelled "Direct handover to the entry agent". |
| Settings | Checkbox "Announce agent changes", off by default, with help text: "When off, agents hand over to each other without saying so." The existing "Quiet update on handover" switch is relabelled "Quiet update on delegation"; its stored key `quietHandover` stays. |

## Rollback

Roll back the runtime first, then management. A runtime from before this feature refuses `2.3.0` revisions (strict model) and keeps serving the last loaded revision. Republishing without the new fields writes 2.2.0 again.
