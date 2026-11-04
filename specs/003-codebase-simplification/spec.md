# Feature Specification: Codebase Simplification and Consistency Cleanup

**Feature Branch**: `003-codebase-simplification`

**Created**: 2026-11-03

**Status**: Draft

**Input**: User description: "This spec is only about cleanup consistency and documentation. (1) Simplify the code structure so it is more readable and easier for humans to understand. (2) Add comments where possible so the code base is easier to read. (3) Use official libraries where it makes sense and simplifies the code, including official Microsoft libraries for Azure and the OpenAI SDK. (4) Simplify backend endpoints: one endpoint for text interaction and one for voice interaction; remove the call integration endpoint for now, keeping direct voice interaction via WebSockets to the backend from the management app or a future client app; no different endpoints for the same purpose unless absolutely crucial. (5) The management app's testing feature should directly use the same backend web API endpoints as other apps, built so specific settings on that connection can be added later. (6) Create one consistent Terraform configuration for all stages (dev/test/prod) that relies only on backend files and variable files for environment-specific differences. (7) Identify how testing can be simplified so it takes less time. (8) Create hooks to interact with GitHub. Breaking changes are acceptable because the app is not used or deployed anywhere today. Testing in a real environment is deferred to a follow-up spec."

**Scope boundaries (confirmed 2026-11-03)**: This feature is cleanup only. It adds no new end-user
capability, with one deliberate exception confirmed on 2026-11-03: text conversation history is
stored server-side per user (FR-028 to FR-031). It reduces and clarifies the existing code, interfaces, infrastructure definitions,
tests, documentation and contributor workflow. Everything that feature 002 delivered and that is
not named here (published-configuration graphs, one shared Agents backend for text and speech,
GPT-Live client delegation, one active test per manager, content privacy and security controls, apart from the stored text history in FR-028 to FR-031)
keeps its behavior. Breaking changes to interfaces, formats and file layouts are allowed and need
no migration, compatibility layer or deprecation period. No real environment, cloud resource,
physical device or live provider is used; real-environment verification belongs to a later spec.

## Clarifications

### Session 2026-11-03

- Q: Should the management browser connect directly to the backend, or should the management server remain its gateway? -> A: Keep the management server as the browser-facing gateway; management and other trusted application servers use the same authenticated backend endpoints. Direct browser-to-backend connections are out of scope.
- Q: Should the numerical cleanup targets I introduced be mandatory acceptance gates or improvement goals? -> A: Treat the 25% code reduction, 600-line limit, 50% test-time reduction and three-minute quick tier as advisory goals. Measured improvement and preserved test coverage remain mandatory.
- Q: By "hooks to interact with GitHub," do you mean steps inside the Spec Kit workflow, automation running in GitHub Actions, or both? -> A: Spec Kit workflow hooks only, offering branch preparation, issues, commits, pushes and pull requests with confirmation before changes. GitHub Actions automation is not part of this hook requirement.
- Q: Should this cleanup add configurable connection settings now, or only prepare the extension point for future settings? -> A: Prepare the extension point only. Accept omitted or empty settings, reject unknown fields, and preserve existing defaults without adding configurable behavior in this feature.
- Q: Should historical specs, task logs and acceptance records keep obsolete endpoint references when clearly marked as historical or superseded? -> A: Preserve historical records and their recorded results, clearly mark superseded material, and apply the zero-obsolete-reference rule to active code and current operational documentation only.
- Q: Is a manual reviewer sample of ten modules still required for SC-005? -> A: No. SC-005 is satisfied by the automated purpose-statement check alone; no manual review step is part of this feature.
- Q: Should text conversation history stay volatile as in feature 002, or be stored? -> A: Store it server-side per user in Azure Cosmos DB so follow-up text requests continue from it. Text only; voice transcripts and audio stay volatile and unlogged. Each conversation expires 30 days after its last turn, and expiry is the only deletion path in this feature. This deliberately changes the feature 002 rule that conversation content is never persisted, and it is the one new capability in this otherwise cleanup-only feature.
- Q: How does the text endpoint deliver replies? -> A: One HTTP request per turn with a streamed (server-sent event) reply; the caller sends a conversation identifier, not the history.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - One Text Entry Point and One Voice Entry Point on the Backend (Priority: P1)

