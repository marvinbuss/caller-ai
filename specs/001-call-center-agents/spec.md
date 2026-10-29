# Feature Specification: Call Center Agent Automation

**Feature Branch**: `main` (existing branch; no branch-creation hook registered)

**Created**: 2026-10-21

**Status**: Draft

**Input**: User description (summarized): A scalable call-handling backend manages speech-based
multi-agent conversations, sessions, and handovers. A separate responsive management application
configures agent names, prompts, relationships, tools, and MCP servers, and persists configuration
for dynamic runtime loading. Ordinary agents belong to isolated communities. A shared Welcome
agent, Routing agent, and Goodbye agent are always present. Additional configuration and
management testing capabilities will be defined in future specifications.

## Clarifications

### Session 2026-10-22

- Q: What must be working when the initial repository setup is considered complete? -> A: Both
  apps start locally, expose basic health checks, and pass starter tests and CI; live calls and
  agent-management workflows come later.
- Q: Must local startup and starter tests work without Azure resources or external-service
  credentials? -> A: Yes; both apps and starter tests work without cloud resources or credentials
  after dependencies are installed.
- Q: Which operating systems must support the documented local setup and starter-test
  workflow? -> A: Windows and Linux.
- Q: Should the first release serve one organization or multiple independently managed customer
  organizations? -> A: One organization; authorized managers manage its communities and agents.
- Q: Should the first release let agents access private customer information or make changes on
  a caller's behalf? -> A: Allow public-information, read-only tools only; defer private customer
  data and business-changing actions.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Complete an Agent-Assisted Call (Priority: P1)

A caller reaches the call center, receives an introduction, speaks with the appropriate agent,
and receives a clear closing without losing the conversation during agent changes.

**Why this priority**: Completing an inbound call is the primary business outcome.

**Independent Test**: Use a preconfigured community and the three required shared agents to
complete a scripted call; no management edits are required during the test.

**Acceptance Scenarios**:

1. **Given** a valid active configuration, **When** an inbound call is accepted, **Then** exactly
   one session is created and Welcome speaks before any other agent.
2. **Given** Welcome has introduced the service, **When** the caller describes a supported need,
   **Then** Routing selects an eligible community and its designated entry agent continues the
   same session with the permitted conversation context.
3. **Given** an agent is speaking, **When** the caller interrupts, **Then** the current speech
   stops and the caller's new input becomes the next conversational turn.
4. **Given** the request is finished or the caller asks to end, **When** normal closure starts,
   **Then** Goodbye delivers the closing and the session and phone connection end exactly once.
5. **Given** an active call, **When** the caller disconnects or duplicate disconnect notifications
   arrive, **Then** the session ends once and no further speech or new tool actions are started.

---

### User Story 2 - Configure the Agent Organization (Priority: P1)

An authorized configuration manager uses a separate responsive application to manage communities,
agents, prompts, relationships, and the tools and MCP server connections available to each agent.
The first release serves one organization; authorized managers work with that organization's
shared configuration rather than selecting or administering independent customer organizations.

**Why this priority**: Operators must change call behavior without changing application code.

**Independent Test**: Create a two-community configuration, reload the management application,
and confirm that its validated configuration remains available without placing a phone call.

**Acceptance Scenarios**:

1. **Given** an authorized manager, **When** they create communities and agents, edit names and
   prompts, choose community entry agents, and save, **Then** the complete valid configuration
   is retained and displayed after closing and reopening the application.
2. **Given** an existing agent, **When** the manager changes relationships, assigned tools, or
   MCP server connections, **Then** the saved configuration shows those assignments and their
   permissions without exposing credentials.
3. **Given** a configuration edit, **When** it removes a required shared role, references a missing
   agent or tool, creates an invalid community entry, or bypasses Routing, **Then** saving is
   rejected with an actionable explanation and the previous valid revision remains active.
4. **Given** two managers editing the same revision, **When** one saves before the other,
   **Then** the second receives a conflict and cannot silently overwrite the first change.
5. **Given** a phone, tablet, or desktop viewport, **When** a manager completes the primary edit
   and save workflow using touch or keyboard, **Then** all fields and actions remain reachable
   without overlapping controls or page-level horizontal scrolling.
6. **Given** an unauthenticated person or a person without configuration permission, **When**
   they attempt to read or change configuration, **Then** access is denied and nothing changes.
