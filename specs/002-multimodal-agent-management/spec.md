# Feature Specification: Unified Multimodal Agents and Management

**Feature Branch**: Unchanged; no branch-creation hook is registered.

**Created**: 2026-10-29

**Status**: Draft

**Input**: Replace realtime-model integration with GPT-Live client delegation, not responses
delegation; retain management-defined agents and communities backed by published configuration;
provide community and community-agent management pages; relocate repository documentation to
root-level docs; and let authorized managers test published configuration with browser text,
microphone capture and headset playback through the backend.

**PR scope boundaries (confirmed 2026-10-29)**: Work is limited to the requested GPT-Live
client-delegation backend, multi-page community/agent management, root documentation move and
published-configuration browser testing. All telephony integration and all hosting-specific
features are excluded. Azure Voice Live MUST NOT be used in any role; GPT-Live on Azure AI
Foundry is the only speech-model target. A separate Azure Foundry reasoning/text model is
approved for the shared application-owned Agents backend, called directly for text and through
GPT-Live client delegation for speech. Browser microphone/headset and text testing over
standard secure WebSockets remain in scope and do not require a telephone integration.

**Owner scope amendment (2026-11-03)**: The later explicit request adds Terraform CI/CD for
bootstrap and workload infrastructure, reusable protected workflows, one shared definition
per root type with environment variable/backend files, private saved-plan review and optional
managed alert destinations. This narrowly supersedes the earlier infrastructure-code exclusion;
it does not authorize actual apply, public-state/network exceptions, image/application deployment,
telephony or automatic reopening of feature-001 deferred operational work.

## Clarifications

### Session 2026-10-29

- Q: How should managers edit communities, agents, and their connections? -> A: Editable graphs with draggable community/agent boxes and directed connections, plus forms and equivalent keyboard controls.
- Q: What should happen to existing published configurations when the new connection graphs are introduced? -> A: There are no existing published configurations; start fresh, with no backward compatibility or migration requirement for this feature.
- Q: Should the management test console include text chat in this feature, alongside microphone/headset testing? -> A: Yes; include both text and speech testing in management, using the same published configuration.
- Q: Which browsers must pass microphone and headset testing before this feature is accepted? -> A: Desktop Edge and Chrome on Windows; the owner deferred Linux physical qualification on 2026-11-03. Retain mobile-width layout checks without requiring mobile audio qualification.
- Q: What limits should apply to each manager's live test sessions? -> A: One active session per manager across modes, tabs and devices; a ten-minute maximum and a two-minute inactivity timeout.
- Q: May a separate Azure Foundry reasoning/text model power the shared Agents backend, with GPT-Live used for speech via client delegation and text chat calling that same backend directly? -> A: Allow a separate Foundry reasoning/text model; one shared application-owned Agents backend serves both modes and GPT-Live remains the only speech service.

### Session 2026-11-03

- Q: How should Linux physical qualification proceed when no Linux device is available? -> A: Defer it to a follow-up; current physical acceptance covers Windows Edge and Chrome. This closes the Linux requirement by explicit deferral for this feature, not by a passing Linux test. Windows tests still require actual evidence.
- Q: What spend limit applies to the required full-duration qualification? -> A: The owner approves a 100 USD total ceiling. Use bounded provider/device checks and a 900-second endurance operation for admission plus five participants each completing the unchanged 600-second session lifetime; no automatic spend or execution before prerequisites.
- Q: What infrastructure delivery work is now requested? -> A: Terraform bootstrap and workload CI/CD using reusable protected workflows and shared IaC roots with per-environment variables/backends; the existing seed backend and supplied storage account are used, GitHub-hosted runners are the initial configurable default, and actual apply still requires reviewed plans and prerequisites.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Manage Communities and Their Transfer Paths (Priority: P1)

A configuration manager opens a community overview, edits communities and their directed
connections, and publishes a complete valid configuration. Selecting a community opens its
dedicated agent-management page without mixing agents from other communities into the view.

**Why this priority**: Community structure must be understandable and editable before testing
the behavior of the unified backend.

**Independent Test**: Using two communities and no model connection, configure a permitted
one-way community transfer, open each community, publish, and reload the same saved structure.

**Acceptance Scenarios**:

1. **Given** an authorized manager, **When** they open management, **Then** the first page shows
  an editable graph of the organization's communities and directed community connections,
  with forms and keyboard controls offering equivalent editing.