A developer building any client (the management app today, another client app later) finds that
the backend offers exactly two conversation entry points: one for text and one for voice over a
WebSocket. There is no call-provider integration and no second path that does the same job.

**Why this priority**: The backend interface is the contract every client depends on. A small,
unambiguous interface is the foundation for the management change, the documentation and the
test simplification.

**Independent Test**: With controlled model and storage doubles, list every externally reachable
backend interface, run one text conversation and one voice conversation through their single
endpoints, and confirm that nothing else accepts conversation traffic and that no call-provider
interface remains.

**Acceptance Scenarios**:

1. **Given** the running backend, **When** a contributor lists its externally reachable interfaces,
   **Then** the list contains exactly one text interaction endpoint, one voice interaction endpoint
   and the health/readiness probes, plus only those further interfaces whose necessity is recorded
   with a justification.
2. **Given** an authorized client, **When** it opens a text conversation on the text endpoint,
   **Then** it receives replies from the same published-configuration conversation behavior that
   feature 002 defined.
3. **Given** an authorized client, **When** it opens a voice conversation over a WebSocket on the
   voice endpoint, **Then** audio exchange, interruption, stop and cleanup behave as feature 002
   defined for speech.
4. **Given** the former call-provider callback and call-media interfaces, **When** any client
  requests them, **Then** they do not exist, and the active code, configuration, tests and current
  operational documentation that existed only for them are gone. Historical specs, task logs
  and acceptance records are preserved and clearly marked where superseded.
5. **Given** two interfaces that served the same purpose, **When** the cleanup is complete,
   **Then** only one remains; any pair kept on purpose has a recorded reason why it is crucial.
6. **Given** an authorized manager who has already exchanged text turns in a conversation, **When**
   they send a follow-up text request with that conversation's identifier, **Then** the reply
   continues from the stored history, while a request for an unknown, expired or another
   manager's conversation is refused without revealing which of the three applies.

---

### User Story 2 - Management Test Console Uses the Same Backend Endpoints (Priority: P1)

A manager starts a text or voice test in the management app. Behind the scenes the management app
keeps its server as the browser-facing gateway. That server talks to the backend through the very
same text and voice endpoints other trusted application servers use, not through a private
test-only path. The way it builds that connection has one clear place where connection-specific
settings can be added later. The browser does not connect directly to the backend.

**Why this priority**: Testing through the production interface proves that interface, removes a
duplicate path, and makes the future client app a peer of the management app.

**Independent Test**: Start a text test and a voice test from an owned authenticated browser
against controlled doubles and confirm from the recorded backend traffic that only the two public
endpoints were used. Confirm omitted and empty settings preserve existing defaults, and any
nonempty settings are rejected without changing conversation behavior or authority.

**Acceptance Scenarios**:

1. **Given** an authorized manager, **When** they start a text test, **Then** the management app
   reaches the backend on the text endpoint and no test-only endpoint is involved.
2. **Given** an authorized manager, **When** they start a voice test, **Then** the management app
   reaches the backend on the voice endpoint and no test-only endpoint is involved.
3. **Given** a connection is being opened, **When** settings are omitted or an empty settings
  structure is supplied, **Then** existing defaults apply; any nonempty settings structure is
  rejected because this feature introduces no supported setting fields.
4. **Given** a setting that tries to choose a revision, identity, capability or credential,
   **When** it is supplied by a client, **Then** it is rejected; settings never grant authority.
5. **Given** the management app relays a test, **When** the test runs, **Then** all feature 002
   guarantees still hold: published configuration only, one active test per manager, visible
   revision and mode, fail-closed authorization and bounded cleanup.

---

### User Story 3 - Easier-to-Read Code, Explaining Comments and Official Libraries (Priority: P2)

A new contributor reads any part of the code base and understands what it is for, why it is shaped
that way and what happens when it fails, without needing its original authors. Where an official
library already provides a capability, the code uses it instead of a hand-written equivalent.

**Why this priority**: Readability and less custom code lower the cost of every later change, but
they depend on the interface decisions in the first two stories.

**Independent Test**: Review a random sample of modules and confirm each states its purpose, shows
no dead or duplicated code and explains its non-obvious decisions; compare a list of capabilities
against the official Azure and OpenAI libraries and confirm every hand-written equivalent either is
gone or has a recorded reason.

