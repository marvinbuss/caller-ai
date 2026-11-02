# Research: Unified Multimodal Agents and Management

**Date**: 2026-10-29
**Status**: Phase 0 complete after the user's explicit shared reasoning/text backend approval.
This document records research, not live qualification or authorization to widen scope.

## Decision 1: Exact Foundry GPT-Live Identity and Client Delegation

**Decision**: The requested product exists in first-party Foundry documentation as
`gpt-live-1`. Use only explicit client delegation for speech; never Azure Voice Live,
Realtime events or provider Responses delegation as a replacement.

**Rationale and evidence**:

- [Foundry overview](https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/gpt-live)
  names `gpt-live-1`, describes a voice model plus separate application backend, and states
  `Output | Voice only`.
- [Foundry connection guide](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/gpt-live)
  documents `wss://<resource>.openai.azure.com/openai/v1/live/sessions`, a `session.start`
  configuration, explicit `delegation: {type: client}`, and readiness at `session.started`.
- [Foundry delegation guide](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/gpt-live-delegation)
  states that client delegation hands work to the application. `session.delegation.created`
  carries target `client` and a delegation ID, not task text. The application uses transcript
  fragments and authoritative state, performs authorized work and returns bounded context via
  `session.commentary.append` or `session.thinking.append`, with the delegation ID.