7. **Given** two authorized managers and two communities, **When** either manager opens or edits
  the configuration, **Then** both work within the same organization's configuration and can
  manage both communities; no customer-organization selection or creation workflow is offered.

---

### User Story 3 - Enforce Community Boundaries During Handover (Priority: P1)

A caller can receive help from different communities while ordinary agents remain unable to
discover or directly contact agents outside their own community.

**Why this priority**: Community isolation and controlled routing are core organizational and
security requirements, not optional prompt conventions.

**Independent Test**: Use two preconfigured communities and attempt permitted, prohibited, and
failed handovers while inspecting the agent-visible information and session ownership.

**Acceptance Scenarios**:

1. **Given** two enabled ordinary agents in one community, **When** either inspects its available
   peers, **Then** both are discoverable, but no ordinary agent in another community is exposed.
2. **Given** a caller needs another community, **When** the active agent requests a transfer,
   **Then** Routing selects the destination community and its entry agent; an ordinary agent
   cannot directly invoke a foreign agent even if it guesses that agent's identifier.
3. **Given** a permitted cross-community transfer, **When** the destination accepts ownership,
   **Then** it receives only the approved transfer context and the source stops controlling
   the call before the destination speaks or initiates an action.
4. **Given** a handover cannot complete, **When** its deadline expires, **Then** the source retains
   ownership and explains the failure; if it cannot continue, the call follows controlled closure.
5. **Given** a transfer loop or an unavailable destination, **When** further routing would exceed
   the configured limit, **Then** the caller receives a clarification request or controlled closing,
   rather than repeated silent transfers.

---

### User Story 4 - Apply Configuration Without Disrupting Calls (Priority: P2)

A manager saves a valid configuration change and new callers receive the change without a
redeployment, while existing callers finish under a consistent configuration.

**Why this priority**: Safe runtime updates enable operational changes without interrupted calls.

**Independent Test**: Start a call, save a changed greeting or relationship, and place another
call; compare the recorded configuration revisions and observed behavior of both sessions.

**Acceptance Scenarios**:

1. **Given** a call using revision A, **When** a manager saves valid revision B, **Then** subsequent
   calls use B within 60 seconds and the existing call keeps A for ordinary configuration changes.
2. **Given** an invalid edit or unavailable configuration storage, **When** a save is attempted,
   **Then** no partial revision becomes active and existing calls continue using their revision.
3. **Given** temporary configuration unavailability, **When** a new call arrives, **Then** it uses
   the last complete validated configuration if available; otherwise the call is declined clearly.
4. **Given** permission to a tool or server has been revoked, **When** any active session next
   attempts that action, **Then** it is denied even if the session uses an older configuration.

---

### User Story 5 - Use Only Authorized Agent Capabilities (Priority: P1)

A caller receives assistance through an agent's approved public-information, read-only tools
without gaining access to private customer information, other agents' capabilities, credentials,
or unrelated callers' information. Agents do not make business changes on the caller's behalf
in the first release.

**Why this priority**: Model-generated actions must not bypass access controls.

**Independent Test**: Preconfigure an allowed public-information tool and denied private-data
and business-changing tools, including MCP-provided capabilities, and exercise allowed, denied,
failed, and repeated read-only requests.

**Acceptance Scenarios**:

1. **Given** an agent assigned an available public-information, read-only tool and permission for
  its action, **When** it requests that action with valid inputs, **Then** the permitted public
  result is used in that caller's conversation.
2. **Given** an unassigned tool, invalid input, revoked permission, or caller instructions to bypass
   a boundary, **When** the action is requested, **Then** it is rejected before execution without
   exposing credentials, foreign agent configuration, or another session's information.
3. **Given** an unavailable tool or server, **When** its deadline expires, **Then** the caller receives
   a failure explanation or approved alternative and the agent does not claim the action succeeded.
4. **Given** a read-only request with an uncertain outcome, **When** the request is retried,
  **Then** retries remain within the declared deadline, no business data is changed, and
  uncertainty is reported honestly.
5. **Given** a request to retrieve private customer information or change business data, **When**
  an agent attempts the action, even if assigned the capability, **Then** execution is denied
  and the caller is told that the action is not supported in this release.

---

### User Story 6 - Continue Serving Calls During Scale-Out (Priority: P1)

