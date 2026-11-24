# Capacity and Cost Qualification

## Statement for the `int` environment

The first internal test environment (see [Environments](environments.md)) has **no capacity target**. Each app runs
one replica, the data store is serverless, and the admission limits above (one active test per manager, five
occupied slots) bound the load a tester can create. The environment exists to prove that the deployed apps start,
authenticate, converse and recover; it is not sized or priced for traffic. Do not use any number from it as a
forecast for a production environment. The test console is a way to try a configuration, not a call-handling
feature, so it carries no throughput promise.

## Greeting (feature 006)

Each new text conversation and each new voice session makes **one extra bounded model call**: the
Welcome agent writes the greeting with low reasoning effort, at most 256 output tokens and no
tools. It runs after `ready`, outside the admission budget, so it never delays or refuses
admission. Voice cancels it at 5 seconds and speaks nothing late; text waits at most 4 seconds and
then sends the fixed fallback. At most one greeting request is in flight per session.

| Target | Declared | Local load test (synthetic latency up to 3 s) | int measurement |
|--------|----------|-----------------------------------------------|-----------------|
| Voice: first greeting audio after `ready` | 5 s for at least 90 % of silent sessions (SC-001) | 5 concurrent sessions at the 5-slot limit: all within 5 s | **Pass** (T091, 2026-11-19): 29 of 30 sessions within 5 s (96.7 %), p50 2.4 s, p90 3.0 s; the one miss was cancelled at 5.0 s (`deadline`) and nothing was spoken late. Query in [monitoring](runbooks/monitoring.md#greeting-spoken-parts-and-progress-events) |
| Text: greeting after the open request | 5 s | 5 concurrent conversations: all within 5 s | Pending |
| Slow or failing Welcome agent | Voice cancels and logs `deadline`; text streams the fallback | All 5 voice greetings cancelled, nothing spoken late, sessions still answer; all 5 text conversations got the fallback within 5 s | Pending |
| Slots and cleanup | Unchanged by greetings | No extra refusal; every slot released after the sessions end; no greeting request left in flight | Pending |

The local results come from `code/backend/tests/load/test_greeting_capacity.py`, which uses the
test doubles and a fixed latency list (0.3, 0.9, 1.5, 2.2 and 3.0 seconds). They prove the bounds
and the failure behavior, not model latency on int. The voice int measurement covers the voice
sessions on `int` from 2026-11-13 to 2026-11-19 (images up to `sha-d3aa69d`).

## Shared history and handovers (feature 007)

Every agent now continues from the whole conversation, and a handover costs **one model step per
agent on the path** (Routing, then an entry agent, then perhaps a colleague), inside the existing
work budget (20 s text, 30 s speech) and step limit (8). Each step's input is the shared history,
so the input size per turn grows with the conversation until condensing (above 192 KiB, one
extra bounded call of at most 2,048 output tokens) brings it below 128 KiB. A voice delegation
may wait up to `CALLER_TRANSCRIPTION_WAIT_SECONDS` (0.5 s by default) for its transcript.

| Measurement | Declared | Local load test | int measurement |
|-------------|----------|-----------------|-----------------|
| Answer after a handover (SC-005 target) | Starts within the work budget: no `work_deadline_exceeded`, no `refused_budget` | 5 concurrent voice sessions at the 5-slot limit, each with three handovers, one refused handover, one condensing and one slow transcript: every answer within the budget, no `refused_budget` | Pending (T087, T088) |
| Handover delay per path and modality (SC-005) | Recorded, no threshold: p50 and p90 of `handover_seconds` by `path` and `kind` | Measured with the doubles only (no model latency) | Pending (T088) |
| Input size per turn | Recorded: `test.history` `history_kib` range at each handover | Below 16 KiB in the load test | Pending (T088) |
| Cost per conversation | Recorded: `input_tokens` and `output_tokens` of `test.usage` per conversation, handovers and condensing included | Not measured (doubles) | Pending (T088) |
| Slots and Live attempts | Unchanged: one slot and one Live attempt per voice session, released after verified cleanup | No extra admission refusal; every slot released; one `session.start` per session | Pending |

The local results come from `code/backend/tests/load/test_handover_capacity.py`. The queries are
in [monitoring](runbooks/monitoring.md#handover-delegation-and-history-events).

**Follow-up criteria for a dispatch step (option A of R1).** After the acceptance runs (T087),
record here:

- the added handover delay per path and modality (SC-005);
- wrong destinations and repeated questions in the 18 acceptance runs (SC-001, SC-002);
- how often voice requests are still rejected as unclear (`test.delegation` with reason
  `invalid_input`).

All three are **pending** until T087.

## Current State

Actual model cost per turn and per Live voice second, Cosmos RU demand, deployed instance and
session ceilings and safe admission limits are **pending live measurement**. No invented cost,
region, legal retention or quota default is accepted. Local control runs with synthetic latencies
are not production capacity or a billing estimate.

| Required measurement | State | Release obligation |
|----------------------|-------|--------------------|
| Text model cost per turn | Pending | Actual approved Foundry deployment usage and cost |
| Live voice cost per second and context-window usage | Pending | Approved GPT-Live usage records (`voice_seconds` snapshots are control evidence only) |
| Cosmos RU per authority and history read/write, throttling and retention | Pending | Owned-container Strong authority operations and failure behavior under measured load |
| Text history storage growth | Pending | Measured item size and the 30-day expiry effect on the `text-conversations` container |
| Instance and long-lived socket limits | Pending | Measured voice sessions per instance against the authority's five occupied slots |
| Voice and text turn latency and admission | Pending | Approved live samples, not HTTP means or virtual timing |
| Monitoring and log ingestion budgets | Pending | Approved daily cap, actual ingestion, retention and on-call delivery |

Admission allows one active test per manager and at most five occupied slots in the shared
authority item; an ambiguous write keeps a slot occupied until cleanup is verified. See
[conversation endpoints](conversation-endpoints.md) and [text history](runbooks/text-history.md).

Measure only after separately authorized qualification and live execution. Record approved
environment, image and review references and safe aggregate results without conversation
content, credentials or invented prices. Missing measurements block release but do not prevent
local deterministic development. [Live environment status](acceptance/live-environment.md) and
[release gates](acceptance/release-gates.md) remain the authoritative pending classifications.