2. **Given** two communities, **When** the manager creates, changes or removes a directed
   connection, **Then** the configured allowed transfer direction is explicit and can be saved;
   the opposite direction is not silently granted.
3. **Given** a community on the overview, **When** the manager selects it, **Then** a distinct
   page opens for that community and identifies the selected community and a return path.
4. **Given** a community reference that does not exist or is no longer accessible, **When** a
   manager opens its saved link or reloads it, **Then** no other community is substituted and a
   safe unavailable or access-denied state is displayed.
5. **Given** unsaved edits or another manager's intervening publication, **When** navigation or
   saving would discard or overwrite those edits, **Then** the manager must explicitly discard
   them or receive a conflict while retaining the unsaved work.

---

### User Story 2 - Configure Agents Within One Community (Priority: P1)

A manager edits a community's agents and directed handover connections on its dedicated page,
chooses its entry agent, and publishes the change without changing application code.

**Why this priority**: The internal agent network defines which specialist can receive a
handover while preserving the community boundary.

**Independent Test**: With a community containing three agents, edit names/prompts, designate
an entry agent, change an allowed handover and verify the saved result after navigation/reload.

**Acceptance Scenarios**:

1. **Given** a selected community, **When** the manager opens its agent page, **Then** only its
  ordinary agents and their intra-community connections are editable there through an
  interactive graph and equivalent forms/keyboard controls.
2. **Given** an ordinary agent, **When** the manager edits its name, instructions, enabled state,
   reviewed capability assignment or an eligible handover connection, **Then** validation and
   publication use the same complete-configuration rules as the community overview.
3. **Given** an enabled community, **When** an edit removes or disables its referenced entry or
   leaves a dangling connection, **Then** publication fails with an actionable explanation
   without replacing the previous valid revision.
4. **Given** two ordinary agents in the same community, **When** one discovers its peers,
   **Then** eligible peers remain discoverable, but a handover succeeds only over an allowed
   directed agent connection; no foreign-community agent details are exposed.
5. **Given** a requested transfer to another community, **When** it is permitted by the community
   connections, **Then** Routing mediates selection of the destination's eligible entry agent;
   no direct cross-community agent connection bypasses Routing.

---

### User Story 3 - Use One Configured Backend for Text and Speech (Priority: P1)

Users interact with the same configured agents through either text or speech. The application
backend controls agent/community selection, handovers and capability authorization through
client delegation rather than leaving those decisions to provider responses delegation.

**Why this priority**: A shared, configuration-driven backend prevents text and speech from
developing incompatible agent definitions or different security rules.

**Independent Test**: Run equivalent text and speech conversations using the same published
revision; verify eligible agents, community transfers, tools and terminal behavior match.

**Acceptance Scenarios**:

1. **Given** a published configuration and an available approved GPT-Live integration,
   **When** a text or speech session begins, **Then** the backend selects and identifies its
   published revision and uses the configured protected roles, communities and agents.
2. **Given** a delegation request, **When** an agent, community, handover or capability must be
   selected, **Then** trusted backend policy handles the decision in client-delegation mode;
   provider responses delegation and browser assertions cannot grant authority.
3. **Given** equivalent text and speech requests, **When** the same published configuration
   applies, **Then** both obey identical entry, connection, capability and isolation rules.
4. **Given** an active session pinned to revision A, **When** revision B is published, **Then**
   the session keeps A for ordinary behavior while subsequent sessions receive B; security
   revocations still deny the next affected action regardless of the pinned revision.
5. **Given** configuration/model unavailability, **When** a session cannot safely proceed,
   **Then** it reports a bounded unavailable/failure outcome without silently using a legacy
   realtime integration, responses-delegation mode or fabricated configuration.

---

### User Story 4 - Test Published Configuration From a Browser (Priority: P1)

An authorized manager starts a clearly identified live test of the published configuration,
speaks through the device microphone, hears agent output through the selected headset/output,
and can also exercise text interaction without a separate agent backend.

**Why this priority**: Managers need to verify the behavior they have published without
placing a telephone call or installing a separate test client.

**Independent Test**: From an approved browser session, start a test, grant device permission,
exchange speech, interrupt playback, stop, and repeat using text; verify the revision and cleanup.

**Acceptance Scenarios**:

