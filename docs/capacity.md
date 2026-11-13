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
| Voice: first greeting audio after `ready` | 5 s for at least 90 % of silent sessions (SC-001) | 5 concurrent sessions at the 5-slot limit: all within 5 s | Pending (T091, query in [monitoring](runbooks/monitoring.md#greeting-spoken-parts-and-progress-events)) |
| Text: greeting after the open request | 5 s | 5 concurrent conversations: all within 5 s | Pending |
| Slow or failing Welcome agent | Voice cancels and logs `deadline`; text streams the fallback | All 5 voice greetings cancelled, nothing spoken late, sessions still answer; all 5 text conversations got the fallback within 5 s | Pending |
| Slots and cleanup | Unchanged by greetings | No extra refusal; every slot released after the sessions end; no greeting request left in flight | Pending |

The local results come from `code/backend/tests/load/test_greeting_capacity.py`, which uses the
test doubles and a fixed latency list (0.3, 0.9, 1.5, 2.2 and 3.0 seconds). They prove the bounds
and the failure behavior, not model latency on int; the int measurement is recorded here when it
is taken.

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