Call-center operations can add call-handling capacity independently of management activity
without duplicate sessions, overlapping agents, or dropped calls from planned capacity changes.

**Why this priority**: The service must support increasing concurrent call volumes reliably.

**Independent Test**: Run controlled call traffic at the baseline, add capacity and increase
demand, then remove capacity gracefully and introduce one unexpected processing failure.

**Acceptance Scenarios**:

1. **Given** 100 simultaneous calls, **When** capacity is added and demand reaches 200,
   **Then** new calls meet the declared service targets and existing calls retain ownership.
2. **Given** a planned capacity reduction, **When** a processing instance is retired, **Then** it
   stops accepting new calls and active calls finish or transfer without an interrupted session.
3. **Given** a processing failure, **When** ownership recovery is attempted, **Then** the session
   recovers or reaches a terminal outcome within 10 seconds, with no competing active owner.
4. **Given** demand exceeds supported capacity or an external service limit, **When** a call
   cannot be admitted, **Then** it is declined with an unavailable outcome within 10 seconds
   rather than waiting indefinitely or being represented as an active call.

### Edge Cases

- An empty installation has no usable business community: the three shared agents are present,
  but call admission remains unavailable until a valid destination community is configured.
- A caller is silent, has an unsupported request, or requests an unknown community: Routing asks
  for clarification once, then invokes Goodbye if no eligible destination can be established.
- A community entry agent is disabled, moved, or deleted while referenced: the save is rejected
  until all affected references are resolved in the same change.
- An ordinary agent guesses a foreign identifier, or a model invents a transfer or tool request:
  authorization rejects it independently of the prompt.
- Two handovers compete, a notification is duplicated, or the caller hangs up during transfer:
  there is one committed owner or one terminal session, never two active speakers.
- A new configuration is saved while a call is in progress or capacity is added: revision pinning
  applies to that call and its handovers, except for immediate security revocations.
- A caller or model requests private customer data or an external record change: execution is
  denied even if the capability is assigned or advertised by an MCP server. A timed-out permitted
  read-only request remains uncertain and is retried only within the declared deadline.
- Speech service loss, storage unavailability, capacity exhaustion, and failed ownership recovery
  produce the bounded failure outcomes specified below, without exposing sensitive diagnostics.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The call-handling application MUST accept inbound phone calls and support two-way
  model-backed speech, including caller interruption of agent speech, from introduction to closure.
- **FR-002**: Each accepted call MUST have one session identity, lifecycle state, configuration
  revision, and active agent owner. Repeated events MUST NOT create duplicate sessions, closures,
  or business side effects. An ended call MUST NOT start further speech or new tool actions.
- **FR-003**: Every usable configuration MUST contain exactly one protected Welcome role, one
  protected Routing role, and one protected Goodbye role. They MUST be created on initial setup
  and MUST NOT be deleted, disabled, reassigned to ordinary roles, or made unreachable.
- **FR-004**: Welcome MUST introduce every normally accepted call before delegating to Routing.
  Routing MUST select the initial community. Goodbye MUST close every normal conversational
  ending and end the connection within five seconds after its closing speech finishes.
  Caller disconnects and loss of the speech channel are explicitly exempt from a spoken goodbye.
- **FR-005**: Each ordinary agent MUST belong to exactly one community. Every enabled ordinary
  agent MUST be able to discover all enabled ordinary peers in that community. Each enabled
  community MUST designate one enabled member as its entry agent.
- **FR-006**: Ordinary agents MUST NOT discover foreign ordinary agents, inspect their prompts
  or tools, or hand over directly to them. These restrictions MUST be enforced independently
  of agent instructions and model output. Community membership is not permission to use peer tools.
- **FR-007**: The three shared roles MUST remain reachable as lifecycle roles outside ordinary
  communities. Only Routing MAY select a different community. Its cross-community view MUST
  contain only community names, routing descriptions, and eligible entry references, not foreign
  prompts or tool configuration. Welcome and Goodbye MUST NOT expose a foreign agent directory.
- **FR-008**: Relationships MUST identify permitted handover paths and permitted transfer-context
  fields. Same-community discovery MUST NOT be restricted by those paths. Cross-community
  relationships MUST go through Routing and then the destination community's entry agent.
- **FR-009**: Handovers MUST preserve the call identity and approved task context. The receiving
  agent MUST receive only permitted fields: by default the caller's current request summary and
  transfer reason, not raw transcripts, prompts, credentials, or unrelated tool results.
