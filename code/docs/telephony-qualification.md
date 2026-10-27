# Telephony and Realtime Qualification

**Reviewed**: 2026-10-23. **Decision**: no live phone provider or deployment is qualified.
Local deterministic implementation may continue; phone/model adapters and live admission
stay disabled until the separately authorized T099 environment gate supplies every required
proof. This document does not authorize provisioning, Azure login, model/phone calls or a
non-Azure provider. T026 and pending foundation acceptance are not waived.

## Source-Backed Findings

The [ACS retirement and breaking-changes guide](https://learn.microsoft.com/en-us/azure/communication-services/acs-retirement-and-breaking-changes-guide)
states that standalone ACS retirement takes effect on 2028-09-30. Onboarding restrictions
for retiring services begin 2026-10-23, with existing-resource conditions. Restrictions do
not automatically prohibit supported Teams scenarios, but eligibility does not imply feature
parity. Call Automation/Audio Streaming remain subject to the documented scenario, SDK,
licensing, participant and endpoint requirements. Connecting a standalone workload to an
AI service alone does not establish continued support.

The [ACS audio-streaming overview](https://learn.microsoft.com/en-us/azure/communication-services/concepts/call-automation/audio-streaming-concept)
describes bidirectional call streaming, 16-bit mono PCM at 16/24 kHz, 20 ms packets and mixed
or unmixed channel modes. These general capabilities are not evidence that every Teams
integration supports injecting audio back into the call. The retirement guide explicitly
requires checking direction, formats, channels, participants and SDK support for the exact
scenario. TPE documentation of streaming audio out of a call is not bidirectional proof.

The [TPE capability matrix](https://learn.microsoft.com/en-us/azure/communication-services/concepts/interop/tpe/teams-phone-extensibility-capabilities)
is a required scenario-specific qualification input, not an approval recorded by this review.
Tenant entitlement, resource-account configuration, number/PSTN connectivity and the exact
bidirectional operation must be confirmed through current documentation and, where unclear,
Microsoft support. No tenant/license/resource evidence was inspected here.

The [Azure realtime WebSocket guide](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/realtime-audio-websockets)
documents the GA endpoint pattern:

```text
wss://<approved-resource>.openai.azure.com/openai/v1/realtime?model=<approved-deployment>
```

Use explicit authentication headers with the OpenAI Agents SDK model configuration.
Managed-identity bearer authentication is preferred where supported; verify the deployment's
token audience/scope, RBAC and renewal behavior rather than assuming a legacy scope. The
guide recommends Microsoft Entra and an Authorization bearer header, with api-key headers
as a separate supported mechanism. This project does not put credentials in URL queries.
Do not mix the GA model parameter/path with preview deployment/api-version formats.

The [OpenAI Agents realtime guide](https://openai.github.io/openai-agents-python/realtime/guide/)
documents `RealtimeAgent`, `RealtimeRunner`, `RealtimeSession` and injectable `RealtimeModel`.
Custom Azure endpoints use `model_config` URL and explicit headers; providing headers means
the SDK does not add Authorization automatically. Local checks use the independently locked
SDK 0.24.0 and scripted models; successful imports/sessions do not prove a live Azure
deployment, codec bridge, authorization renewal or latency target.

## Candidate Assessment

| Candidate | Current Assessment | Required Before Adoption |
|-----------|--------------------|--------------------------|
| Standalone ACS PSTN/Direct Routing | Not an unconditional long-term choice; retirement/onboarding constraints apply | Eligibility evidence, supported lifetime and approved migration strategy |
| Teams Phone Extensibility | Potential Azure/Teams-aligned path; no bidirectional injection proof for this project | Exact supported call/media operations, licensed resource account, PSTN path and current SDK support |
| Teams meeting interoperability or click-to-call | Eligibility alone does not establish server-side streaming/Call Automation parity | Explicit documentation/support confirmation for every required operation |
| Other communications provider | Not selected or approved | Constitution-required gap/security/operations assessment and maintainer authorization |

## Required Environment Evidence

| Area | Required Proof | Current Status |
|------|----------------|----------------|
| Lifecycle/onboarding | Supported scenario, resource eligibility and migration horizon | Pending |
| Identity/admission | Callback issuer/audience/signature/expiry, replay handling and admitted call binding | Synthetic contracts only |
| Bidirectional media | Audio received from and injected into the same qualified call | Pending |
| Codec/framing | Exact wire protocol, sample rate, channels, frame length and bridge/resampling policy | Pending; fixture-codec is not a real codec |
| Playback interruption | Provider clear/stop acknowledgment plus model cancellation within one second | Pending live measurement |
| Termination | Provider hangup acknowledgment and normal closure within five seconds after Goodbye | Synthetic contracts only |
| SDK/API compatibility | Locked SDK/client options against approved GA deployment/provider versions | Scripted SDK control passes; live compatibility pending |
| Identity renewal | Managed identity/token scope, expiry and bounded reconnection without credential leakage | Pending |
| Capacity/quotas | Supported concurrent calls, model/provider quotas, admission budget and cost | Pending |
| Network/deployment | Reviewed image deployment, private data access and allowlisted external destinations | Pending T099 |
| Privacy/residency | Approved regions, disclosure/consent, provider/model retention and audit policy owners | Pending approval |

No pending entry may be converted into success by a synthetic benchmark, generic product
availability page or a health endpoint. Unknown provider eligibility blocks live enablement,
not the deterministic coordinator/repository/SDK test work.

## Wire and Privacy Requirements

The qualified provider adapter must pin its actual callback/media payloads, supported SDK
operations and authentication surface. The local `/v1/dev/transport` probe and opaque T032
fixture bytes must never be presented as that provider protocol. Require authentication and
call binding before upgrade, one reader/serialized writer, 64 KiB encoded frame limits,
bounded audio queues starting at 200 ms and stale-epoch output rejection.

Use one absolute ten-second operation/failure deadline across attempts and cooperative
cleanup. Closing a WebSocket is not proof of ending a phone call. For delayed telephony
playback, qualify playback tracking against what was actually heard rather than assuming
generated audio has already played.

Audio, transcripts, prompts, summaries and tool payloads remain bounded volatile context,
never durable repositories, logs or trace exports. Documentation examples that print
transcripts or write audio files are not acceptable application logging/storage patterns.
SDK/provider tracing stays off by default; provider opt-in remains rejected until approved
metadata-only controls exist. Do not ask for credentials through assistant tools or commit
secret-bearing diagnostic reports. Actual legal/retention approval must identify owners and
regions; this document makes no jurisdictional or compliance certification claim.

## Activation and Reassessment

T035 completes this documented assessment, not live qualification. T099 must validate the
separately authorized environment, approved provider/media path, identity, network, model
deployment, privacy inputs and quotas before adapters can be enabled. Recheck the sources
and product terms at that point and after any provider/SDK/lifecycle change.

If required bidirectional injection, playback clear or hangup semantics are undocumented,
unavailable or incompatible with the approved scenario, stop live adapter work and obtain
support/maintainer decisions. Do not guess a wire protocol, select an unapproved alternative,
silently use public OpenAI/preview endpoints, or provision resources to bypass the gate.