1. **Given** an authorized manager and an available test backend, **When** they explicitly start
   a speech test and grant permission, **Then** device audio reaches the backend and agent audio
   plays through the available selected/default output device.
2. **Given** no microphone permission, denied permission, unavailable devices or blocked playback,
   **When** speech testing is attempted, **Then** a safe actionable state is shown without a
   silent or falsely connected session; text testing remains usable where available.
3. **Given** published revision A and unsaved or unpublished changes, **When** a test starts,
   **Then** it uses A, shows A's identity and makes clear that the draft was not included.
4. **Given** a selected community page, **When** testing begins, **Then** the normal published
   Welcome/ Routing/entry flow is retained; page selection does not bypass routing permissions.
5. **Given** active playback, **When** the manager interrupts or stops the test, **Then** obsolete
   output stops, no new action is started after termination, and owned device/session resources
   are released within their declared bounds.
6. **Given** an unauthorized connection, expired session, disconnect or failed backend,
   **When** the test is started or interrupted by that failure, **Then** access fails closed,
   capture/playback stops and no automatic new charged session is created.
7. **Given** a manager with an active text or speech test, **When** another tab or device tries
  to start a second test, **Then** it is rejected without replacing the existing session.
8. **Given** a test reaches ten minutes or two minutes without user activity, **When** its
  deadline expires, **Then** capture/playback stops, the session terminates and a new test
  requires another explicit start.

---

### User Story 5 - Find Repository Documentation at the Root (Priority: P2)

A contributor finds maintained setup, architecture, acceptance and operational documentation
under the repository's root docs directory, with working references from the rest of the repo.

**Why this priority**: Documentation should be discoverable independently of implementation
layout, and moving it must not break setup, verification or operational guidance.

**Independent Test**: Starting at the repository root, follow setup, architecture and runbook
references and run the documented documentation checks without relying on the old directory.

**Acceptance Scenarios**:

1. **Given** the repository, **When** a contributor opens the root docs directory, **Then** it
   contains the maintained documentation previously located under code/docs without content loss.
2. **Given** a moved document or referenced diagram/asset, **When** it is opened from a repository
   reference or documentation check, **Then** relative links, assets and rendered diagrams resolve.
3. **Given** automation or contributor instructions that used the old documentation location,
   **When** the migration is complete, **Then** they use the root docs location as the single
   maintained home rather than silently maintaining two divergent copies.

### Edge Cases

- An empty organization has protected shared roles but no eligible business community: editing
  works and conversational admission remains unavailable until a valid community is published.
- Removing a community/agent with active references, adding duplicate/self/foreign connections,
  or changing an entry leaves no dangling graph; invalid complete publication is rejected.
- A browser opens a stale community link, navigates back/forward, reloads a child page, or changes
  views with unsaved edits: identity and draft/conflict rules still apply.
- An ordinary agent guesses a foreign identifier or a client submits invented graph/delegation
  instructions: server-owned authorization rejects the attempt independently of model prompts.
- Browser audio permission, headset routing, autoplay eligibility, connectivity or login expires
  mid-test: do not keep capturing silently or display a misleading connected state.
- Mute stops outgoing microphone audio; stop, page departure and connection loss stop capture
  and queued playback, including output from an obsolete agent or session.
- Slow clients, oversized messages, duplicate messages and exhausted test/model capacity cannot
  create unbounded buffering, duplicate sessions, repeated actions or unlimited charge exposure.
- Simultaneous test starts from one manager's tabs/devices cannot create parallel sessions.
  Silence, connection keepalives and agent output do not reset the user-inactivity deadline.
- Publication or revocation occurs during text or speech testing: ordinary revision pinning and
  current permission checks remain distinct and visible.
- Documentation contains links to code, old acceptance artifacts and diagrams: preserve historic
  evidence and fix paths without recertifying old results or changing completed feature scope.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The unified backend MUST target GPT-Live hosted on Azure AI Foundry and use
  client-delegation mode for this feature, not provider responses-delegation mode. Azure Voice
  Live MUST NOT be used, including as a wrapper, proxy, speech layer or fallback. A public
  OpenAI-hosted service, legacy realtime model or differently named model/service MUST NOT
  replace the requested GPT-Live speech integration in this PR. A separately configured Azure
  Foundry reasoning/text model MUST power the shared application-owned Agents backend; text
  calls that backend directly and speech delegates to it through GPT-Live client delegation.
  This is not permission for provider Responses delegation or another speech service.