- **FR-010**: A handover MUST commit one ownership change before the receiver speaks or starts
  an action. A failed handover MUST leave the source in control; if the source cannot continue,
  the system MUST perform controlled closure rather than leave an ownerless active call.
- **FR-011**: The system MUST enforce a configurable handover-rate limit, defaulting to ten
  completed handovers per call in any 60 seconds. At the limit it MUST stop further transfers,
  ask for clarification once, and use Goodbye if continuing would require another blocked transfer.
- **FR-012**: Speech interruptions MUST stop current agent speech within one second. A handover,
  external action, or speech-service failure MUST resolve or enter a declared failure outcome
  within ten seconds. Recovery from a lost processing owner MUST finish or mark the session
  terminal within ten seconds; a reachable caller MUST receive an explanation before closure.
- **FR-013**: A separate management application MUST let authorized managers create, read, edit,
  and remove unreferenced ordinary agents and communities; configure names, prompts, enabled
  state, community membership and entry points; and manage handover relationships.
- **FR-014**: Management MUST support a catalog of tool definitions and MCP server connections,
  including names, descriptions, available capabilities, enabled state, credential references,
  and per-agent assignments and permitted actions. Required shared roles MUST be configurable
  subject to their protected responsibilities and visibility restrictions.
- **FR-015**: Configuration MUST be durably retained, survive management and call-handler
  restarts, and load into call handling without application redeployment. A successful save MUST
  create one complete validated revision; partial or rejected edits MUST NOT become active.
- **FR-016**: Each enabled agent MUST have a nonempty name and prompt, agent names MUST be unique
  within the configuration, and all relationships, memberships, entry points, tool assignments,
  and server references MUST resolve to eligible definitions. Invalid edits MUST identify the
  affected fields or references. Deletes MUST be rejected while unresolved references remain.
- **FR-017**: Saves based on an outdated revision MUST be rejected with a visible conflict and
  preserve the manager's unsaved edits for review; silent last-writer overwrites are prohibited.
- **FR-018**: New calls MUST use the latest successfully saved revision within 60 seconds.
  Existing calls MUST retain their selected revision across handovers. Temporary configuration
  unavailability MUST use a last complete validated revision or decline new calls if none exists.
- **FR-019**: Tool and server permission revocations MUST override pinned revisions before any
  subsequent affected action. Ordinary edits to prompts, membership, or relationships MUST NOT
  silently alter an active call's revision.
- **FR-020**: Management reads and writes MUST require authenticated configuration permission.
  Callers and model-generated requests MUST NOT grant themselves management access or change
  configuration. Unauthorized access MUST be denied without revealing configuration content.
- **FR-021**: Each external action MUST be checked against the active agent's assignments,
  current permissions, allowed destinations, and input restrictions before execution. A server
  connection alone MUST NOT grant access to every capability advertised by that server.
- **FR-022**: Credential-bearing configuration MUST use approved secret references; literal
  credentials are prohibited. A versioned detection policy MUST reject recognized credential
  formats in ordinary text fields before persistence. Errors MUST identify affected fields
  without reflecting submitted values. Credential values MUST NOT be stored in agent prompts,
  shown by management reads, or exposed to models or callers. The policy MUST define accepted
  reference syntax, recognized formats, and positive, negative, and false-positive fixtures.
- **FR-023**: Call context and tool results MUST remain isolated between sessions. Cross-community
  context transfer MUST follow FR-009. This feature MUST NOT retain call recordings or complete
  transcripts after a call; temporary conversation content MUST be discarded within five minutes
  of termination. Any future retained content requires a separately approved privacy policy.
- **FR-024**: Configuration changes, denied access, handovers, permission failures, and session
  outcomes MUST produce reviewable audit records containing actor or session reference, time,
  revision, action, and outcome as applicable. Records MUST NOT contain raw call content, secret
  values, full prompts, or unrestricted tool results, and MUST follow an approved retention policy.
- **FR-025**: Primary management workflows MUST work at viewport widths of 360, 768, and 1440
  pixels without page-level horizontal scrolling. Fields MUST have accessible names, all edit
  and save actions MUST be keyboard operable, and validation errors MUST identify their fields.