**Acceptance Scenarios**:

1. **Given** a module, **When** a contributor opens it, **Then** it opens with a short statement of
   its purpose and its inputs, outputs, failure behavior and security assumptions where they apply.
2. **Given** non-obvious logic (ordering rules, deadlines, security decisions, cleanup proofs),
   **When** a contributor reads it, **Then** a nearby comment explains why it is that way; comments
   do not restate what the code visibly does or narrate change history.
3. **Given** code and abstractions that existed only for removed features or that duplicate each
   other, **When** the cleanup is complete, **Then** they are removed and the retained behavior is
   unchanged under the automated tests.
4. **Given** a capability covered by an official Azure library or the OpenAI SDK, **When** the code
   needs it, **Then** it uses that library; a hand-written protocol or transport remains only with a
   recorded reason, such as a required capability the library does not support.
5. **Given** a library change, **When** dependencies are updated, **Then** it stays within the
   existing approved-feed, lock and vulnerability-gate policy.

---

### User Story 4 - One Terraform Definition for Every Stage (Priority: P2)

An operator plans dev, test or prod from exactly the same Terraform code. The only things that
differ per stage are a backend file and a variable file.

**Why this priority**: One definition removes drift between stages and makes adding a stage a
data-only change, but nothing else in this feature depends on it.

**Independent Test**: Run the credential-free Terraform checks for three environment variable sets
(dev, test, prod) against the unchanged code and confirm all three validate; add a fourth by
creating only its two files.

**Acceptance Scenarios**:

1. **Given** the dev, test and prod stages, **When** each is planned, **Then** the same Terraform
   code is used and only the backend file and the variable file differ.
2. **Given** a new stage, **When** an operator adds it, **Then** they create its backend file and
   variable file and change no Terraform code and no workflow.
3. **Given** the Terraform code, **When** it is reviewed, **Then** it contains no logic that
   branches on a stage name; anything that must differ per stage comes from a variable value.
4. **Given** the delivery workflows, examples and runbook, **When** they are read, **Then** they
   describe this single convention and include the test stage; applying still requires the existing
   protected, reviewed manual process.

---

### User Story 5 - Faster Automated Testing Without Losing Coverage (Priority: P2)

A contributor gets feedback quickly while working and the required checks on a change finish
sooner, yet every retained requirement is still covered by a meaningful automated test.

**Why this priority**: Fast feedback is needed throughout the cleanup itself, but the largest
savings come after the removed features and duplicate paths are gone.

**Independent Test**: Record the elapsed time of the local and required checks before the cleanup,
repeat the measurement after, and compare; map every retained requirement to at least one test.

**Acceptance Scenarios**:

1. **Given** the existing suites, **When** the cleanup starts, **Then** their elapsed times are
   recorded as the baseline, and the same measurement is recorded when it ends.
2. **Given** tests for removed behavior or tests that duplicate each other's coverage, **When**
   the cleanup is complete, **Then** they are removed or merged, and expensive shared setup is
   reused instead of repeated.
3. **Given** a contributor working on one area, **When** they run the quick tier, **Then** they get
   a result for that area faster than the full tier (measured as in SC-008), and the full tier remains the required
   gate for a change.
4. **Given** any speed-up, **When** it is reviewed, **Then** no test was skipped, disabled or
   weakened merely to save time, and long-running or real-environment acceptance runs remain opt-in.

---

### User Story 6 - GitHub Hooks for the Spec Workflow (Priority: P3)

A contributor following the Spec Kit workflow is offered the matching GitHub steps at the right moments
(preparing a branch, creating issues from tasks, committing, pushing and opening a pull request)
without leaving the workflow. These are contributor-invoked Spec Kit hooks, not GitHub Actions
automation.

**Why this priority**: It saves routine effort but does not change product behavior.

**Independent Test**: Run the workflow stages with the hooks configured and confirm the expected
GitHub steps are offered, that nothing is changed on GitHub without confirmation, and that missing
GitHub access produces a clear message instead of a failure.

**Acceptance Scenarios**:

1. **Given** the hooks are configured, **When** a contributor starts a feature specification,
   **Then** preparing a feature branch is offered.
2. **Given** a completed task list, **When** the workflow finishes that stage, **Then** creating
   GitHub issues from the tasks is offered.