- [OpenAI exact model card](https://developers.openai.com/api/docs/models/gpt-live-1)
  and [client-delegation guidance](https://developers.openai.com/api/docs/guides/live-delegation)
  are general guidance only; they do not override the Foundry-specific service contract.

Each context append is plain text limited to 500 tokens. An append acknowledgement confirms
context injection, not task success or audible playback. Transcript fragments are not complete
or authoritative turns. Interrupted speech does not automatically cancel backend work.

**Alternatives considered**: No excluded service is investigated or adopted. Provider
Responses delegation is explicitly rejected. A legacy Realtime API cannot supply missing Live
operations or establish compatible semantics.

## Decision 2: Modality and Agent-Execution Model Gate

**Decision**: Resolved by user approval on 2026-10-29: allow a separate Foundry reasoning/text
model. One application-owned Agents backend serves both modes; GPT-Live is the only speech
service. Text calls that backend directly without microphone access or a GPT-Live session.

**Rationale**: Foundry describes voice-only model output and a separate reasoning/tool backend.
The documented context-append events are instructions/thinking/commentary, not proof of an
ordinary typed-user-turn/text-reply contract. Rendering an audio transcript is not by itself
proof of the required no-microphone text-chat mode. The original GPT-Live-only wording did not
explicitly permit the additional execution model required by the shared Agents backend.
The user's recorded planning clarification resolved that scope question: a separately
configured Foundry reasoning/text model is now explicitly permitted, with no second speech
service or provider Responses delegation.

**Implementation decision**: Use the existing Agents SDK with an explicit
`OpenAIChatCompletionsModel` and asynchronous Azure client, not SDK implicit model/provider
defaults. Configure a reviewed Foundry text deployment supporting streaming and strict function
tools. Its deployment name is operator configuration, not a hard-coded publication field or
automatic fallback. The GPT-Live client adapter uses maintained `websockets` directly, avoiding
an unverified SDK Live abstraction. No dependency upgrade is required by this decision.

[Agents model integration](https://openai.github.io/openai-agents-python/models/) documents
explicit custom clients and the Chat Completions adapter. The
[Foundry v1 guide](https://learn.microsoft.com/en-us/azure/foundry/openai/api-version-lifecycle)
documents Azure v1 Chat Completions, deployment-name selection, Entra token refresh, scope
`https://ai.azure.com/.default` and the `Cognitive Services OpenAI User` role. The exact
approved deployment is validated before live activation, not discovered implicitly. Disable
SDK-hosted traces and persistence; application metadata-only observability remains enabled.

**Alternatives considered**: Do not silently use transcripts as text chat, generate synthetic
speech from typed input, enable Responses delegation, substitute another speech service, drop
the approved text requirement or fabricate an SDK capability.

## Decision 3: Conservative Foundry Transport and Operational Facts

**Decision**: Treat the dedicated Foundry Live how-to/reference as the candidate audio contract
and keep unverified operational details explicit; no live endpoint or credential is accessed.

The dedicated guide specifies raw mono PCM16LE at 24 kHz, base64 `session.input_audio.append`
and `session.output_audio.delta` with timeline ranges. It documents `session.close` followed by
`session.closed`; missing final usage/closure is uncertainty, not successful cleanup. Do not
invent a Realtime-style clear/cancel event. The overview lists additional audio formats while
the dedicated guide specifies 24-kHz PCM; do not broaden the candidate codec set from that
discrepancy. Public-provider timing and acknowledgement differences are not Azure guarantees.

Microsoft Entra identity is preferred by the Foundry guide. Use the documented v1 scope/role
through an explicit asynchronous credential provider, with the Live bearer header server-side.
Require an operator-approved mapping of the Live deployment to `gpt-live-1`, supported region,
lifecycle/preview assessment and both deployments' quotas before activation. Never infer Live
GA from general v1 GA. Direct WebSocket transport removes the need to assume SDK Live support.
The published Live quota table is 10 concurrent sessions for non-tiered, 25/50/200/300/500 for
tiers 1 through 5. The app admits at most five, also counting unconfirmed closures, and respects
lower available reviewed capacity. A catalog entry is not actual entitlement or free capacity.
These are activation/acceptance checks, not authorization to provision or increase timeouts.

**Design evidence versus activation evidence (remediation agreed 2026-10-29)**: The official
regional catalog lists `gpt-live-1` version `2026-09-10` under Global Standard in
`canadacentral`, `centralus` and `eastus2`. Together with the published concurrent-session
limits and verified Live/client-delegation API documentation, this is design-level evidence
for the offered model, supported regions and the five-session architectural target. Global
Standard does not guarantee inference stays in the deployment's region. The previous C1
finding overstated a blanket implementation block by demanding subscription/deployment
evidence at that stage. Correct the documentation distinction, not the constitution.
Actual access, deployment/version binding, remaining quota, identity, lifecycle/preview,
processing/retention and real performance are separately approved pre-live checks. Confirm
the selected reasoning/text binding's published capabilities/limits before approving it;
no alternate provider, live call, resource creation or new approval is implied here.

Additional first-party references:

- [Foundry event reference](https://learn.microsoft.com/en-us/azure/foundry/openai/gpt-live-reference).
- [Foundry regional model catalog](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/models-sold-directly-by-azure-region-availability).
- [Confirmed Americas Global Standard table](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/models-sold-directly-by-azure-region-availability?tabs=az-americas&pivots=standard).
- [Azure OpenAI v1 authentication/version guidance](https://learn.microsoft.com/en-us/azure/foundry/openai/api-version-lifecycle).
- [Live session quotas](https://learn.microsoft.com/en-us/azure/foundry/openai/quotas-limits#gpt-live-concurrent-session-limits).
- [Official Python SDK](https://github.com/openai/openai-python); SDK source similarity is not
  an independently verified Foundry compatibility commitment.

## Decision 4: Python-Authored Graph and Browser Audio Boundaries

**Decision**: Keep Reflex-authored pages and evaluate a narrow OSS `@xyflow/react` component
wrapper. Use a minimal browser-only capture/playback component integrated through Reflex,
not an independently authored React app or audio serialized through Reflex state/Socket.IO.

**Rationale**: Current management has Reflex 0.10.0, React 19.3.0 and no graph/capture library.
Existing `EditorDraft` already owns mutations and conflict retention. A community route must
reuse the draft; the current unconditional `load()` replaces it and must not run on every
child-page navigation. Client-provided edges are edit requests, not authorization. Position
changes are presentation-only. Every graph operation needs an equivalent keyboard/form path.

Candidate audio uses secure standard WebSockets, bounded binary frames/control JSON and
AudioWorklet capture/playback. Browser support is not proof of the required latency or actual
headset routing. Start must resume/acquire audio from the user gesture, stop must handle late
permission completion, and unavailable output selection must identify the OS-default fallback.
No entire recording is buffered, retained or sent through Python UI state.

Sources:

- [Reflex React wrapping](https://reflex.dev/docs/wrapping-react/overview/).
- [Reflex custom hooks](https://reflex.dev/docs/wrapping-react/custom-code-and-hooks.md).
- [Reflex dynamic routing](https://reflex.dev/docs/pages/dynamic-routing.md).
- [React Flow accessibility](https://reactflow.dev/learn/advanced-use/accessibility).
- [WebSocket browser limitations](https://developer.mozilla.org/en-US/docs/Web/API/WebSocket).
- [AudioWorklet](https://developer.mozilla.org/en-US/docs/Web/API/AudioWorklet).
- [Audio output selection](https://developer.chrome.com/blog/audiocontext-setsinkid/).
- [WebSocket security](https://cheatsheetseries.owasp.org/cheatsheets/WebSocket_Security_Cheat_Sheet.html).

**Alternatives considered**: The official Reflex Enterprise Flow wrapper exists, but its terms
need review and it is not selected. Experimental capture wrappers, a separate JS frontend,
the existing source-only audio player and browser-to-provider credentials are not adopted.
Dependency versions, SSR and actual supported-browser behavior remain implementation tests.

## Decision 5: Shared Authority and Fresh Configuration

**Decision**: Retain management-owned Strong configuration publication, audit and capability
authority. Runtime consumes authenticated contracts and owns only its own conversation metadata.
Use a documented fresh-start versioned contract disposition; no published-data migration or
compatibility layer is needed because the user confirmed that no configurations exist.

**Rationale**: Existing Cosmos publication atomically creates immutable revision, active pointer
and current authority. Preserve exact ETag/conflict semantics, role protection and current-policy
intersection. A stable actor-keyed shared conditional admission record is required for one
test per manager across tabs/devices/modes. Per-cookie/process dictionaries cannot supply that
invariant. Unknown cleanup must not release a slot for a competing charged session.

Plan one 600-second lifetime and 120-second user-idle clock from admission/accepted text or
detected speech; silence, heartbeats and assistant output do not extend idle. Stop/navigation,
identity expiry and transport failure end capture/playback without automatic restart. Queue,
frame, text and credit limits must be finalized in Phase 1 without altering these user bounds.

**Alternatives considered**: No new database service, cross-service container access, schema
migration, independent modality-specific policy or hosting-specific admission controller.

## Decision 6: Root Documentation Move

**Decision**: Move the maintained `code/docs` tree to `docs` during implementation, retaining
suffixes and one maintained home. This planning command moves nothing.

**Rationale**: Current inventory is 32 maintained Markdown files, 31 inline Mermaid blocks and
no tracked non-Markdown assets there. Update `verify_docs.py` discovery, `ci.py` scanning,
`verify_release.py` evidence paths and declarative test-profile references. Repair inbound links
from spec 001 without rewriting its historical results or reopening its deferred tasks.
Docs-to-docs links retain relative paths; outgoing code links acquire the correct `code/` path.
Add image/asset/fragment and duplicate-old-tree checks to the existing tooling tests.

**Alternatives considered**: No duplicate maintained tree or hosting work hidden in the move.
Existing operational documents may move as documentation, not new infrastructure functionality.

## Research Outcome

Model existence and client delegation are confirmed, and the user resolved the modality gate
by explicitly allowing a shared Foundry reasoning/text backend. Phase 0 is complete; Phase 1
may define app contracts without inventing GPT-Live text events. All original clarifications
and three PR exclusions remain intact. Approved deployments, actual capacity, lifecycle terms,
physical audio and latency remain explicit activation/acceptance gates, not claimed evidence.
Only feature-002 planning/specification documents are changed; no application, dependency lock,
runtime pin, old spec, constitution, hosting configuration or live resource is changed.