- **FR-026**: Call-handling capacity MUST be expandable independently of management usage.
  Adding capacity MUST NOT require active callers to reconnect. Planned capacity removal MUST
  stop new assignments and preserve active calls until completion or successful ownership transfer.
- **FR-027**: Admission MUST respect available capacity and dependency limits. Calls that cannot
  be served MUST receive an unavailable outcome within ten seconds. Automatic retries MUST be
  bounded by that deadline, and uncertain actions MUST NOT be repeated without duplicate protection.
- **FR-028**: Operations MUST have privacy-preserving measures of active calls, admission failures,
  response delay, handover failures, dependency limits, and capacity saturation, plus a way to
  correlate a call's lifecycle events without exposing its conversation. Both services MUST
  emit application-owned, metadata-only distributed traces with cross-service correlation.
  Tests MUST cover context propagation, safe span attributes, and exclusion of credentials,
  prompts, audio, transcripts, and tool payloads. OpenAI SDK and realtime tracing MUST be
  disabled by default and MAY be enabled through explicit application configuration only while
  retaining the same content-exclusion and approved-destination requirements.
- **FR-029**: Manager-facing configuration guidance and operator runbooks MUST document the three
  protected roles, community visibility, relationships, tool permissions, revision activation,
  failure outcomes, capacity limits, and recovery procedures as part of this feature's delivery.
- **FR-030**: Initial repository setup MUST deliver locally runnable foundations for the separate
  call-handling and management applications. Each application MUST expose a basic health check.
  Setup MUST include automated starter tests and continuous integration (CI) that runs the tests
  and reports failures. Working live calls and agent-management workflows are not prerequisites
  for accepting this setup milestone; they remain required for subsequent feature delivery.
- **FR-031**: After dependencies are installed, local startup of both application foundations,
  their basic health checks, and starter tests MUST work without provisioned Azure resources
  or external-service credentials. Setup verification MUST use local stand-ins for required
  external dependencies and MUST NOT invoke live phone, speech-model, or business-tool services.
  Live integration verification MUST be separate from the default startup and starter-test checks.
- **FR-032**: The documented local setup and starter-test workflow MUST support Windows and
  Linux. Instructions MUST describe the prerequisites and commands needed on each platform,
  and both platforms MUST support starting both applications, checking their health, and running
  the same starter-test assertions without cloud resources or external-service credentials.
- **FR-033**: The first release MUST serve one organization with one shared configuration scope.
  Authorized configuration managers MUST be able to manage its communities and agents, subject
  to the protected-role rules. Communities MUST NOT act as customer-organization boundaries.
  Separate customer organizations and organization-selection or creation workflows are outside
  first-release scope.
- **FR-034**: The first release MUST allow agents to invoke only approved public-information,
  read-only tools, including capabilities supplied through MCP servers. Requests to retrieve
  private customer information or perform business-changing actions MUST be denied before
  execution, regardless of agent assignments, model instructions, or caller requests. The caller
  MUST receive an explanation that the requested action is unsupported. Caller identity
  verification and confirmation workflows for protected actions are deferred to a future feature.

### Key Entities *(include if feature involves data)*

- **Agent**: A stable identity, unique name, instructions, enabled state, role, assigned capabilities,
  and one community membership for an ordinary agent. Protected shared roles are not members.
- **Community**: A named group with a routing description, enabled state, member agents, and a
  designated entry agent; it defines ordinary-agent discovery boundaries.
- **Relationship**: An allowed source-to-target handover path and permitted context fields,
  constrained by community boundaries and protected routing responsibilities.
- **Tool Definition**: A named external capability, description, allowed actions and inputs,
  enabled state, and optional originating server connection.
- **MCP Server Connection**: An approved server reference, capability catalog, enabled state,
  and protected credential reference; access is granted through explicit agent assignments.
- **Configuration Revision**: One complete validated set of agents, communities, relationships,
  and capability assignments, with a revision identity, author, and save time.
- **Call Session**: A call identity, lifecycle state, pinned revision, active owner and community,
  temporary conversation context, and termination outcome.
- **Handover**: A source, destination, permitted context, reason, request time, and committed,
  failed, or canceled outcome associated with one session.
- **Audit Record**: A privacy-preserving account of a configuration, security, or call-lifecycle
  event, sufficient to identify its actor or session, revision, time, action, and outcome.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: At least 99% of 200 scripted eligible call journeys reach the intended community
  and a single normal closing, with Welcome first and Goodbye last in every normally ended call.
