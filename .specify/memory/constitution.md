<!--
Sync Impact Report (temporary review material; remove before committing)
Version change: 2.1.0 -> 2.2.0
Bump rationale: Materially expand Principle VIII to allow reviewed, handover-ready scripts for
prerequisites and administrative tasks outside Terraform; no principle is removed or redefined.
Modified principles:
- VIII. Terraform Infrastructure as Code: new paragraph on administrative scripts outside Terraform
Added principles: None
Added sections: None
Updated sections: None
Removed sections: None
Deferred placeholders and follow-up TODOs: None
Dependent templates, commands, and feature specifications: Unchanged; they read this constitution
at runtime. Feature 004 task T001 (ratification of this rule) is satisfied by this amendment once
it is approved by the maintainers.
-->
# Caller AI Constitution

## Core Principles

### I. Azure-First Platform

Production hosting, identity, networking, storage, observability, and delivery tooling MUST use
Azure services and Azure-compatible tools by default. OpenAI realtime model integrations MUST
be designed for Azure-hosted operation, with model availability, regional support, and service
quotas verified before a design is approved. A non-Azure platform dependency MUST have a
documented capability gap, security and operational assessment, and maintainer approval before
adoption. This keeps platform operations and trust boundaries consistent.

### II. Security-First Engineering

Every feature MUST assess threats and protect caller data, call media, transcripts, credentials,
and model interactions. Services MUST enforce authentication, least-privilege authorization,
encrypted transport and storage, and isolation between customers where applicable. Azure managed
identities MUST be used where supported; remaining secrets MUST use an approved secret store
such as Azure Key Vault and MUST NOT appear in source control, logs, or model prompts.

Security defects MUST be triaged ahead of ordinary feature work. Critical and high-severity
security findings MUST block release of affected functionality until fixed or covered by an
explicitly approved, time-limited mitigation with an accountable owner. Active exploitation MUST
trigger incident response immediately. Caller input and model output MUST be treated as
untrusted; model-requested actions MUST pass server-side authorization and input validation.
These controls protect sensitive conversations and prevent models from becoming trust authorities.

### III. Documentation as a Deliverable

All code and features MUST be documented in the same change that introduces or modifies them.
Each module and public interface MUST document its purpose, inputs, outputs, failure behavior,
and security assumptions as applicable; non-obvious implementation decisions MUST be explained.
Each feature MUST include behavior, configuration, usage, and failure or recovery documentation.
Each service MUST maintain setup instructions, API or event contracts, dependencies, deployment
and rollback procedures, and an operational runbook. Architectural decisions and trade-offs MUST
be recorded. System and feature designs MUST be captured in version-controlled Markdown with
Mermaid diagrams in fenced `mermaid` blocks. Diagrams MUST show the affected service boundaries,
data flows, and interaction or handover sequences as applicable, and MUST be updated in the same
change as the design. Reviews MUST verify diagram rendering and consistency with the design,
and MUST reject missing or stale documentation so the system remains operable without relying
on its original authors.

### IV. Bounded Microservices

The backend MUST use microservices organized around explicit business capabilities. Each service
MUST have a documented responsibility, owner, data ownership boundary, and independently
testable and deployable artifact. Services MUST communicate through documented, versioned APIs
or event contracts and MUST NOT read or write another service's private data store directly.
Breaking contract changes MUST include a compatibility or migration plan. Service boundaries
MUST enable independent change, fault isolation, or scaling; arbitrary service fragmentation
without one of these benefits MUST be rejected.

### V. Python Backend and Reflex Frontends

Application backend services, call orchestration, model integration, and backend workers MUST
be written in Python. All application frontends MUST be developed in Python using Reflex and
MUST integrate through the documented service contracts. Each backend service and frontend
application MUST declare a supported Python version and reproducible dependencies, and MUST
pass automated linting, type checking, and tests. Blocking I/O MUST NOT run on realtime
asynchronous execution paths. Reflex-generated assets MAY use the framework's native output
languages; this does not permit authoring frontend applications in another framework.
Infrastructure definitions MUST follow Principle VIII; build tooling MAY use its native
languages. A consistent Python application stack reduces maintenance overhead across apps.

