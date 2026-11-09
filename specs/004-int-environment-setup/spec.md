# Feature Specification: First Internal Test Environment

**Feature Branch**: `feat/spec-004`

**Created**: 2026-11-04

**Status**: Draft

**Input**: User description: "The next spec is about getting an actual first environment spun up that can be used for testing and development purposes, ensuring that all necessary configurations and dependencies are correctly applied and that the environment is stable and reproducible." It requires a clear Terraform file and module structure, a bootstrap deployment that provides network and service connectivity but not the remote state, per-environment configuration made only of a variable file and backend files, an optional public-access flag, managed platform services in place of self-managed infrastructure, and documentation of security, operations, limitations and references. No backward compatibility is required because nothing has been deployed before.

## Clarifications

### Session 2026-11-04

- Q: How should deployments reach the `int` environment and its Terraform state storage when the services are private? → A: GitHub-hosted runners deploy; the state storage is reachable over its public endpoint with identity-only access and no shared keys; all other services stay private.
- Q: Which applications should the `int` environment expose publicly when public access is turned on? → A: Both the management application and the runtime's test and conversation endpoints, each behind the source allow-list and its own authentication.
- Q: How should the separately managed Terraform state storage be created for a new environment? → A: A separate Terraform deployment creates it, applied once with temporary local state that is then migrated into the new storage and the local copy removed.
- Q: What should the hosted applications run when the environment is first provisioned, so the checks have something to answer? → A: The real application images, published to GitHub's container registry so that any published version can be pulled at any time. Image publishing is therefore in scope. The owner approves GitHub's container registry as the source of images, as Constitution Principle I requires for a non-Azure dependency, and the assessment is documented under FR-038.
- Q: Should the application images published to GitHub be private or public? → A: Public, so the environment pulls them without a pull credential.
- Q: Should the `int` app environment deployment create the AI model account and model deployments, or use an existing one? → A: Create them. The app environment deployment creates a new Microsoft Foundry account and the text and realtime model deployments that the variable file describes, grants the runtime identity access, and reaches the account only through a private endpoint (decided 2026-11-09, replacing an earlier "use an existing one"; FR-039).
- Q: Who creates the directory sign-in registrations (app registrations and roles) that protect the public entry points: the app environment deployment or the directory administrators? → A: Directory administrators create them once, using a PowerShell and Azure CLI script that this feature delivers. The script prefers federated credentials and user-assigned managed identities, the variable file names the results, and the app environment deployment only configures the applications to use them.
- Q: Should each environment have its own Azure subscription, or should environments share one? → A: Each environment has its own subscription, named in its variable file.
- Q: Should outbound internet traffic from the hosted applications be restricted? → A: No additional outbound rules. Default network rules and configuration apply, because the real hosting lands in a customer network that a platform team provides (decided 2026-11-05, replacing an earlier approved-destinations list; FR-044).
- Q: How many GitHub environments and federated credentials does an environment need? → A: One. The GitHub environment is named exactly like the deployment environment (`int`), every root's plan and apply jobs run in it with required reviewers, and the deployment identity has one federated credential for that environment (decided 2026-11-09, replacing a plan and an apply environment per root).

### Session 2026-11-05