- **SC-002**: At 100 simultaneous calls sustained for 30 minutes, at least 99% of eligible call
  attempts are admitted, 95% of admitted callers hear Welcome within three seconds of connection,
  and 95% of ordinary speech turns begin a relevant spoken response within two seconds after
  the caller finishes speaking, excluding turns waiting on an external business action.
- **SC-003**: After capacity is added, supported simultaneous calls increase from 100 to 200
  within five minutes, sustain SC-002's service levels for another 30 minutes, and no existing
  call is disconnected because capacity was added or gracefully removed.
- **SC-004**: At least 95% of successful handovers resume relevant speech within two seconds
  of the transfer decision, and all tested handovers preserve one session and one active owner.
- **SC-005**: In all tested unauthorized discovery, direct cross-community transfer, forged
  capability, and cross-session data-access attempts, zero prohibited disclosures or actions occur.
- **SC-006**: Every tested attempt to delete, disable, duplicate, or bypass a protected role,
  save an invalid reference, or overwrite a conflicting revision is rejected without changing
  the active configuration.
- **SC-007**: Across 20 valid configuration changes, all new calls reflect each change within
  60 seconds, all existing calls retain their prior revision, and every tested security revocation
  blocks the next affected action regardless of revision.
- **SC-008**: Deferred: participant-based manager usability assessment is not part of current
  release acceptance. No manager recruitment, completion-time study, or participant-success
  threshold is required now. Automated management functionality and accessibility checks remain
  required by the applicable functional requirements and SC-009.
- **SC-009**: The create, edit, assignment, and save tasks succeed at all three specified viewport
  widths and using keyboard-only navigation, with zero obscured actions or overlapping fields.
- **SC-010**: In all tested provider outages, unavailable tools, exhausted capacity, routing loops,
  and owner failures, the declared failure or recovery outcome occurs within ten seconds; retries
  produce zero duplicate business side effects and zero competing session owners.
- **SC-011**: All tested caller interruptions stop current agent speech within one second;
  normal connections close within five seconds after Goodbye finishes; duplicate disconnects
  cause no subsequent speech or newly initiated tool actions.
- **SC-012**: After management and call handling are restarted, all previously saved configuration
  remains available; rejected or partially saved edits are used by zero calls.
- **SC-013**: All terminated sessions discard temporary conversation content within five minutes,
  and a review of configuration, audit records, and operational diagnostics finds zero raw secret
  values, retained recordings, complete transcripts, or cross-session conversation content.
- **SC-014**: An operator using the supplied runbook can identify the affected call outcome and
  configuration revision in each of ten seeded failure cases within five minutes, without access
  to raw conversation content or assistance from the implementation team.
- **SC-015**: For initial repository setup, a contributor following the setup guide from a fresh
  checkout can start both applications locally, verify two successful application health checks,
  and run all starter tests successfully. CI MUST report a passing result for that revision and
  a failing result when a starter-test assertion is deliberately broken. These checks MUST NOT
  require completing a live phone call or using an agent-management workflow.
- **SC-016**: With no Azure resources provisioned and no external-service credentials configured,
  a contributor can start both application foundations, obtain two successful health checks,
  and pass all starter tests after installing dependencies. The same starter tests MUST pass
  in CI without cloud credentials, and the verification MUST make zero requests to live phone,
  speech-model, or business-tool services.
- **SC-017**: Setup verification evidence MUST show that a fresh checkout on each of Windows
  and Linux can start both application foundations, obtain two successful health checks, and
  pass all starter tests using the documented workflow without cloud resources or credentials
  after dependencies are installed. Success on only one operating system is insufficient.
- **SC-018**: With two authorized configuration managers and two communities, both managers can
  view and edit the same organization's community and agent configuration. Verification MUST
  show one shared configuration scope, one set of the three protected roles, and no workflow
  for selecting or creating independent customer organizations.
- **SC-019**: In scripted direct-tool and MCP-provided capability tests, all permitted public,
  read-only requests with valid inputs and available dependencies return the expected public
  information. Every attempt to access private customer data or change business data is denied
  before execution, including attempts using assigned capabilities, with zero private customer
  data disclosures and zero business-data changes.

## Assumptions

