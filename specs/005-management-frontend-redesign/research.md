# Research: Management Frontend Redesign

Feature: [spec.md](spec.md) · Plan: [plan.md](plan.md)

Each decision records what was chosen, why, and what else was considered. Findings are based on the current code in `code/management` and `code/backend` and on the contracts in `specs/002-multimodal-agent-management/contracts/` and `specs/004-int-environment-setup/contracts/`.

## R1. Default communities are a fixed projection, not stored records

**Decision**: The Welcome, Routing and Goodbye communities are drawn by the management frontend from the three protected agents (`role` = `welcome`, `routing`, `goodbye`). They have reserved, fixed identifiers in the view model (`default-welcome`, `default-routing`, `default-goodbye`) and are never written to the configuration. The protected agents keep `communityId: null` in the stored and published configuration.

**Rationale**: The runtime rejects protected agents with a community (`visibility.py`: `invalid_membership`) and drives the lifecycle Welcome → Routing → business community → Goodbye from the roles, not from communities. Projecting the default communities gives the user the requested picture (fixed communities, fixed Welcome → Routing link, Goodbye reachable from everywhere) without a runtime migration, and makes "cannot delete or modify" structural: there is nothing to delete. The spec edge cases about older configurations disappear, because nothing is stored differently.

**Alternatives considered**: Store three real community records and move the protected agents into them. Rejected: needs changes to runtime membership, lifecycle and visibility rules and a data migration, for no behavior change. A `kind: default` flag on stored communities: same cost, plus a way to corrupt the fixed structure through the API.

## R2. Routing reach becomes an explicit, published choice

**Decision**: Add `routable` (boolean) to each custom community. The graph draws a Routing → community link when `routable` is true; drawing or removing that link sets the flag. The runtime's Routing agent offers only enabled, routable communities with an enabled entry agent. A missing flag reads as `true`, so every existing configuration keeps today's behavior. A community is *unreachable* when it is enabled, not routable, and no chain of community connections from a routable, enabled community leads to it; the editor marks it and lists it in the publish confirmation (FR-012).

**Rationale**: Today Routing silently offers every enabled community (`visibility.view_for`, role `routing`). The spec asks managers to define the connections from Routing, and to see unreachable communities. One additive flag is the smallest contract change that makes the drawn link mean what it shows.

**Alternatives considered**: Model Routing → community as a `communityConnections` entry with a reserved source id. Rejected: mixes a reserved id into a list the runtime validates against real communities. Display-only links without runtime effect. Rejected: the graph would show a choice the conversation does not honor.

## R3. Configuration schema 2.1.0, additive and backward compatible

**Decision**: Raise `schemaVersion` to `2.1.0` with two additive changes: `Community.routable` (R2) and an optional top-level `conversationSettings` object (R5). The runtime accepts `2.0.0` and `2.1.0`; the management app writes `2.1.0`. Deployment order: runtime first, then management. `layout` stays readable for old revisions but new revisions write an empty layout (R6).

**Rationale**: Constitution IV requires a compatibility plan for contract changes. Both changes have safe defaults, so a runtime that knows 2.1.0 serves both versions, and rollback of management alone is safe.

**Alternatives considered**: Keep 2.0.0 and add the fields silently. Rejected: the runtime checks the version string, and unknown fields would be rejected by the strict models.

## R4. Settings and graph have separate drafts over one revision stream

**Decision**: The Settings page keeps its own draft containing only `conversationSettings`. Publishing it reads the current active revision, replaces `conversationSettings`, and calls the existing `PUT /v2/configuration` with the active revision's ETag in `If-Match`. The graph page does the same with everything except `conversationSettings`, which it copies from the active revision. A conflict (`409`) keeps the page's draft and shows the new-revision notice.

**Rationale**: Matches the clarified answer (separate drafts) with no new write endpoint: the existing endpoint already enforces atomic publication, validation, secret scanning, audit and ETag concurrency.

**Alternatives considered**: A new `PATCH /v2/configuration/settings` endpoint. Rejected: a second write path would duplicate validation and audit. One shared draft. Rejected by the clarification.

## R5. Editable conversation settings and their limits

**Decision**: `conversationSettings` holds three optional values; an absent value means "use the environment default".