- **FR-002**: The backend MUST own delegation, agent/community selection, handover authorization
  and capability execution. Neither the browser nor model output may become a trust authority.
- **FR-003**: Text and speech MUST use one authoritative published agent/community configuration
  and the same server-side isolation, relationship, revision and capability rules.
- **FR-004**: Published agent names, instructions, communities, entry agents, directed connections,
  reviewed tool/MCP assignments and protected roles MUST remain management-configured rather
  than being hard-coded into a separate live-test or modality-specific agent definition.
- **FR-005**: The backend MUST obtain complete validated published configuration from the existing
  authoritative store, through its owning service boundary, and identify the revision used.
  It MUST NOT trust a browser-supplied configuration or read another service's private store.
- **FR-006**: The new integration MUST replace the legacy realtime-model execution path for this
  feature without a silent fallback to it or to provider responses delegation. Missing approval,
  support or availability MUST be surfaced as unavailable, not fabricated protocol behavior.
- **FR-007**: Management's initial community page MUST show all communities in the one organization
  and their directed community connections in an editable graph, with authorized create/edit/remove
  operations available through the graph and equivalent forms/keyboard controls.
- **FR-008**: Selecting a community MUST open its distinct agent-management page, with a stable
  reloadable reference, selected-community identity and return navigation. Missing/unauthorized
  community references MUST fail safely without exposing another community's configuration.
- **FR-009**: The community agent page MUST support eligible agent creation, name/instruction and
  enabled-state edits, entry selection, reviewed capability assignments and directed intra-community
  handover connections through an editable agent graph and equivalent forms/keyboard controls,
  while preserving all protected-role and secret-reference rules. Both graph levels MUST support
  draggable boxes and directed-connection editing. Moving a box alone MUST NOT change membership,
  permissions or handover connections; connection changes require an explicit edit.
- **FR-010**: Directed community connections MUST restrict transfers from an active community to
  another community through Routing and the destination entry. Initial community selection by
  Routing remains separate. Ordinary agents MUST NOT transfer directly to foreign agents.
- **FR-011**: Directed agent connections MUST define allowed handovers within a community without
  hiding otherwise eligible same-community peers or granting access to peer tools/prompts.
- **FR-012**: Welcome, Routing and Goodbye MUST remain one configuration-wide protected set,
  outside ordinary communities and editable subject to their existing lifecycle responsibilities.
- **FR-013**: Publishing MUST validate the complete community/agent/connection/capability graph
  and reject dangling, duplicate or prohibited paths and invalid entries atomically. Previously
  published configuration MUST remain active when publication is rejected or interrupted.
- **FR-014**: Concurrent edits MUST use explicit conflict detection, retain unsaved edits on
  conflict and prevent silent overwrites. Navigation MUST require explicit confirmation before
  discarding unsaved changes rather than implying those changes were published.
- **FR-015**: Primary community, agent, publication and test workflows MUST have accessible names,
  keyboard alternatives and reachable controls at 360, 768 and 1440 pixel viewport widths,
  without overlapping controls or page-level horizontal scrolling.
- **FR-016**: Authorized managers MUST be able to start/stop browser tests of published
  configuration through a standard secure WebSocket connection to the application backend,
  not a direct browser connection containing model-provider credentials.
- **FR-017**: Speech tests MUST require an explicit user gesture and microphone permission;
  capture microphone audio, play agent audio through the available selected/default device,
  and expose connection, recording/mute, playback and terminal/error states truthfully.
  Required audio acceptance MUST cover current stable desktop Edge on Windows and Chrome on
  Windows, with tested versions recorded. Linux physical qualification is explicitly deferred
  to a follow-up, not reported as passed. Mobile audio qualification is not required.
- **FR-018**: Test controls MUST include start, stop and microphone mute/unmute. Stopping, leaving
  the test surface, losing authorization or losing the connection MUST stop capture/playback;
  background navigation MUST NOT continue an unnoticed live test.
- **FR-019**: The same test surface MUST support text input and visible text replies without
  requiring microphone permission, using the same published configuration and policy as speech.
  Both text and speech testing are required in management in this feature, not deferred to a
  separate text client. Simultaneous mixed input in one conversation is not required.
- **FR-020**: Every test session MUST display its selected published revision and modality.
  Unsaved drafts MUST NOT be executed. Existing sessions keep their revision for ordinary
  edits; new tests select the latest complete publication within 60 seconds.
