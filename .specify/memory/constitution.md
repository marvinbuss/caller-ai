<!--
Sync Impact Report (temporary review material; remove before committing)
Version change: unversioned scaffold -> 1.0.0 (initial adoption)
Modified principles:
- PRINCIPLE_1_NAME (unfilled) -> I. Azure-First Platform
- PRINCIPLE_2_NAME (unfilled) -> II. Security-First Engineering
- PRINCIPLE_3_NAME (unfilled) -> III. Documentation as a Deliverable
- PRINCIPLE_4_NAME (unfilled) -> IV. Bounded Microservices
- PRINCIPLE_5_NAME (unfilled) -> V. Python Backend
Added principles:
- VI. Scalable Real-Time Call Handling
Added sections:
- Architecture and Operational Constraints (replaces SECTION_2_NAME placeholder)
- Development Workflow and Quality Gates (replaces SECTION_3_NAME placeholder)
- Governance rules and adoption metadata
Removed sections: None
Deferred placeholders and follow-up TODOs: None
Dependent templates and commands: Unchanged; read this constitution at runtime.
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
be recorded. Reviews MUST reject missing or stale documentation so the system remains operable
without relying on its original authors.

### IV. Bounded Microservices

The backend MUST use microservices organized around explicit business capabilities. Each service
MUST have a documented responsibility, owner, data ownership boundary, and independently
testable and deployable artifact. Services MUST communicate through documented, versioned APIs
or event contracts and MUST NOT read or write another service's private data store directly.
Breaking contract changes MUST include a compatibility or migration plan. Service boundaries
MUST enable independent change, fault isolation, or scaling; arbitrary service fragmentation
without one of these benefits MUST be rejected.

### V. Python Backend

Application backend services, call orchestration, model integration, and backend workers MUST
be written in Python. Each service MUST declare a supported Python version and reproducible
dependencies, and MUST pass automated linting, type checking, and tests. Blocking I/O MUST NOT
run on realtime asynchronous execution paths. Frontends MAY use another language or framework,
but MUST integrate through the same documented service contracts. Infrastructure definitions
and build tooling MAY use their native languages. A consistent backend language reduces
maintenance overhead while preserving frontend flexibility.

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
MUST be reproducible using versioned infrastructure as code and configuration, include health
checks, and define rollback and active-call draining procedures.

## Development Workflow and Quality Gates

1. Specifications MUST define feature behavior, data sensitivity, failure behavior, documentation
	deliverables, and measurable acceptance criteria, including capacity targets for call paths.
2. Plans MUST identify Azure dependencies and quotas, Python service boundaries, contracts,
	threats and controls, and the tests that demonstrate compliance with all six principles.
3. Changes MUST include automated tests proportionate to risk. Contract changes MUST include
	contract tests; changes to call, model, or handoff paths MUST include integration tests and
	applicable load or failure tests.
4. Before merge, each change MUST pass code review, applicable linting and type checks, tests,
	secret and dependency vulnerability scans, and a documentation completeness review.
5. Before release, maintainers MUST verify security findings and approved mitigations, required
	capacity evidence, observability, runbooks, and rollback readiness. Missing evidence MUST block
	release of the affected functionality.

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

**Version**: 1.0.0 | **Ratified**: 2026-10-21 | **Last Amended**: 2026-10-21