| Setting | Field | Allowed values | Environment default and ceiling |
|---------|-------|----------------|-------------------------------|
| Voice | `voice` | One of the voices the runtime reports as supported | `CALLER_LIVE_VOICE` |
| Reply length limit | `maxOutputTokens` | 1 to the environment ceiling | `CALLER_MAX_OUTPUT_TOKENS` (1 to 4096) is the ceiling |
| Idle timeout | `idleTimeoutSeconds` | 30 to 120 | 120 (today's fixed value) |

The runtime owns the list of supported voices as a reviewed constant and reports it, with the ceilings, through the status endpoint (R7), so the Settings page validates against the same values the runtime enforces. The voice is chosen from a drop-down built from that list (R15). The runtime applies the values when a session is admitted, so running sessions keep theirs. The management test relay reads the idle timeout from the active revision instead of its own 120-second constant.

**Rationale**: These are the three settings the clarification named; each maps to an existing runtime value. Bounds never exceed what the environment allows today, so no capacity or cost target changes (Constitution VI).

**Alternatives considered**: Store the voice list in the management app. Rejected: two sources of truth. Let editors raise limits above today's. Rejected: changes reviewed capacity and cost.

## R6. Graph view preferences live in the browser

**Decision**: Node positions and collapsed state are stored in the browser's local storage, keyed by environment origin, signed-in user reference and node id, capped at 400 entries with oldest-first eviction. Nodes without a stored position are placed by a deterministic automatic arrangement (communities in a grid ordered by name; agents in rows inside their community; default communities fixed in the first row). Moving or collapsing never touches the draft.

**Rationale**: The clarification made both personal and unpublished. The current graph component already caches positions locally (up to 200 nodes); this extends that cache and removes the write to `layout`.

**Alternatives considered**: Server-side per-user preference store. Rejected: a new data store for cosmetic state, with retention and privacy obligations.

## R7. Status comes from a new read-only runtime endpoint

**Decision**: Add `GET /v1/status` to the runtime, protected by the existing workload identity check (same audience, role and principal as the conversation endpoints). It returns the Foundry account host for text and voice, the text deployment name, the voice region and voice model (`gpt-live-1`, the name sent in `session.start`), the supported voices, the setting ceilings, and a reachability result per model with its check time. The management app exposes `GET /v1/status` to signed-in users with either role and calls the runtime with its workload token. Results are cached for 60 seconds on each side.

Reachability is checked without a billable model call: the runtime acquires a token for the Foundry scope and opens a TLS connection to the endpoint host within 2 seconds, and also reports the outcome and time of its most recent real model call.

**Rationale**: Only the runtime knows the model settings (`CALLER_TEXT_*`, `CALLER_LIVE_*`); the management app has none of them. The workload identity path already exists, so no new identity or role is needed. Host names and deployment names are configuration, not secrets (FR-024).

**Alternatives considered**: Duplicate the model settings into the management app. Rejected: drift between the two apps. Query Azure Resource Manager from the management app. Rejected: needs a new Azure role and gives configured state, not what the runtime uses. Send a minimal completion request. Rejected: costs tokens on every check.

## R8. Per-agent "last changed" comes from publication stamps

**Decision**: When the management app publishes a revision, it compares each agent with the previous active revision and writes a publication record outside the graph: `publishedAt`, `publishedBy` (actor reference), `publishedByName` (display name from the sign-in token) and `agentStamps` (agent id → revision number, time, actor reference and name of the last change). The stamp carries forward unchanged for agents that did not change. The runtime projection never includes it.

**Rationale**: FR-020 needs "when and by whom" per agent from existing data. The audit log records only actor reference and revision per publication, so a per-agent answer would need a scan of all revisions on every page load. Stamping at publication is O(agents) once and O(1) to read.

**Privacy**: The publisher's display name becomes part of the revision metadata, visible only to editors, retained with the revision, and never logged. It is staff identity, not caller content (Constitution II and VII).

**Alternatives considered**: Show only the actor reference. Rejected: an opaque identifier is not "by whom" for a person. Scan revision history on demand. Rejected: cost grows with history.

## R9. One authenticated frame and a login page

**Decision**: Every page renders a single root frame component. Before the session is known it renders nothing but a neutral loading state; without a session it renders the login page; with a session but no role it renders the "no access" page; otherwise it renders the sidebar, top bar and the page. Page data loads only through role-checked server events, so no management data reaches an unauthenticated browser. `/auth/callback` redirects to a validated `returnTo` path (same origin, from a fixed allowlist of page routes) or to `/`. New routes: `/login`, `/` (Dashboard), `/communities`, `/test`, `/status`, `/settings`. Old routes `/configuration`, `/configuration/communities/[community_id]` and `/configuration/test` redirect to the new ones.

**Rationale**: Reflex pages are exported as a static bundle; access control must come from server state and role-checked events, which the app already uses. A strict `returnTo` allowlist prevents an open redirect (OWASP A01).

**Alternatives considered**: Server-side redirects for every route in front of the static bundle. Rejected: the frontend is served as static files; the data path is the real boundary and is already enforced.

## R10. Session expiry warning and extension

**Decision**: Add `POST /auth/session/extend` (CSRF-protected) that reissues the session for another 15 minutes, capped by the sign-in token's expiry as today. The frame shows a warning 5 minutes before expiry with "Stay signed in". If the session can still be extended, "Stay signed in" extends it. If the sign-in token itself is about to expire, "Stay signed in" saves the open graph and settings drafts (with the ETag they started from) in the tab's session storage, keyed by actor reference, then runs a sign-in that returns to the same page; after sign-in the drafts are restored only for the same actor reference and then removed from storage. Sign-out, a different user, or a restore failure clears the saved drafts. If the user ignores the warning and the session expires, the drafts are discarded and the login page says so (FR-006).

**Rationale**: Matches the clarification (5-minute warning) and keeps FR-006's promise without a server-side draft store; the sign-in token cap stays a security boundary. Session storage is per tab and cleared when the tab closes; the drafts hold configuration text, not caller content or credentials.

**Alternatives considered**: Silent refresh with refresh tokens. Rejected: adds token storage on the server and a broader credential.

## R11. Tester-only access to Dashboard, Status and Test

**Decision**: Once `TESTER_ONLY_TESTS` is turned on after the security review (see the release gate below), browser sessions with only the tester role may open Dashboard, Status and Test and start tests, and the editor role is no longer required for tests. Server checks: ticket issuance and the console socket require the tester role; configuration reads and writes require the editor role; status requires either role. All existing per-user and global test limits stay.

**Release gate** (owner decision, 2026-11-11): Tester-only test access ships behind the switch `TESTER_ONLY_TESTS` in `management_app/security.py`, `False` by default. While it is `False`, ticket issuance and the console socket require the editor and tester roles as today, and a tester-only session sees Dashboard and Status only. The switch is turned on by a reviewed change that records the completed security review; until then this part of the feature is not released, as the constitution's release gate requires. Everything else ships independently.

**Threat review** (Constitution II): A tester can now start tests without the right to change configuration. Tests run only against the published revision, under the existing admission limits (one session per user, five global slots, ten-minute lifetime) and the same storage rules, so the added exposure is model cost by a group that already holds the tester role. Automated authorization tests cover every role combination with the switch off and on; the owner deferred the manual security review and all manual tests to a later stage (2026-11-11), and the switch stays off until that review is recorded.

## R12. Visual design tokens and accessibility

**Decision**: Move all colors, spacing and type into one tokens module used by every page. Keep the teal brand; default items use a neutral slate fill, a lock icon and the label "Default"; custom items use a white fill with a teal border, a pencil icon and the label "Custom"; the entry agent gets a flag icon and the label "Entry". All text and essential graphics meet WCAG 2.1 AA contrast. The graph gets a keyboard- and screen-reader-friendly outline list beside it: selecting an item in the list selects the node and opens its settings.

**Rationale**: Two visual means plus a label (FR-015) work in grayscale. The outline list is the reliable way to make a canvas graph operable by keyboard and assistive technology (FR-032).

## R13. Live notice for other managers' publications

**Decision**: While the Community and Agent or Settings page is open, a background task asks `GET /v2/configuration` with `If-None-Match` every 15 seconds and shows a notice with the new revision number when the ETag changes. Reload is the manager's choice; the draft is never replaced silently.

**Rationale**: Meets SC-011 (notice within 30 seconds) with the existing endpoint and conditional requests; no push channel is needed.

## R14. Observability

**Decision**: Add these management events to the documented schema, with no personal content: `ui.page_view` (page id, outcome), `auth.sign_in` (outcome, reason code), `auth.session_extend` (outcome), `configuration.rule_refused` (rule id, item kind), `status.check` (outcome, per-model result). Publication keeps its existing `configuration.publish` audit and event. The runtime adds `status.probe` (model, outcome, duration).

**Rationale**: FR-035 and Constitution VII; reuses the existing `Telemetry.event` path.

## R15. Voice catalog and drop-down

**Decision**: The runtime holds a reviewed voice catalog for `gpt-live-1`. Each entry has an API name, display name, language, regional influence and presentation. The Settings page shows the catalog as a drop-down grouped by language, labeled like "Quartz — English (Australian), feminine", with the environment default marked. Only catalog names are accepted by management validation and by the runtime.

Catalog as researched on 2026-11-11 from the GPT-Live session guide ([Managing GPT-Live sessions, Voice options](https://developers.openai.com/api/docs/guides/live-conversations#voice-options)) and the Foundry guide ([Use GPT-Live for real-time voice](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/gpt-live), which states `audio.output.voice` defaults to `marin`):

| API name | Language | Regional influence | Presentation |
|----------|----------|--------------------|--------------|
| `marin` | English | — | — (service default) |
| `quartz` | English | Australian | Feminine |
| `ripple` | English | Australian | Masculine |
| `vesper` | English | British | Masculine |
| `willow` | English | Irish | Feminine |
| `stone` | English | Irish | Masculine |
| `gleam` | English | North American | Feminine |
| `meridian` | English | North American | Masculine |
| `beacon` | English | Filipino | Masculine |
| `delta` | English | Southern U.S. | Feminine |
| `cinder` | English | Southern U.S. | Masculine |
| `bossa` | Portuguese | Brazilian | Feminine |
| `tempo` | Portuguese | Brazilian | Masculine |
| `nuri` | German | — | Feminine |
| `juni` | German | — | Feminine |
| `flitz` | German | — | Masculine |
| `brise` | French | — | Feminine |
| `sillage` | French | — | Masculine |
| `nira` | Hindi | — | Feminine |
| `harema` | Japanese | — | Feminine |
| `shida` | Japanese | — | Masculine |
| `noeul` | Korean | — | Feminine |

**Rationale**: A drop-down built from one runtime-owned list prevents typos and unsupported values, and the status endpoint already carries it to the management app.

**Not included**: The Realtime voices (`alloy`, `ash`, `ballad`, `coral`, `echo`, `sage`, `shimmer`, `verse`, `cedar`) are listed for the Realtime API but not in the GPT-Live voice table, so they stay out until GPT-Live documents them. Custom voices need separate approval and are out of scope.

**Follow-up in implementation**: Re-read the source table when the catalog constant is written (the extracted table may have omitted rows) and record the date in the constant's docstring. An unsupported voice fails the session start with the existing `live_unavailable` path; the Settings page never offers it.

## R16. Caller AI logo

**Decision**: A new vector logo: a rounded teal tile (`#197767`) holding a white speech bubble with four teal waveform bars, and a small white four-point spark in the top-right corner for "AI". The full logo adds the word mark "Caller" in the text color and "AI" in teal. Design files: [assets/caller-ai-mark.svg](assets/caller-ai-mark.svg) and [assets/caller-ai-logo.svg](assets/caller-ai-logo.svg); implementation copies them to `code/management/assets/` and uses the full logo on the login page and the mark plus product name in the top bar, replacing the headset icon and the "CALLER / CONTROL" text. The favicon is generated from the mark.

**Rationale**: An original, simple vector mark renders sharply at every size, uses only the existing brand colors, and has 5.6:1 contrast for white on teal. Both SVGs carry a `title` for assistive technology.

**Alternatives considered**: Keep the generic headset icon. Rejected: the request asks for a Caller AI logo. A raster image. Rejected: blurry when scaled and larger.

## R17. Automated validation only; manual checks deferred

**Decision**: Every acceptance scenario is validated by automated tests (unit, contract, integration, browser). Manual checks are deferred to a later stage by the owner (2026-11-11): the manual security review, the usability checks with managers behind SC-003, SC-005 and SC-007, and any manual environment walkthrough. For those criteria the feature delivers automated proxies: one-selection navigation and named-page assertions (SC-003), a scripted end-to-end create-connect-publish flow within the time budget (SC-005), and a grayscale rendering check that default and custom nodes differ by shape, icon and label (SC-007).

**Rationale**: Owner instruction; keeps the feature releasable on CI evidence while recording what is still owed.