### VI. Scalable Real-Time Call Handling

Call processing MUST support horizontal scaling and explicitly managed call-session state.
Every call-handling feature MUST define measurable concurrent-call capacity, latency, availability,
and recovery targets before implementation. Designs MUST account for telephony connections,
long-lived realtime model sessions, Azure and model quotas, resource limits, and cost per call.
Autoscaling signals MUST reflect active sessions or measured load, not CPU utilization alone.

Services MUST bound queues, timeouts, and retries; apply admission control or backpressure under
overload; and make retried side effects idempotent. Stateful sessions MUST have documented
routing, draining, and recovery behavior. Before release, load and failure tests MUST verify
the declared targets, scale-out behavior, and overload handling. Capacity MUST be demonstrated
with evidence rather than assumed from infrastructure settings.

### VII. Structured Logging and Monitoring

All backend services, frontend applications, workers, and integration components MUST emit
application-owned logs using a documented structured event schema. Each event MUST include a
UTC timestamp, severity, application or service name, environment, event name, and outcome,
plus correlation and trace identifiers where available. Severity levels, event meanings, and
error categories MUST be defined consistently across applications. Log context MUST support
troubleshooting across app boundaries without exposing secrets or sensitive call content.

Every application MUST expose health and failure signals and define monitoring for errors,
latency, dependency failures, and resource saturation as applicable. Call-handling monitoring
MUST also cover active sessions, call and handover failures, and quota exhaustion. Actionable
alerts MUST have documented thresholds, an accountable owner, and a linked response runbook.
Before release, tests MUST demonstrate event correlation, sensitive-data exclusion, and alert
delivery for representative failures. These controls make issues detectable and diagnosable
across the complete solution rather than only within individual services.

### VIII. Terraform Infrastructure as Code

All infrastructure as code MUST be defined in Terraform. Infrastructure configuration and
reusable modules MUST be version controlled, with Terraform and provider version constraints
and reproducible dependency selections. Infrastructure changes MUST pass formatting, validation,
and security checks, and their Terraform plan MUST be reviewed before application. Changes
MUST document operational impact and recovery procedures so infrastructure remains reproducible
and auditable across environments.

Terraform state MUST use an access-controlled remote backend with encryption and locking to
prevent conflicting changes. State and plan artifacts MUST be treated as potentially sensitive
and MUST NOT be committed to source control or exposed in application logs. Secrets MUST use
the protections in Principle II. Alternative IaC authoring tools require a constitutional
amendment rather than an undocumented exception.

Prerequisites and administrative tasks that Terraform cannot or should not perform, such as
directory objects and role grants that need elevated directory rights, or the one-time creation
of the remote state storage, MAY be performed outside Terraform by an administrator. Each such
task MUST be delivered as a version-controlled, reviewed script that shows exactly how it is
done, so that it can be handed over to the administrators who hold the required rights. The
script MUST be idempotent, MUST offer a preview mode that changes nothing, MUST state the
rights, inputs, failure behavior, and recovery steps it needs, and MUST print the identifiers
that the Terraform configuration consumes. It MUST NOT create or manage infrastructure
resources that Terraform can manage, MUST follow Principle II for any secret it handles, and
MUST have automated tests under Principle IX. Each such task MUST be recorded in the
deployment documentation with its owner. A script that meets these rules is not an alternative
IaC authoring tool. This keeps deployment identities free of elevated directory rights while
the administrative steps remain reproducible and auditable.

### IX. Automated Tests for Functional Correctness

All new or modified executable application behavior MUST include automated code tests in the
same change, covering backend and frontend functionality as applicable. Tests MUST assert
expected results derived from requirements, including normal behavior, boundary conditions,
and failure paths proportionate to risk. Unit tests MUST cover changed business logic;
contract and integration tests MUST verify affected service interactions, and end-to-end
tests MUST cover changed critical user journeys. Creating test files without meaningful
assertions or executing them does not demonstrate functional correctness.