3. **Given** a completed implementation, **When** the workflow finishes that stage, **Then**
   committing, pushing and opening a pull request are offered.
4. **Given** any hook that would change GitHub or push code, **When** it is about to run, **Then**
   it requires explicit confirmation from the contributor; read-only hooks may run automatically.
5. **Given** GitHub access is missing or a hook fails, **When** the stage runs, **Then** the hook
   reports the reason and the local workflow continues.

### Edge Cases

- A removed endpoint is still referenced by a test, workflow, Terraform file, setup guide or the
  management app: dependencies on the removed behavior and stale operational instructions are
  removed. Regression checks may name a retired endpoint solely to verify its absence.
- Feature 002 contract documents still name the old test endpoint or its message protocol: retain
  their historical descriptions, mark them superseded and link to the current contract so there
  is one maintained operational description. Do not rewrite recorded results to imply new tests.
- An official library does not support a required capability (for example the GPT-Live
  client-delegation protocol): the hand-written code stays, with a recorded reason, and no excluded
  service or fallback is introduced to avoid it.
- A simplification would change security, privacy, limit or cleanup behavior: it is not allowed;
  only the interface and layout changes listed in this spec are intended breaking changes.
- A per-connection setting could change who a client is or what it may do: it is rejected, because
  client-provided values never become authority.
- An empty settings structure is provided: it preserves existing defaults. Any nonempty settings
  structure is rejected; configurable language, voice or other preferences are outside this feature.
- A test speed-up introduces shared state between tests and causes order-dependent failures: the
  sharing is reduced rather than the tests being skipped.
- A Terraform difference between stages seems to need conditional code: it is expressed as a
  variable value instead of a stage-name check.
- A GitHub hook has no network, no permission or no authenticated session: it reports and the
  workflow continues without partial changes.
- A follow-up text request names a conversation that has expired, never existed or belongs to
  another manager: it is refused identically and no content or existence is revealed.
- A stored conversation would exceed the context limit: the turn is refused with the existing
  context-limit outcome and the stored history is left unchanged.
- Operational interfaces from earlier work, such as rollout draining or a development transport
  probe, may or may not still be needed: each is either removed or kept with a recorded justification.

## Requirements *(mandatory)*

### Functional Requirements

**Backend interfaces**

- **FR-001**: The backend MUST offer exactly one endpoint for text conversations and exactly one
  endpoint for voice conversations. Voice MUST use a secure WebSocket. Both MUST serve the same
  published-configuration conversation behavior that feature 002 defined.
- **FR-002**: The call-provider integration interfaces (provider callbacks and call media) MUST be
  removed, together with the active code, configuration, tests and current operational
  documentation that exist only for them. Historical specs, task logs and acceptance records
  MUST be preserved and clearly marked where superseded. Voice reaches the backend only through
  the voice endpoint, from the management app or a future client app.
- **FR-003**: The backend MUST NOT expose more than one endpoint for the same purpose. Beyond the
  text endpoint, the voice endpoint and the health/readiness probes, any interface that remains
  MUST have a recorded justification that it is crucial and does not overlap another.
- **FR-004**: The text and voice endpoints MUST share one documented connection contract for
  authentication, admission, limits, errors and closing; they MAY differ only where the modality
  requires it.

**Management and backend interaction**

- **FR-005**: The management test console MUST connect to the backend through the same text and
  voice endpoints other trusted application servers use. The test-only internal endpoint and any
  protocol used only by it MUST be removed.
- **FR-006**: The management server MUST remain the browser-facing gateway and enforce browser
  sign-in, session and permission checks. Only trusted application servers may call the backend
  conversation endpoints directly; the backend MUST independently authenticate and authorize each
  connection using verified caller and user context. The browser MUST NOT connect directly to the
  backend or receive provider or backend workload credentials.
- **FR-007**: The connection MUST accept one optional, validated settings structure at start.
  This feature MUST accept omitted or empty settings only, preserving existing defaults. It MUST
  reject any nonempty settings structure, not ignore unknown fields. No configurable behavior or
  supported setting fields are introduced; the structure is an extension point for future work.
- **FR-008**: Client-provided settings MUST NOT choose or alter revision, identity, permissions,
  capabilities or credentials; authority stays server-side.