- **Acceptance stages**: Local acceptance means deterministic tests pass without cloud resources.
  Live qualification remains pending until an authorized environment is provisioned or validated
  as pre-provisioned, including identity, networking, provider access, and quotas. A local story
  checkpoint MUST NOT claim live qualification or wait on cloud evidence assigned to a later gate.
- **Manager usability assessment**: The participant-based study formerly described by SC-008 is
  deferred. This does not defer automated editor, authorization, responsive-layout, or accessibility
  tests, and does not introduce an interactive agent-testing product.
- **Initial repository setup milestone**: The first deliverable is runnable foundations for both
  applications, basic health checks, starter tests, and CI, verified by FR-030 and SC-015.
  The complete call flows, configuration workflows, and call-capacity targets remain requirements
  for subsequent feature delivery rather than setup acceptance. Constitution-wide security,
  documentation, logging, and testing requirements still apply to the delivered foundations.
- **Cloud-independent setup verification**: Local application startup, health checks, and starter
  tests use local stand-ins and require no Azure resources or external-service credentials after
  dependencies are installed. Dependency installation may require internet access. Live integration
  checks are separate; their failure or lack of credentials MUST NOT block these setup checks.
- **Developer platforms**: Windows and Linux are required for local setup and starter-test
  verification. macOS support is not part of initial setup acceptance. The supported versions
  and platform-specific commands will be documented during implementation planning.
- **Scope**: This feature establishes inbound call handling and a separate configuration management
  application for one organization. Independently managed customer organizations, outbound
  campaigns, human call-center staffing and transfer workflows, billing, production business-tool
  implementations, advanced analytics, and additional configuration dimensions are outside this
  feature. Authorized configuration managers manage the organization's shared configuration;
  communities are agent-discovery boundaries, not separate customer organizations.
- **First-release caller tools**: Only public-information, read-only tool capabilities are
  supported. Access to private customer information, business-changing actions, and the caller
  identity-verification and action-confirmation journeys needed for those capabilities are
  deferred. These limits apply to direct tools and MCP-provided capabilities alike, not to
  authorized managers editing the application's agent configuration.
- **Future management testing**: Interactive agent testing within management is an intended future
  capability. Its modes, test data, execution controls, and result presentation will be specified
  separately; this feature does not require a playground, simulation, or evaluation interface.
  The acceptance tests above are delivery verification, not that deferred product capability.
- **Agent meaning**: Agents in this feature are automated conversational roles. A community is
  an agent-discovery boundary, not by itself a customer or tenant isolation model.
- **Shared roles**: There is one configuration-wide Welcome, Routing, and Goodbye role, not one
  copy per community. Their display names and prompts are editable, but their responsibilities,
  mandatory presence, and visibility limits are not. Welcome handles introduction, Routing owns
  community selection, and Goodbye closes the call; ordinary agents own business tool actions.
- **Relationship defaults**: Ordinary peers are discoverable within a community; configured paths
  govern who may receive a handover. Configuration must include the required lifecycle paths
  from Welcome to Routing, Routing to eligible entries, and active agents to Goodbye.
- **Activation**: A successful validated save activates a revision for new calls; a separate draft,
  approval, scheduling, or rollback user workflow is not included. Active calls use revision pinning
  except for security revocations, which take effect before the next affected action.
- **Initial service targets**: The 100-call baseline, 200-call scale-out target, response deadlines,
  and ten-handovers-per-minute default are explicit planning assumptions, not user-supplied demand
  forecasts. Acceptance uses scripted voice traffic and representative permitted tool actions;
  service quotas and test access must support that traffic. Higher production demand requires
  revising these targets before implementation planning is approved.
- **External dependencies**: Live call handling requires phone connectivity and OpenAI
  realtime/speech model access; initial setup verification does not. Managers supply approved
  tool and MCP server definitions and necessary access for live integrations.
  Those services must permit the chosen call volume; failures follow the specified bounded outcomes.
- **Privacy**: Persistent recordings and complete transcripts are excluded by default. Applicable
  disclosure, data residency, credential protection, and audit-retention rules require documented
  approval before production use; this spec does not assume a jurisdiction or certify compliance.
- **Governance**: Platform, backend language, service ownership, documentation, and release controls
  remain governed by the [project constitution](../../.specify/memory/constitution.md). This spec
  states required behavior without selecting implementation frameworks or storage technology.