- Q: Where are the network resources created? → A: All network resources (virtual network, subnets, network security groups and route tables) are created in the bootstrap deployment, with default rules and routes (FR-005, FR-045).
- Q: What hosts the runtime and management applications? → A: Azure Container Apps (FR-025).
- Q: What is the third deployment called? → A: The app environment deployment, with identifier `app_environment`, formerly called the platform deployment.
- Q: How does the management application's sign-in work on Azure Container Apps, given that the platform's built-in sign-in cannot use a federated credential and would run in front of the application's own session guard? → A: The management application's own Microsoft Entra sign-in (an authorization-code flow with server-side sessions) with a client secret, and no platform sign-in. This is an owner-approved exception to the no-secret rule (FR-041).
- Q: Which deployment creates the Key Vault that holds that secret, and how does the secret reach it? → A: The app environment deployment creates the Key Vault, and no additional vault is created. The identity script creates the sign-in registration and the client secret once and writes the secret to a local file that source control ignores. The administrator stores it in a protected GitHub environment secret, and the app environment deployment receives it as a sensitive input and stores it in the Key Vault (FR-040, FR-046).
- Q: Where does the app environment deployment run, given that it needs that secret? → A: On GitHub-hosted runners like every other deployment. The secret is stored once as a secret of the protected GitHub environment, and the local file is only a hand-over (FR-046).
- Q (carried over from the analysis review, answered earlier): How should the runtime's missing sign-in be resolved? → A: Reword FR-019 so that authentication applies where an entry point carries data. The management application uses the sign-in from FR-041, the runtime's conversation endpoints use the runtime's signed-permit check, and health probes are exempt but stay behind the allow-list (FR-019).
- Q (carried over from the analysis review, answered earlier): How should the image-pull failure rule be enforced? → A: By detection after the deployment. The environment check fails and names the application and version, and the environment keeps running the version it had (FR-037).
- Q: How should "a healthy response from an allowed source" be proved for an app that requires sign-in? → A: Per app. The runtime's health probe answers success from an allowed source. For the management app the platform reports it healthy, and an unauthenticated request from an allowed source is redirected to sign-in (SC-012).
- Q: What should "the model is reachable" and "connections between services work" mean when the applications make no calls yet? → A: Control-plane state. The account and deployments exist, the private connection is approved, and the runtime identity holds the role. A real call is proved by the functional journey in FR-048 (FR-028, FR-039).
- Q: How should the spec treat the runtime's "test and conversation endpoints" that the published images do not serve yet? → A: The applications must work in `int`. The owner wants the management test console to work with the backend already, so the endpoints must exist and be reachable (FR-018, FR-047, FR-048).
- Q: Which application functionality must work in `int` beyond health and status pages? → A: A deployable live mode for both applications, so that a signed-in tester can use the configuration editor and the test console with text and speech against the runtime and the existing model, with data kept in the data store. Telephony calls stay out of scope (FR-047 to FR-050).
- Q: Who may sign in to the management application in `int`? → A: Any user of the directory tenant may sign in. Editing and testing need the editor or tester application role, which the identity script assigns to the group named in the variable file, so the owner chooses who can use the editor (FR-019).
- Q: Should GitHub-hosted runners be able to reach `int` when public access is on? → A: Yes. Their addresses change, so `int` sets the open range `0.0.0.0/0` explicitly in its allow-list and the pipeline's environment check, including the functional journey, runs from the runner as the deployment identity holding the tester role (FR-019, FR-028, FR-053).
- Q: How should the key pair that signs and verifies test-conversation start permits be created and stored? → A: The app environment deployment generates an RSA key pair, stores the private key only in the Key Vault, where the management application reads it through its managed identity, and gives the public key to the runtime. This is a second owner-approved exception to the no-secret rule, and the key lives in the protected remote state (FR-041, FR-051).
- Q: How should the runtime and the management app prove their identity to each other when they call each other? → A: With managed identity tokens and an application role, and no extra secret. The directory change that assigns the role is made by the identity script, because the app environment deployment may not change directory objects (FR-042, FR-052).
- Q: Should the rule "module calls only in `main.tf`" apply to deployments as well as to reusable modules, given FR-001 wants one file per service in every deployment? → A: It applies to reusable modules only. Deployments keep each service's module call in that service's own file (FR-001, FR-002).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Stand up the `int` environment from configuration alone (Priority: P1)

A platform engineer needs a working internal test environment, called `int`, in which developers and testers can run the Caller AI services. The engineer provides one variable file and the backend files for `int`, runs the documented state, bootstrap and app environment deployments in order, and ends with a running environment. Nothing else is edited, and no code is changed to describe the environment.

**Why this priority**: Nothing else in the project can be exercised against real infrastructure until one environment exists. This is the smallest slice that delivers value.

**Independent Test**: Starting with a subscription that meets the documented prerequisites, create only the `int` variable file and backend files, then run the bootstrap and app environment deployments. Every declared service reports healthy, and the environment's outputs list the endpoints and identifiers needed to reach it.

**Acceptance Scenarios**:

1. **Given** the prerequisites are in place and the `int` configuration files exist, **When** the engineer runs the bootstrap deployment, **Then** the virtual network, subnets, network security groups, route tables, private connectivity and name resolution that later deployments depend on exist with default rules and routes and are reported in the deployment outputs.
2. **Given** the bootstrap deployment has completed, **When** the engineer runs the app environment deployment for `int`, **Then** every declared service is provisioned and integrated, and the deployment outputs identify each service without exposing secrets.
3. **Given** the `int` environment is running, **When** the engineer inspects the repository, **Then** the only environment-specific files for `int` are one variable file and its backend files, kept in the dedicated environment-configuration location and nowhere in the main code.
4. **Given** a second environment such as `tst` is later required, **When** an engineer adds its variable file and backend files, **Then** the same Terraform code provisions it with no code change and it keeps its own state, separate from `int`.

---

### User Story 2 - Reproduce and trust the environment (Priority: P1)

A developer or tester relies on `int` to be stable. The engineer needs to know that the environment matches its configuration, that repeating a deployment changes nothing, and that a misconfigured input is caught before any resource is created.

**Why this priority**: A test environment that drifts, or that cannot be rebuilt, produces results nobody can trust. This is the stated goal of the feature.

**Independent Test**: Run the validation step against the `int` files, deploy, run the same deployment again, then run an environment check. The second deployment reports no changes and the check passes. Re-run validation with a required value removed and it fails before any resource is touched.

**Acceptance Scenarios**:

1. **Given** the `int` environment has been deployed, **When** the same deployment runs again with unchanged inputs, **Then** it reports zero planned changes.
2. **Given** an environment variable file omits a required value or contains an invalid one, **When** validation runs, **Then** it fails before any resource is created and the message names the file and the setting.
3. **Given** the `int` environment has been deployed, **When** the environment check runs, **Then** it confirms each service is provisioned as configured, each connection between services works, and it reports any service that is not.
4. **Given** the `int` resources are removed and the deployment is run again from the same configuration, **When** it completes, **Then** the environment is equivalent to the first one and requires no manual repair.
5. **Given** the environment's remote state, **When** an engineer or the repository is inspected, **Then** state and plan files are held only in the access-controlled, encrypted, locked remote backend, never in the repository or in logs.

---