- **FR-009**: The management app MUST build its backend connection server-side in one place so future
  connection-specific settings are added by extending that place and the backend validation only,
  without new endpoints or changes to unrelated code.

**Code structure, comments and libraries**

- **FR-010**: The code structure MUST be simplified: dead, duplicated and single-use abstractions,
  and layers that existed only for removed features, MUST be removed; each remaining module MUST
  have one clear responsibility. Behavior that is not named as an intended change MUST be preserved.
- **FR-011**: Every production module and public interface MUST state its purpose and, where
  applicable, its inputs, outputs, failure behavior and security assumptions. Non-obvious
  decisions, ordering rules, deadlines and cleanup logic MUST be explained where they occur.
  Comments MUST NOT restate what the code visibly does or describe change history.
- **FR-012**: Where an official Azure library or the OpenAI SDK provides a capability and
  simplifies the code, the code MUST use it instead of a hand-written equivalent. Custom protocol
  or transport code MAY remain only with a recorded reason. The feature 002 provider constraints
  continue to apply: GPT-Live client delegation only, no Azure Voice Live, no Responses delegation,
  no public-service or legacy-realtime fallback.
- **FR-013**: Adding or changing a library MUST follow the existing dependency policy (approved
  feeds, locked versions, vulnerability gates).
- **FR-014**: Breaking changes to endpoint names, message formats, internal interfaces,
  configuration shapes, file layouts and test names are permitted. No migration, compatibility
  layer or deprecation shim is required, and current operational documentation MUST describe the
  current behavior. Historical descriptions and recorded results MUST remain distinguishable from
  current guidance rather than being rewritten to appear current.
- **FR-015**: The cleanup MUST NOT weaken any retained security, privacy, limit, admission or
  cleanup guarantee from feature 002, except the text-history storage that FR-028 to FR-031 add and bound with their own controls.

**Terraform**

- **FR-016**: One Terraform definition per stack MUST serve dev, test, prod and any later stage.
  Stage-specific differences MUST be expressed only through a backend configuration file and a
  variable file.
- **FR-017**: Terraform code MUST NOT contain logic that depends on a stage name. Adding a stage
  MUST require only its two files and no change to Terraform code or workflows.
- **FR-018**: Delivery workflows, examples and the runbook MUST describe this single convention,
  provide nonsecret synthetic example files for dev, test and prod, and keep secrets out of files.
- **FR-019**: The existing protected, reviewed, manual apply process MUST be retained. This feature
  MUST NOT run any plan or apply against a real environment.

**Testing**

- **FR-020**: The test suites MUST be reorganized to take less time: remove tests of removed
  behavior and duplicated coverage, reuse expensive setup, run independent tests concurrently where
  safe, and provide a quick tier for local feedback alongside the full required tier.
- **FR-021**: Every retained requirement MUST keep at least one meaningful automated test. Tests
  MUST NOT be skipped, disabled or weakened merely to save time, and long-running or
  real-environment acceptance runs MUST stay outside the default checks.
- **FR-022**: The elapsed time of the local and required checks MUST be recorded before the
  cleanup starts and again when it ends.

**GitHub hooks**

- **FR-023**: The Spec Kit workflow MUST provide hooks that offer GitHub steps at defined stages:
  branch preparation when a specification starts, issue creation when tasks are complete, and
  commit, push and pull request creation when implementation is complete. The exact set MUST be
  documented for contributors. This requirement covers Spec Kit workflow hooks only; it does not
  request new GitHub Actions automation.
- **FR-024**: Any hook that changes GitHub or pushes code MUST require explicit contributor
  confirmation. Hooks MUST NOT bypass checks or authentication and MUST NOT store credentials.
- **FR-025**: A hook that cannot run MUST report why and let the workflow continue.

**Documentation and verification boundary**

- **FR-026**: Documentation MUST be updated in the same change: the two endpoints and their shared
  contract, the management-to-backend flow, setup and runbooks, the Terraform convention, the
  testing guide and the hooks, with diagrams kept consistent. Current operational guidance MUST
  not instruct use of removed call integration or endpoints. Historical specs, task logs,
  acceptance evidence and run reports MUST preserve their original descriptions and results;
  supersession labels and navigation/link repairs are permitted without recertifying old results.
  Historical references are exempt from the zero-obsolete-reference rule, and documentation
  checks MUST keep passing.