- **FR-021**: Tests MUST follow normal protected-role and routing behavior, have one logical
  session and active agent owner, reject obsolete output and initiate no action after ending.
  Duplicate start/end/disconnect events MUST NOT create duplicate sessions or closures.
- **FR-022**: Client-delegated capabilities MUST remain reviewed public-information/read-only
  operations, with pinned grants intersected with current permission before every attempt.
  Revocation MUST deny the next affected action; private data and business changes remain denied.
- **FR-023**: Test connections and management views MUST enforce authenticated configuration/test
  permission, allowed-origin and session checks. Model/provider credentials MUST remain server-side,
  absent from browser storage, user-visible configuration, connection URLs, logs and errors.
- **FR-024**: Conversation content MUST remain isolated and temporary. This feature MUST NOT
  durably retain microphone audio, complete test transcripts, runtime-assembled model prompts
  or tool payloads; volatile conversation content MUST be discarded within five minutes after
  termination. Manager-authored agent instructions remain part of the published configuration,
  not a retained conversation or runtime-prompt dump.
- **FR-025**: Session, publication, denial, delegation and failure events MUST emit bounded,
  reviewable metadata for both services without conversation content or credentials. Test activity
  MUST be identifiable as testing, not counted as production telephone-call acceptance.
- **FR-026**: The backend MUST bound test concurrency, queues, message size, retries and session
  duration; declare capacity/admission limits and reject unavailable admission within ten seconds.
  It MUST enforce one active test session per authenticated manager in total across text/speech
  modes, tabs and devices, a ten-minute maximum from successful admission, and a two-minute
  inactivity timeout measured from the last accepted user text or detected user speech (or
  admission when no input has occurred). Silence, keepalives and agent output MUST NOT reset
  inactivity. A second start MUST be rejected without replacing the active session. Expiry MUST
  stop capture/playback and end the test; disconnect or expiry MUST NOT silently create another
  charged conversation. Another test requires an explicit start after the previous session ends.
- **FR-027**: Interruption/stop MUST suppress obsolete audible output within one second. Delegation,
  capability, model and ownership failures MUST produce a safe outcome within ten seconds, while
  preserving established ownership and reporting uncertainty rather than claiming success.
- **FR-028**: Normal local startup and automated default tests MUST remain usable without live
  provider credentials, browser microphone access or cloud resources. Actual browser/model
  qualification MUST be separately authorized and cannot be inferred from controlled doubles.
- **FR-029**: Maintained repository documentation MUST move from code/docs to root-level docs,
  preserving document content, referenced assets and historical evidence, with one maintained home.
- **FR-030**: Repository entry points, relative links, diagram/assets paths, contributor instructions
  and documentation-validation/automation references MUST follow the move. New checks MUST reject
  broken local references and unintended reliance on the previous maintained-docs directory.
- **FR-031**: This feature MUST use a fresh-start baseline with no existing published
  configurations to migrate. Configuration shapes and service contracts MAY change without
  backward compatibility; migration or legacy-compatibility workflows are not acceptance
  prerequisites. New publications MUST still enforce complete validation, protected roles,
  explicit transfer permissions and conflict handling.
- **FR-032**: Setup/operator guidance and automated tests MUST cover both modalities, both management
  levels, publication/conflict behavior, permission denial, device failure, interruption/cleanup,
  documentation relocation and local-versus-approved-live verification boundaries.
- **FR-033**: This PR MUST NOT research, select, implement or qualify telephone-provider
  integrations, telephone-number/PSTN connectivity, carrier callback/media adapters or actual
  telephone-call flows. Telephony decisions and implementation belong to a later specification.
  Browser text and microphone/headset tests MUST work independently of those deferred integrations.
- **FR-034**: Application behavior MUST remain verifiable without hosting-specific changes.
  The owner's 2026-11-03 amendment permits Terraform bootstrap/workload delivery definitions,
  environment-neutral roots, per-environment variable/backend files, optional managed alert
  destinations and reusable OIDC/private exact-plan workflows. It MUST NOT add runtime
  admission/retirement/autoscaling controllers, image/application deployment, unapproved apply,
  local/public state or key/CLI-authentication fallbacks, or silently reopen old release tasks.

### Key Entities *(include if feature involves data)*