### User Story 3 - Control public access per environment (Priority: P2)

A tester outside the private network needs to reach `int` to test the application. The environment owner turns on an optional public-access setting for `int` only, and the environment exposes its designated entry points on the public internet with access controls. Environments that do not set it stay private.

**Why this priority**: Internal testing from outside the private network is needed now, but exposure has to remain a deliberate choice for each environment. The environment works privately without this flag.

**Independent Test**: Deploy `int` with public access disabled and confirm no service is reachable from the public internet. Enable the flag, redeploy, and confirm that only the designated entry points answer from an allowed source, that sources outside the configured ranges are refused (when the ranges are not open), and that data stores and secret stores remain unreachable.

**Acceptance Scenarios**:

1. **Given** the setting is absent or disabled, **When** the environment is deployed, **Then** no service endpoint is reachable from the public internet.
2. **Given** the setting is enabled for `int`, **When** the environment is deployed, **Then** only the designated entry points (the management application and the runtime's test and conversation endpoints) are reachable publicly, and every other service stays private.
3. **Given** public access is enabled, **When** a request arrives from a source outside the configured ranges (when the ranges are not open) or lacks the required sign-in or permit (health probes excepted), **Then** it is refused, and the refusal is recorded.
4. **Given** public access was enabled, **When** the setting is disabled and the environment is redeployed, **Then** public reachability is removed without any manual cleanup and without data loss.
5. **Given** the setting is enabled in one environment, **When** another environment is deployed, **Then** the other environment's exposure is unchanged.

---

### User Story 4 - Navigate and extend the infrastructure code (Priority: P2)

A maintainer needs to find where an input, output, provider setting or service is defined, change one service without touching the others, and add a new service or environment without rework.

**Why this priority**: The structure is what keeps later environments and services cheap to add and review. It does not block standing up `int`, which is why it ranks below the first two stories.

**Independent Test**: Review the Terraform tree. Each deployment has separate files for variables, provider configuration, outputs and each service, and every module has its own directory with declared inputs, outputs and resources. A structure check passes on this layout and fails when a file is merged or a module lacks its inputs or outputs.

**Acceptance Scenarios**:

1. **Given** any Terraform deployment in the repository, **When** a maintainer opens it, **Then** it has one file for variables, one for provider configuration, one for outputs, one file per service, and separate files for each other major resource or module.
2. **Given** any Terraform module, **When** a maintainer opens it, **Then** it sits in its own directory, declares its inputs and outputs with descriptions, and contains only the resources for its own purpose.
3. **Given** a change to a single service, **When** it is planned, **Then** only that service's module and the files that call it need to change.
4. **Given** a module is introduced or changed, **When** the automated infrastructure checks run, **Then** formatting, validation, structure and module tests are included and run in continuous integration.

---

### User Story 5 - Use the configuration editor and test console in `int` (Priority: P2)

A tester signs in to the management application in `int`, edits and publishes an agent configuration, and uses the test console to hold a text conversation and a speech conversation with the runtime. The conversations use the existing AI model, and the configuration and history are kept in the data store. No application code is changed for the environment.

**Why this priority**: An environment that only answers health probes cannot be tested or developed against. The infrastructure stories come first, and this story makes the environment useful for its stated purpose.

**Independent Test**: With `int` deployed and public access enabled, a tester from an allowed source signs in, opens the configuration editor, publishes a configuration, runs a text test conversation and a speech test conversation, and receives model replies. Then the runtime is restarted and the stored configuration and history are still there.

**Acceptance Scenarios**:

1. **Given** `int` is deployed and the tester is signed in from an allowed source, **When** the tester opens the management application, **Then** the configuration editor loads the configuration held in the data store.
2. **Given** a published configuration, **When** the tester starts a text conversation in the test console, **Then** the runtime answers with a reply from the existing model and the history is stored.
3. **Given** a published configuration and microphone permission, **When** the tester starts a speech conversation in the test console, **Then** speech is sent to the runtime, a spoken reply comes back, and stopping the session is confirmed.
4. **Given** the tester is not signed in or the request comes from a source outside the configured ranges (when the ranges are not open), **When** the tester opens the management application or starts a conversation, **Then** the request is refused.
5. **Given** stored configuration and history, **When** an application is restarted or moved to another published image version, **Then** the stored data is still available.

---

### User Story 6 - Operate the environment with clear guidance (Priority: P3)

An operator or reviewer needs one place that explains how the environment is built, secured, operated and recovered, what it deliberately does not do, and where to read more.

**Why this priority**: Documentation is a deliverable under the project constitution, and the environment must be operable without its original authors. It builds on the other stories.

**Independent Test**: A reviewer who did not build the environment follows the documentation alone to deploy `int`, toggle public access, recover from a failed deployment and roll back, and finds no step that depends on unwritten knowledge.

**Acceptance Scenarios**:

1. **Given** the documentation, **When** a new engineer follows it, **Then** they can prepare prerequisites, deploy the state and bootstrap deployments, run the identity script, publish the images, deploy the app environment, validate the result and tear the environment down.
2. **Given** the documentation, **When** a reviewer looks for security, compliance, operational and recovery guidance, **Then** each has its own section with Mermaid diagrams of the network, service boundaries and deployment sequence.
3. **Given** the documentation, **When** a reviewer looks for known limitations and constraints of the environment, **Then** they are stated, including which things `int` does not provide.
4. **Given** the documentation, **When** a reader needs more background, **Then** it links to the relevant official guidance for Terraform and the Azure services used.

### Edge Cases

- A required name is globally unique and already taken: validation or the deployment fails with a message naming the setting, and no partial environment is left unmarked.
- The chosen region lacks capacity or quota for a declared service: the deployment fails clearly and names the service, and can be retried after the setting changes.
- A deployment stops halfway: re-running it from the same configuration completes it without manual repair, or the documentation states the exact recovery steps.
- The remote state is locked by another run: a second run waits or fails clearly and never writes concurrently.
- The state backend does not exist yet: bootstrap and app environment deployments refuse to start and point to the state deployment, and neither falls back to local state.
- Public access is enabled but no allowed source ranges or authentication are configured: validation fails, so the environment cannot be exposed without controls.
- Public access is turned off after being on: the environment returns to fully private with no leftover public rule.
- A variable file contains a secret value: secret scanning rejects it, and secrets are supplied only through an approved secret store.
- Two environments are pointed at the same state location: validation detects the clash and refuses it.
- The identity script is run twice, or after a partial failure: it completes the missing objects, changes nothing that already matches, and reports what it did.
- The person running the identity script lacks directory permissions: it stops before changing anything and names the missing permission.
- A sign-in registration or role named in the variable file does not exist: the environment check reports the missing registration or role, naming the setting, and the management application refuses to start with a fixed reason code.
- An application cannot reach a destination: this feature adds no outbound rules, so reachability follows the network the environment lands in. The documentation states this, and any fix is made in the network and not in the app environment deployment.
- A configured image version does not exist or cannot be pulled: the environment check fails naming the application and version, and the environment keeps running the version it had.
- First deployment of an environment: the management application's callback address exists only after the apps do, so the first environment check is expected to fail once at the sign-in redirect. The identity script then registers the address, and the check is rerun and passes.
- The sign-in secret expires or is lost: the identity script is run again and issues a new secret, the administrator replaces the GitHub environment secret, and the app environment deployment is run again. Until then the management application's sign-in fails closed and no request is let through.
- The local hand-over file still exists after the secret was stored: the documentation tells the administrator to delete it, and source control ignores it so it can never be committed.
- An application setting is missing or invalid at start: the application refuses to start with one fixed reason code, serves no conversation or configuration traffic, and the environment check reports the reason for that application.
- The runtime's application role assignment is missing: the management application refuses the runtime's calls with one fixed reason, and the environment check reports that the role is not held.
- The AI model or the data store is unavailable during a test: the test console shows a clear failure, the session is cleaned up, no conversation content is lost or leaked, and the health probes report not ready where they depend on the store.
- An image contains a secret or a blocking vulnerability: publishing fails, and nothing is published for that build.
- Variable or backend files for an environment other than `int` are present but incomplete: they are ignored by `int` and reported by validation of their own environment only.
- Old environment names and files from before this feature (`dev`, `test`, `prod` examples and workflows) are still present: they are removed or replaced, because no backward compatibility is provided.

## Requirements *(mandatory)*

### Functional Requirements

**Structure and modules**

- **FR-001**: Every Terraform deployment in the repository MUST contain one file for variables, one for provider configuration, one for outputs, one file for each service, and separate files for each other major resource or module.
- **FR-002**: Reusable infrastructure modules MUST follow HashiCorp's standard module structure: one directory per module, with its own declared inputs, outputs and resources, a description on every input and output, and a README. A reusable module MUST make nested module calls only from its main file, and reusable modules MUST be composable rather than calling each other in deep chains. Deployments are not reusable modules, so they follow FR-001 and keep each service's module call in that service's own file.
- **FR-003**: Each module MUST encapsulate one service or capability and MUST be testable on its own with automated Terraform tests.
- **FR-004**: Terraform and provider versions MUST be pinned, and dependency selections MUST be reproducible, as required by the project constitution.

**Bootstrap**

- **FR-005**: The bootstrap deployment MUST create all network resources before any service is provisioned: the virtual network, subnets, network security groups and route tables, each with default rules and routes, plus the private connectivity and name resolution needed for the environment to reach its own components. No other deployment MUST create a virtual network, subnet, network security group or route table. Private endpoints are created with the service that they expose, in subnets that bootstrap provides.
- **FR-006**: The bootstrap deployment MUST NOT create or contain the remote state storage. A separate Terraform deployment, the state deployment, MUST create the state storage for each environment. It is applied once with temporary local state, which MUST then be migrated into the new storage and removed from the machine. The bootstrap deployment MUST store its state in that backend.
- **FR-007**: Bootstrap and app environment deployments MUST refuse to run against local state or an unreachable backend, and MUST report which prerequisite is missing. Local state is allowed only in the state deployment's one-time creation and migration step.
- **FR-008**: The bootstrap deployment MUST publish, as outputs, the identifiers that the app environment deployment needs, so that no value is copied between deployments by hand.

**Environment configuration**

- **FR-009**: Each environment MUST be defined by exactly one variable file, which every deployment of that environment reads, and one backend file per deployment (state, bootstrap and app_environment), and nothing else. The local sign-in secret hand-over file in FR-046 is not part of the environment configuration and is never committed.
- **FR-010**: Environment files MUST live in a dedicated location in the repository, separate from the Terraform code, with one directory per environment named `dev`, `tst`, `prd` or `int`. Only `int` MUST be created by this feature.
- **FR-011**: Each environment MUST use its own state, with a separate state key per environment and per deployment, so environments cannot overwrite or lock one another. Each environment MUST also have its own Azure subscription, named in its variable file, so that permissions, quotas and costs are separated. Resource names MAY therefore omit the environment name when they are unique within the subscription, but MUST NOT collide with another environment's names in shared global namespaces.
- **FR-012**: Environment files MUST NOT contain secrets, credentials or state. Secrets MUST be provided through the approved secret store and managed identities in line with the project constitution; the approved exceptions are described in FR-041, FR-046 and FR-051.
- **FR-013**: The system MUST validate an environment's files before any resource is created, covering required values, value formats, name rules, and the state-location clash described under Edge Cases. A failure MUST name the file and the setting.
- **FR-014**: Adding a new environment MUST require only new environment files and no change to Terraform code, workflows or documentation structure.
- **FR-015**: Files, workflows and examples that use the earlier `dev`, `test` and `prod` names MUST be replaced by the new environment naming. No backward compatibility or migration is required.

**Public access**

- **FR-016**: Each environment MUST accept an optional public-access setting, which defaults to disabled when absent.
- **FR-017**: With public access disabled, no environment service endpoint MUST be reachable from the public internet. The Remote State Backend is not an environment service and follows FR-031 instead.
- **FR-018**: With public access enabled, only the designated entry points MUST be reachable publicly: the management application and the runtime's test and conversation endpoints. Data stores, secret stores and monitoring MUST remain private.
- **FR-019**: Public access MUST NOT be accepted without access controls: an allow-list of source address ranges, and authentication wherever an entry point carries data. The allow-list MAY contain the open ranges `0.0.0.0/0` and `::/0` only when the owner sets them explicitly (`int` does, so that GitHub-hosted runners, whose addresses change, can reach the entry points). Validation then reports a warning, and the documentation MUST record the exposure. The management application uses the sign-in in FR-041, and the runtime's conversation endpoints use the runtime's signed-permit check. Health probes carry no conversation or dependency detail, so they are exempt from sign-in but remain behind the allow-list. Validation MUST reject an enabled setting that lacks the allow-list. Any user of the directory tenant may sign in to the management application. Editing and testing need the editor or tester application role, which the identity script assigns to the group named in the variable file (`app_environment.identity.user_group_id`, required), so the owner chooses who can use the editor. With an open range, sign-in and the role check are the only limits on who can reach the entry points.
- **FR-020**: Enabling or disabling public access MUST change only exposure and MUST NOT remove data or reconfigure unrelated services.
- **FR-021**: Public access MUST be enabled for `int` and left disabled by default in every other environment.

**Managed services**

- **FR-022**: Where a managed platform service meets a need, the environment MUST use it in place of virtual-machine or node-managed hosting and other self-operated infrastructure. Each use of a self-operated component MUST be justified in the documentation.
- **FR-023**: The environment MUST favour simple, widely supported managed services that meet the project's performance, security and operability requirements and keep operations simple. Every service uses one fixed definition and its small sizing defaults from the variable file, and each remaining choice is justified in the documentation (FR-022).
- **FR-024**: Every managed service MUST be provisioned through its own Terraform module and configured by the same definition in every environment.
- **FR-025**: The app environment deployment MUST provision and integrate every service the application needs to run in `int`: hosting for the runtime and management applications on Azure Container Apps, a data store, a secret store, monitoring and logging, and the identities and permissions that connect them. The hosting MUST pull its application images from GitHub's container registry, so no Azure-hosted image registry is required unless the plan documents a need for one.
- **FR-036**: Continuous integration MUST build the runtime and management application images and publish them to GitHub's container registry as public images, each with an immutable version tag. Any published version MUST remain pullable at any time, and published versions MUST NOT be overwritten or deleted by later builds.
- **FR-037**: The environment variable file MUST name the exact image version, by tag or digest, that each hosted application runs. The app environment deployment MUST start those versions. When a version cannot be pulled, the environment check MUST fail and report the application and version, and the environment MUST keep running the version it had. Changing the version in the file MUST be the only step needed to move an environment to another published image.
- **FR-038**: Because the images are public and come from a non-Azure source, they MUST contain no secrets, credentials or environment-specific configuration, and image publishing MUST include a secret scan and a vulnerability scan of every image. The documentation MUST record the capability gap, the security and operational assessment and the owner's approval that Constitution Principle I requires.
- **FR-039**: The app environment deployment MUST create a new Microsoft Foundry account and its two model deployments, one for text and one realtime deployment for speech, from the model, version, deployment type and capacity that the environment variable file names. The account MUST disable key-based access and public network access, and is reached only through a private endpoint. The realtime deployment MUST be named after the model the runtime requests (`gpt-live-1`), and the two deployments MUST differ. The deployment MUST grant the runtime's identity the least access needed to call the model, and it MUST fail, naming the setting, when a model setting is invalid. Model quota is a subscription matter that the deployment cannot create; a missing quota fails the apply with the provider's reason. Whether its private connection is approved and the role is held is reported by the environment check (FR-028), because neither can be known before apply. A real call to the model is proved by the functional journey in FR-048.
- **FR-040**: The directory sign-in objects that protect the public entry points, meaning application registrations, application roles, service identities and any identity that needs directory-level permissions, MUST be created by a PowerShell script that uses the Azure CLI, which this feature delivers. Directory administrators run it once per environment, before the app environment deployment. The script MUST be idempotent, MUST support a preview mode that changes nothing, and MUST print the identifiers that the variable file needs. It also creates the client secret for the management application's sign-in registration (FR-041) and writes it only to a local file that source control ignores, never to the screen or a log.
- **FR-041**: The script and the environment MUST use federated credentials and user-assigned managed identities wherever the platform supports them. The management application's own sign-in is a confidential client, which cannot use a federated credential, so the owner approves one exception: a client secret for the management application's sign-in registration. That secret MUST have a validity of at most 90 days, and MUST NOT be printed, logged or committed. Its holders and path are defined in FR-046. The script MUST NOT create, print or store any other client secret, certificate or key, and where another secret is unavoidable it MUST stop and explain. A second approved exception, the permit signing key pair, is defined in FR-051 and created by the app environment deployment, not by the script. The documentation MUST record both exceptions, their expiry and the renewal steps.
- **FR-042**: The app environment deployment MUST NOT create or change directory objects. It MUST only read the identifiers named in the variable file and configure the applications to use them. Directory objects cannot be read without a directory provider, so a missing registration or role is reported by the environment check (FR-028) and by the application's start-up reason code, not by the deployment.
- **FR-043**: Because the script defines identity infrastructure outside Terraform, the documentation MUST state why directory administration is kept apart from Terraform and MUST record the owner's approval, so that Constitution Principle VIII is applied knowingly and not bypassed. Before the script is implemented, the maintainers MUST ratify in the constitution, through a separate constitution update, that Principle VIII governs infrastructure resources and that directory objects are administered by this reviewed, tested and idempotent script. The script MUST have automated tests, including its preview mode, its failure paths, and proof that it never prints the sign-in secret or writes it anywhere except the ignored hand-over file.
- **FR-044**: No additional outbound network rules MUST be configured. The deployments MUST use the default network rules and configuration, and MUST NOT create custom outbound security rules, custom routes or a filtering appliance. Outbound reachability is whatever the network provides. In a customer setup the hosting lands in a network that a platform team provides and owns.
- **FR-045**: The app environment deployment MUST receive its network only through the identifiers that the bootstrap deployment publishes (subnets and name-resolution zones), so that bootstrap can later be replaced by a network supplied by a platform team without changing the app environment deployment.
- **FR-046**: The management sign-in secret MUST be handled in one path only. The identity script writes it to a local file that source control ignores. The administrator stores it once as a secret of the protected GitHub environment and then deletes the file. The app environment deployment, running on a GitHub-hosted runner, receives it as a sensitive input and stores it in the Key Vault that the deployment creates, and no additional vault is created. The management application reads it from that vault through its managed identity. The secret MUST NOT appear in plan output, deployment outputs, logs or environment files, and the documentation MUST state that the remote state holds it and is protected under FR-031.
- **FR-047**: Both application images MUST start in a deployed mode that the environment selects through settings given at start, and MUST NOT depend on a different image per environment. In that mode the runtime and the management application MUST reach the data store, the secret store and the AI model through managed identities and without shared keys. They MUST refuse to start, with one fixed reason code and no secret or input value in the message, when a required setting is missing or invalid, and the environment check MUST report that reason by application.
- **FR-048**: In `int`, a signed-in tester MUST be able to use the management application's configuration editor and test console against the runtime: edit and publish a configuration, hold a text test conversation and hold a speech test conversation that each receive a reply from the existing AI model. The configuration, its audit entries and the text conversation history MUST be kept in the data store with the retention that the applications already define.
- **FR-049**: The runtime and management application MUST reach each other, and the tester's browser MUST reach the runtime's public entry points, through the exposure rules in FR-016 to FR-019 and the network that bootstrap provides, with no extra setting to change per environment beyond the variable file.
- **FR-050**: Telephony and real caller traffic are out of scope. Test sessions MUST stay within the applications' existing admission limits, and the documentation MUST state that `int` has no capacity target.
- **FR-051**: The key pair that signs and verifies test-conversation start permits MUST be generated by the app environment deployment. The private key MUST be stored only in the Key Vault and read by the management application through its managed identity, the public key MUST be given to the runtime as a setting, and neither key MUST appear in plan output, deployment outputs, logs or environment files. This is a second owner-approved exception to the no-secret rule. The documentation MUST record it, state that the remote state holds the private key under FR-031, and describe how to rotate the pair with the old and new public keys accepted during the overlap.
- **FR-052**: Calls between the runtime and the management application MUST authenticate with managed identity tokens and an application role, and the callee MUST check both the token and the role before serving the call. The identity script MUST create the application role on the management registration and the runtime's user-assigned managed identity, and MUST assign the role to that identity. The app environment deployment MUST only attach that identity to the runtime and MUST NOT change any directory object (FR-042). No extra secret is used for these calls.
- **FR-053**: The identity script MUST also assign the tester role to the deployment identity named by `environment.client_id`, so that the environment check can sign in to the management application as a tester through the identity's federated credential. The script MUST also grant it the read-only Microsoft Graph permission `Application.Read.All`, so that the environment check can verify that the registration and roles exist (FR-042). The deployment identity still holds no directory write permission (FR-042).

**Validation and stability**

- **FR-026**: Repeating a deployment with unchanged inputs MUST produce zero planned changes.
- **FR-027**: The environment MUST be re-creatable from its configuration alone and MUST be equivalent after re-creation.
- **FR-028**: An environment check MUST confirm that each declared service is provisioned as configured, that required connections between services work, meaning that the target exists, its private connection is approved and the calling identity holds the required role, and including the runtime's access to the named AI model, that the named sign-in registration and application roles exist, that public access matches the setting, that each hosted application runs the configured image version, that a text test conversation made through the management application receives a model reply and a speech test session starts and stops (FR-048), and MUST report each failure by service. The conversation and speech steps run from the pipeline's runner as the deployment identity holding the tester role (FR-053). When public access is disabled these steps are skipped with a stated reason, because the entry points are private (FR-017).
- **FR-029**: A deployment MUST NOT report success unless the environment check passes, and it MUST NOT claim application readiness it has not verified.
- **FR-030**: Infrastructure changes MUST pass formatting, validation, structure, security and secret checks and have a reviewed plan before they are applied, as required by the project constitution.

**Security, compliance and documentation**

- **FR-031**: State and plan files MUST be encrypted, access-controlled, locked during changes, and kept out of source control and logs. The Remote State Backend MUST accept only identity-based access with shared keys disabled, so that deployments can run on GitHub-hosted runners without a private network path. Its public reachability is limited to the state and plan data and MUST NOT depend on, or open, any other environment service.
- **FR-032**: Services MUST authenticate with managed identities and least-privilege roles where supported. No shared keys or long-lived credentials MUST be used where an identity-based alternative exists.
- **FR-033**: Logs and telemetry from the environment MUST be structured and MUST exclude sensitive call content and secrets by default.
- **FR-034**: The change MUST be documented in the same change that introduces it. The documentation MUST cover prerequisites, deployment order, public-access operation, validation, rollback and recovery, teardown, security and compliance notes, operational procedures, known limitations and constraints, and links to relevant official guidance. It MUST include Mermaid diagrams of network and service boundaries and the deployment sequence.
- **FR-035**: The new and changed infrastructure code and application behavior MUST include automated tests that assert the requirements above, including the failure paths, and they MUST run in continuous integration.

### Key Entities

- **Environment**: A named, isolated instance of the platform (`int`, and later `dev`, `tst`, `prd`). It has its own subscription, configuration, state, resources and exposure setting.
- **Environment Configuration**: The variable file and backend files for one environment, kept apart from the Terraform code. It holds nothing secret.
- **State Deployment**: The first deployment for an environment. It creates the Remote State Backend with temporary local state, migrates that state into the new backend, and removes the local copy.
- **Bootstrap Deployment**: The second deployment for an environment. It creates all network resources, meaning the virtual network, subnets, network security groups and route tables with default rules and routes, plus private connectivity and name resolution, that the app environment deployment relies on, and publishes their identifiers.
- **Remote State Backend**: The separately managed, encrypted, access-controlled and locked storage of Terraform state and plans, with state kept apart per environment and deployment. It is created by the state deployment before bootstrap and is not part of bootstrap.
- **App Environment Deployment**: The deployment that provisions and connects the managed services the application needs.
- **Service Module**: A self-contained Terraform module for one managed service, with declared inputs, outputs and tests.
- **Public Access Setting**: An optional per-environment flag, with its allow-list of source ranges, that controls whether the designated entry points are reachable from the public internet.
- **Environment Check**: The verification that an environment matches its configuration and its exposure setting, including the functional journey (redirect, probes, text and speech conversation) that the pipeline runs as the deployment identity.
- **Published Image**: An immutable, versioned, public container image of the runtime or management application, built and scanned by continuous integration and pullable at any time from GitHub's container registry.
- **Identity Setup Script**: A script that directory administrators run once per environment to create the sign-in registrations, roles and identities that cannot be created by the app environment deployment. It uses federated credentials where possible, assigns the application roles (including the tester role of the deployment identity) and creates one approved secret, the management sign-in client secret, which it writes only to an ignored local file.
- **Deployment Identity**: The identity (federated with GitHub through OIDC) that runs the deployments and the environment check. It holds no directory write permission. The identity script assigns it the tester role of the management registration and the read-only Graph permission `Application.Read.All` (FR-053).
- **Application Settings**: The non-secret, environment-specific values that the app environment deployment gives each application at start, such as the data store, secret store and model locations, the deployment names, the sign-in registration and the address of the other application. Secrets come only from the secret store. One published image runs in every environment because only these settings differ.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An engineer with the documented prerequisites can bring `int` from nothing to a passing environment check by creating only its variable file and backend files and running the documented steps, in a single working day and without help from the original authors.
- **SC-002**: A repeated deployment with unchanged inputs shows zero planned changes in 100% of repeats.
- **SC-003**: Re-creating `int` from its configuration alone gives an environment that passes the same environment check, with no manual repair, in 100% of trials.
- **SC-004**: Adding a new environment needs exactly two kinds of files (one variable file and the backend files of its deployments) and zero changes to Terraform code, as demonstrated by validating a sample second environment.
- **SC-005**: With public access disabled, 0 environment service endpoints are reachable from the public internet. With it enabled, only the designated entry points answer from allowed sources, and 100% of requests from sources outside the configured ranges are refused (with an explicit open range, sign-in and the role check are the only limits).
- **SC-006**: Invalid environment files are rejected before any resource is created in 100% of tested cases, each with a message that names the file and the setting.
- **SC-007**: A structure check finds the required file and module layout in 100% of Terraform deployments and modules, and fails when it is broken.
- **SC-008**: Secret scanning finds 0 secrets, state files or plan files in the repository, the environment files or the logs.
- **SC-009**: Every service in `int` that has a managed offering uses it, and every remaining self-operated component has a documented justification.
- **SC-010**: A reviewer who did not build the environment can use the documentation alone to deploy, toggle public access, recover from a failed deployment and tear down `int`, without an undocumented step.
- **SC-011**: 100% of published image versions remain pullable after later builds, 0 published images contain a detected secret, and moving `int` to another published version needs a change to exactly one setting in its variable file.
- **SC-012**: Once `int` is deployed, within 5 minutes of the deployment finishing and running the published images, the runtime's health probe answers success from an allowed source, the platform reports both applications healthy, and an unauthenticated request from an allowed source to the management application is redirected to sign-in.
- **SC-013**: Running the identity script twice in a row makes no change on the second run in 100% of trials. The only secret it ever creates is the approved management sign-in secret, and that secret appears in 0 printed outputs, logs, commits and environment files.
- **SC-014**: The deployments create 0 custom outbound security rules, 0 custom routes and 0 filtering appliances. All network resources (virtual network, subnets, network security groups and route tables) are created by the bootstrap deployment alone, as confirmed by inspecting the deployments' resource lists.
- **SC-015**: In `int` with the published images, a signed-in tester completes the journey in User Story 5 (open the editor, publish a configuration, run a text test conversation and a speech test conversation) in 100% of trials with no manual repair, and each text message receives a model reply within 15 seconds.
- **SC-016**: Configuration, audit entries and text conversation history are still available after an application restart and after moving an application to another published image version, in 100% of trials.

## Assumptions

- The Azure subscription for `int`, the deployment identity with its permissions and the target region for `int` already exist and are supplied as inputs. The app environment deployment creates the Foundry account and its model deployments (FR-039). Obtaining model quota and regional availability in the subscription is out of scope; the successful apply of both deployments in an allowed region is the evidence of regional support and quota (Constitution Principle I), and the environment check confirms that both deployments exist.
- A directory administrator with the permissions to create registrations and roles is available to run the identity script once per environment. The deployment identity itself needs no directory permissions.
- The remote state backend is a prerequisite that is created before bootstrap by the state deployment, a separate Terraform deployment with its own one-time documented procedure. The bootstrap deployment then stores its own state in that backend. The previous layout in which bootstrap created the state storage is replaced.
- Deployments run on GitHub-hosted runners that authenticate with identities, not stored credentials, except for the approved sign-in secret, which is stored as a secret of the protected GitHub environment (FR-046). Azure resource changes use the public management interface, and the state backend is the only storage reachable over a public endpoint, so no self-hosted runner or private connection is needed to deploy.
- `int` is for internal testing and development only. It carries no production data, no availability target and no capacity commitment beyond what testing needs. Real caller traffic, telephony integration and formal live model qualification (quality and capacity) are separate work.
- Public access, when enabled, covers only the designated entry points, the management application and the runtime's test and conversation endpoints, protected by an allow-list of source ranges and the authentication in FR-019 (the management application's sign-in, the runtime's signed-permit check, and health probes exempt behind the allow-list). Everything else stays private.
- Replacing the earlier node-managed hosting with Azure Container Apps is acceptable even though it changes the existing platform definition, because nothing has been deployed. The earlier outbound filtering appliance and approved-destinations list are dropped. The environment keeps default network rules only, because real hosting lands in a customer network that a platform team provides and owns, including its outbound policy.
- The management sign-in secret has a validity of at most 90 days by default, and renewal means running the identity script again and replacing the GitHub environment secret.
- The existing delivery pipeline and tooling for Terraform are reused and updated for the new environment names, location and structure, without keeping the old names.
- Only `int` is created now. The directory convention for `dev`, `tst` and `prd` is defined so they can be added later with no code change.
- Building and publishing the container images, running them in `int`, and giving both applications a deployable live mode that composes the editor, test console, runtime and stores they already contain (FR-047, FR-048) are in scope. New application features beyond that, and telephony, are out of scope. This feature ends with an environment in which a tester can use the editor and the test console end to end, connected and checked.
- Environment capacity, cost limits and retention periods use small, test-appropriate defaults, namely the values in `int.tfvars.json` (one replica per application, the minimum Consumption sizes, the retention and daily-cap values in the monitoring section), that are set in the environment's variable file and documented.
- Known limitations of `int`, such as public exposure for testing and the absence of high availability, are documented rather than removed.