- **FR-027**: Verification in this feature MUST use controlled doubles only. No live provider,
  cloud resource, physical device or real environment is used; that verification is deferred to a
  follow-up spec.

**Text conversation history**

- **FR-028**: The backend MUST store each text conversation's history server-side in Azure Cosmos DB,
  keyed by tenant, owning manager and a server-assigned conversation identifier, so a follow-up
  text request that carries only the identifier continues from the stored history. Callers MUST
  NOT send the history. Only text conversations are stored; voice transcripts and audio remain
  volatile and unlogged as in feature 002.
- **FR-029**: A stored conversation MUST be readable and writable only by the owning manager
  identity verified from the start permit. An unknown, expired or foreign identifier MUST be
  refused identically. Stored history MUST stay within the existing context size limit, MUST be
  appended only after a completed turn so a failed turn leaves it unchanged, MUST use optimistic
  concurrency, and MUST keep the existing rule that an agent change resets context to the
  authorized transfer context.
- **FR-030**: Each stored conversation MUST expire automatically 30 days after its last turn.
  Expiry is the only deletion path in this feature; no delete operation or new endpoint is added.
  The operator procedure for retention and emergency purge MUST be documented in the runbook.
  The test console MUST show a static notice that text test conversations are stored for 30 days
  and visible only to their owner.
- **FR-031**: Stored conversation content MUST NOT appear in logs, metrics, traces, error
  responses, alerts, admission or authority records or any other store, MUST be encrypted at rest
  and in transit, and MUST be accessed only by the backend workload identity without stored keys.
  The management application MUST NOT read the store. A threat assessment of this stored caller
  content MUST be recorded in the security documentation.

### Key Entities *(include if feature involves data)*

- **Text Interaction Endpoint**: The single backend entry point for text conversations.
- **Voice Interaction Endpoint**: The single backend WebSocket entry point for voice conversations.
- **Connection Settings**: An optional, validated extension point sent when a connection starts.
  In this feature it is empty when supplied and preserves existing defaults; future behavior
  preferences require explicitly supported fields and can never carry authority.
- **Test Console Connection**: The management app's connection to the backend, built in one place
  from the user's request and the settings structure.
- **Environment Configuration**: The backend file and the variable file that together describe one
  stage; the only per-stage input to Terraform.
- **Workflow Hook**: A configured step offered at a spec-workflow stage that interacts with GitHub.
- **Baseline Measurement**: The recorded pre-cleanup elapsed times and code volume used to judge
  improvement.
- **Text Conversation History**: The stored turns of one text conversation, owned by one manager,
  bounded in size, expiring 30 days after the last turn; the only conversation content the system
  persists.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The backend exposes exactly one text interaction endpoint, one voice interaction
  endpoint and the health/readiness probes; every other interface present has a recorded
  justification; zero call-provider interfaces remain.
- **SC-002**: In the automated test-console journeys, 100% of backend traffic uses the two public
  endpoints; zero test-only endpoints exist.
- **SC-003**: Connection start accepts omitted or empty settings with unchanged default behavior,
  and 100% of nonempty settings in tests are rejected, including unknown, unsupported and
  authority-changing fields. No language, voice or other preference is configurable in this feature.
- **SC-004**: Cleanup demonstrates a measured reduction in dead or duplicated production code
  against the recorded baseline while preserving retained behavior. Total production source volume
  (excluding generated files, lock files and third-party code) and largest source-file length are
  reported before and after cleanup. A 25% source-volume reduction and a 600-line maximum file
  length are advisory goals, not mandatory acceptance gates.
- **SC-005**: 100% of production modules open with a purpose statement, checked automatically.
  No manual review sample is required.
- **SC-006**: Every capability covered by an official Azure library or the OpenAI SDK uses it, or
  has a recorded reason for the hand-written alternative; zero unrecorded hand-written equivalents.
- **SC-007**: Dev, test and prod validate from the same unchanged Terraform code; adding a further
  stage in a test requires exactly two new files and zero code or workflow edits; zero stage-name
  conditions exist in the code.
- **SC-008**: The required automated checks demonstrate a measured elapsed-time reduction against
  the baseline under comparable conditions, with retained coverage. The quick tier returns a
  changed-area result faster than the full tier. Completing required checks in at most 50% of
  baseline time and returning a quick-tier result within three minutes are advisory goals, not
  mandatory acceptance gates.