Every bug fix MUST include a regression test that fails without the fix and passes with it.
Generated tests MUST be reviewed for correct assertions and executed before the change is
considered complete. Tests MUST use controlled data and isolate nondeterministic external
dependencies where needed; integration tests MUST still verify the relevant real contracts.
Test data and outputs MUST comply with the security and privacy rules in Principle II.

Required automated tests MUST run in continuous integration, and their results MUST be
available to reviewers. Missing or failing required tests MUST block merge and release.
Tests MUST NOT be skipped, disabled, or weakened solely to obtain a passing build. These
requirements make functional correctness and regression prevention verifiable rather than
assumed from successful code generation or manual checks.

## Architecture and Operational Constraints

Caller AI is an Azure-based call-center solution in which agents and OpenAI realtime models
handle phone calls. Designs MUST document call ownership, model and agent responsibilities,
handoff behavior where applicable, and a defined response to telephony, service, or model failure.

Audio, recordings, transcripts, prompts, and model outputs MUST have documented collection,
access, retention, deletion, and residency rules before production use. Recording and disclosure
requirements MUST be reviewed for the applicable jurisdictions. Logs and telemetry MUST exclude
raw sensitive call content by default; any exception MUST have explicit access and retention
controls and security approval.

Services MUST emit structured, privacy-preserving logs, metrics, and distributed traces with
correlation identifiers across call and service boundaries. Operational metrics MUST cover
active calls, call failures, latency, quota exhaustion, and resource saturation. Deployments
MUST be reproducible using versioned Terraform infrastructure and configuration in accordance
with Principle VIII, include health checks, and define rollback and active-call draining
procedures. Logging and monitoring across all applications MUST comply with Principle VII.

## Development Workflow and Quality Gates

1. Specifications MUST define feature behavior, data sensitivity, failure behavior, documentation
	deliverables, and measurable acceptance criteria, including capacity targets for call paths.
2. Plans MUST identify Azure dependencies and quotas, Python backend and Reflex frontend
	boundaries, contracts, Terraform-managed resources, threats and controls, Markdown designs with
	Mermaid diagrams, and the tests that demonstrate compliance with all nine principles.
3. Changes MUST include automated tests proportionate to risk as required by Principle IX.
	Contract changes MUST include contract tests; changes to call, model, or handoff paths MUST
	include integration tests and applicable load or failure tests.
4. Before merge, each change MUST pass code review, applicable linting and type checks, required
	automated tests with recorded results, secret and dependency vulnerability scans, applicable
	Terraform checks, and a documentation completeness review that includes Mermaid rendering
	and design consistency.
5. Before release, maintainers MUST verify security findings and approved mitigations, required
	capacity evidence, structured-log correlation and privacy checks, alert delivery, runbooks,
	and rollback readiness. Missing evidence MUST block release of the affected functionality.

## Governance

This constitution takes precedence over conflicting project practices, templates, and plans.
Maintainers MUST review compliance during specification, planning, code review, and release.
Each review MUST record compliance evidence or identify a blocking gap; a checklist assertion
without supporting evidence is insufficient.

Amendments MUST be proposed as a documented change stating the rationale, affected principles,
impact on existing services and documents, and any migration actions with owners. Maintainer
approval MUST precede adoption. Approved changes MUST update the version and last-amended date;
the original ratification date MUST remain unchanged. Maintainers MUST check dependent guidance
for conflicts after an amendment and track necessary follow-up changes separately.

Versions MUST follow semantic versioning: MAJOR for incompatible principle or governance
removals or redefinitions; MINOR for new principles, sections, or materially expanded guidance;
PATCH for non-semantic clarifications and corrections. The initial adoption is version 1.0.0.
Exceptions MUST follow an explicit approval path in the relevant principle. Any other deviation
requires a constitutional amendment rather than an undocumented waiver.

**Version**: 2.2.0 | **Ratified**: 2026-10-21 | **Last Amended**: 2026-11-05