- **Community**: Organization-owned grouping with a name, routing description, enabled state,
  eligible entry agent and its ordinary agent membership.
- **Community Connection**: Directed allowed transfer between two communities, enforced through
  Routing and destination-entry selection rather than direct foreign-agent access.
- **Agent and Agent Connection**: Configured ordinary agent and directed allowed intra-community
  handover, preserving role, membership, review and transfer-context restrictions.
- **Published Configuration Revision**: Complete validated protected-role/community/agent/connection
  and capability configuration, identified independently of unsaved editor state.
- **Delegated Conversation**: One backend-owned text or speech session with a verified participant,
  pinned published revision, active agent/community, temporary context and terminal outcome.
- **Browser Test Session**: Explicitly started authorized test conversation with selected modality,
  visible revision, connection/device state and bounded resource lifetime.
- **Documentation Reference**: Maintained document, linked asset or automation reference whose
  location must remain valid after root-level documentation relocation.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Across at least 20 eligible scripted journeys, split equally between text and
  speech, every journey uses its displayed published revision and obeys the same configured
  community, agent and capability permissions, with no modality-specific configuration fork.
- **SC-002**: Across all tested prohibited delegation, guessed foreign-agent, forged configuration,
  expired-session and revoked-capability attempts, zero unauthorized transfers, disclosures or
  external actions occur; no test uses an excluded model service, disallowed delegation mode
  or legacy fallback.
- **SC-003**: At 360, 768 and 1440 pixel widths, managers can create/edit connected communities,
  open a community, edit connected agents, publish and return through both interactive graphs
  and equivalent forms/keyboard controls, with zero obscured required actions, overlaps or
  page-level horizontal scrolling. Every graph-editing operation has a keyboard-operable path.
- **SC-004**: Every tested invalid or stale publication preserves the prior complete revision
  and the manager's unsaved edits; every valid publication survives navigation and reopening.
- **SC-005**: In all tested permission-denied/device-unavailable/playback-blocked scenarios,
  managers receive an actionable state and no unnoticed recording or falsely active test remains;
  text interaction does not require microphone permission. This MUST pass on desktop Edge on
  Windows and Chrome on Windows; results identify the browser versions tested. Linux physical
  testing is owner-deferred to a follow-up and is not part of this feature's current acceptance.
- **SC-006**: All tested interruptions, stops and disconnects suppress obsolete playback within
  one second, initiate zero post-terminal actions and release temporary content within five minutes.
  This also applies to ten-minute maximum-duration and two-minute inactivity expiry; simultaneous
  starts from a manager's tabs/devices produce at most one active test session, without automatic
  replacement or restart.
- **SC-007**: In an approved test environment, five concurrent managers can run isolated sessions
  for ten minutes; at least 95% of ordinary completed turns begin a relevant text/audio response
  within two seconds after the participant finishes input, excluding declared external-action waits.
- **SC-008**: Across at least 20 valid publications, every newly started test selects each new
  revision within 60 seconds, existing tests retain their ordinary revision, and every tested
  revocation blocks the next affected action.
- **SC-009**: After documentation relocation, all maintained documents/assets are discoverable from
  root docs, all checked local documentation references resolve, and all required diagrams render;
  no second divergent maintained copy remains at the old location.
- **SC-010**: Default startup and automated local checks require zero live phone/model calls,
  microphone permissions or external-service credentials. Separately approved browser/GPT-Live
  checks make zero telephone-service calls and require zero hosting-specific changes; they report
  actual scope/results without upgrading synthetic evidence into live qualification. The added
  Terraform delivery is a separately protected manual operation, never part of default startup
  or credential-free local/native CI; definitions/tests do not certify actual provisioning.

## Assumptions

- **Confirmed provider target**: The user confirmed GPT-Live hosted on Azure AI Foundry, with
  OpenAI documentation usable for general behavior/delegation guidance. Azure/Foundry-specific
  documentation governs actual deployment, identity, availability and supported integration.
  Planning must verify the exact offered model/deployment and client-delegation contract before
  implementation; this spec records the intended target, not independently verified availability.
  Unsupported capabilities must block that integration rather than trigger a public OpenAI,
  Azure Voice Live, legacy realtime or responses-delegation substitution. Azure Voice Live is
  explicitly prohibited in this PR, not an alternative to investigate or approve. Azure-first
  hosting/identity and all existing governance constraints remain in force.