- **SC-009**: 100% of retained requirements map to at least one meaningful automated test; zero
  tests are skipped or weakened to achieve the speed-up.
- **SC-010**: Every defined workflow stage offers its GitHub step, and zero GitHub-changing or
  pushing actions run without explicit confirmation.
- **SC-011**: Zero production registrations or traffic routes use removed endpoints or call
  integration; no active code, tests, workflows, Terraform or current operational guidance depend
  on removed functionality. Historical specs, task logs, acceptance evidence and run reports may
  retain obsolete references when clearly historical or superseded, with recorded results intact.
  Retirement regressions may name removed endpoints to verify their absence. Documentation
  checks pass and current guidance identifies the supported interfaces without ambiguity.
- **SC-012**: Default verification needs zero live provider calls, cloud resources, physical
  devices or credentials.
- **SC-013**: In automated tests, 100% of follow-up text requests continue from stored history;
  every stored conversation carries its owner and a 30-day expiry; 100% of requests for another
  manager's, unknown or expired conversations are refused identically; zero conversation text
  appears in logs, metrics, traces, error bodies, alerts or any other store; and zero voice
  transcripts or audio are written to the history store.

## Assumptions

- **Audience**: The people affected are contributors and maintainers. This is a technical cleanup, so
  the endpoints, libraries and Terraform conventions named here are the owner's explicit
  constraints rather than choices left to planning; success criteria still describe outcomes.
- **Feature 002 stays closed**: Feature 002 is closed with eight tasks deferred for real-environment
  verification. This spec does not reopen them. The follow-up real-environment spec will verify the
  simplified interfaces. Preserve feature 002's original task text and evidence; mark outdated
  interface descriptions superseded and direct readers to current guidance without rewriting
  past results or claiming they verify the new interfaces.
- **No deployments exist**: Because the app is not used or deployed anywhere, breaking changes need
  no migration, no data conversion and no compatibility period.
- **Telephony is deferred, not forbidden forever**: Removing the call integration for now leaves the
  constitution unchanged; direct voice over a WebSocket is the only voice entry point in this spec.
- **Provider rules continue**: The GPT-Live client-delegation, Azure-hosted and excluded-service
  rules from feature 002 still apply, including when an official library is adopted.
- **Authentication and limits**: The existing sign-in, admission, one-active-test and limit rules are
  kept in principle. They may be unified across the two endpoints but not weakened. Management
  remains the browser-facing gateway; future client apps use their trusted application servers to
  call the same backend endpoints under the same rules. Direct browser access to those endpoints
  and a new browser-to-backend authentication flow are outside this feature.
- **Wire details belong to planning**: Message formats, the transport of the text endpoint and the
  wire representation of the empty settings structure are decided during planning. This feature
  prepares its extension point only; adding supported connection preferences is future work.
- **Terraform scope**: The existing bootstrap and platform definitions are consolidated; stage files
  use the standard Terraform backend and variable file mechanisms. Applying to any real environment
  remains a separate, protected, manual step.
- **Hook interpretation**: "Hooks to interact with GitHub" means Spec Kit workflow-stage hooks that
  use the contributor's own authenticated GitHub tooling; they are not new GitHub Actions
  workflows. The owner confirmed this scope during clarification; GitHub changes and pushes still
  require explicit contributor confirmation.
- **Baselines**: Code volume and test times are measured once at the start on the existing CI
  runners and a typical developer machine, and are compared in the same way at the end. Dead or
  duplicated production code and largest source-file length are also recorded for comparison.
  The numerical reduction, file-size and quick-tier timing goals are advisory; missing one alone
  does not fail acceptance. Measured simplification, faster checks and retained coverage are required.
- **Dependency policy**: Library changes use the existing approved package feeds, locks and
  vulnerability gates.
- **Persistent text history is a deliberate exception**: Feature 002 kept all conversation content
  volatile. The owner decided on 2026-11-03 to store text conversation history so follow-up text
  requests work across separate HTTP requests and backend instances. Feature 002's recorded
  privacy results stay as historical evidence and are not rewritten; the current guarantee is
  the one in FR-028 to FR-031. Voice remains volatile.