- **Existing delivery**: Feature 001 is accepted with deferred follow-ups. This feature changes
  its model integration, management navigation, documentation location and browser testing scope;
  it does not reopen every old deferred operational/release task automatically.
- **Configuration baseline**: The user confirmed that there are no published configurations
  anywhere. This feature may change configuration structures and application contracts without
  retaining backward compatibility. Published-data migration, legacy compatibility layers and
  old-configuration review/republishing are not required. This does not weaken validation of new
  publications or broaden the PR's explicit feature, provider or hosting boundaries.
- **Specified integration constraints**: GPT-Live client delegation, exclusion of responses
  delegation, secure standard WebSockets, the existing Cosmos-backed authoritative configuration
  and root-level docs are explicit user constraints. Internal framework/SDK choice, wire messages,
  and storage schema belong to planning, not this specification; no legacy migration is required.
- **Meaning of client delegation**: The trusted application backend acts as the delegating client
  of the model service. The browser remains an authenticated user interface, not the agent-policy
  authority or holder of provider credentials. During planning the user explicitly approved a
  separate Foundry reasoning/text deployment for this shared backend. GPT-Live is voice-only;
  standalone text chat calls the same configured Agents backend directly, without microphone
  access, a speech session, provider Responses delegation or a second agent-policy definition.
- **Data ownership**: Cosmos remains authoritative through the existing management-owned boundary;
  backend consumption uses authorized service contracts rather than cross-service private-container
  access. One organization and one global Welcome/Routing/Goodbye set remain the release boundary.
- **Graph semantics**: Connections are directed permissions, not mere decoration. Community edges
  govern Routing-mediated transfers; internal agent edges govern handovers without reducing
  same-community discovery. Both levels use editable graphs with equivalent forms/keyboard
  controls. Dragging changes presentation only; displaying or moving a box/edge never grants
  capability execution permission or an implicit new handover.
- **Confirmed test modes**: The user requires both text chat and microphone/headset testing
  on the management test surface, using the same published configuration and policy. Text chat
  works without microphone permission. Concurrent multimodal mixing, images/video, test histories,
  evaluations, recordings and an expanded playground are not included.
- **Device scope**: Testing uses the manager's available browser/operating-system microphone and
  output-device selection, including a headset when selected. Unsupported browser device-selection
  features fail clearly rather than promising universal hardware control. Required audio coverage
  is desktop Edge and Chrome on Windows. Linux physical qualification is owner-deferred to a
  follow-up, not a successful test. Responsive layout remains tested at
  360, 768 and 1440 pixel widths, but does not certify mobile-browser audio support; Firefox,
  Safari and mobile audio are not additional acceptance targets for this feature.
- **Publication**: Test sessions execute published configuration only, not unsaved drafts or an
  implicit agent/community override from the currently viewed page. Explicit stop/restart selects
  a new revision; permission revocations remain immediate security checks.
- **Performance baseline**: Five testers for ten minutes and the response/cleanup bounds above
  are initial verification targets, not claims about production fleet capacity. Planning must
  qualify supported environments and constraints without silently relaxing them.
- **Confirmed test limits**: One active test session per manager spans both modalities and all
  tabs/devices. The backend enforces a ten-minute lifetime and a two-minute user-inactivity
  deadline; no accepted text or detected user speech is inactivity, even if silence, keepalives
  or agent output continue. Expiry stops the test rather than reconnecting or replacing it.
- **Scope exclusions**: Telephone-provider integration, actual phone routing/PSTN qualification,
  telephony-provider research/selection and telephone-call testing are deferred entirely.
  Kubernetes/AKS and other runtime-specific hosting, infrastructure provisioning/deployment,
  cluster enforcement/RBAC, pod/node retirement and autoscaling work are outside this PR.
  Azure Voice Live is not a permitted dependency or fallback. Image publication, legal/privacy
  certification and blanket completion of old deferred tasks remain separate. Application-level
  authorization, bounded sessions and browser audio/text behavior remain in scope without
  introducing hosting features. Authoring this spec authorizes no live access or charges.
- **Documentation migration**: The requested root docs change applies to maintained repository
  documentation and its consumers. Feature specs remain under specs; generated assets and
  implementation sources are not relocated as documentation.
- **Preservation**: Existing user changes, the constitution, runtime version pins, prior spec and
  plan remain untouched by this specification command.
