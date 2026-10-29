# Capacity and Cost Qualification

## Current State

Actual model/phone cost per call, Cosmos RU demand, deployed node/session ceilings and safe
fleet admission limits are **pending live measurement**. No invented cost, region, legal
retention or quota default is accepted. Local virtual 100-to-200 reservations and synthetic
latencies are control evidence, not production capacity or a billing estimate.

| Required measurement | State | Release obligation |
|----------------------|-------|--------------------|
| Model cost per call and transient handover overlap | Pending | Actual approved deployment usage/cost and simultaneous source/receiver headroom |
| Phone connection/media/control cost and limits | Pending | Qualified scenario/provider eligibility and measured connection/codec behavior |
| Cosmos RU per read/write/batch, throttling and retention | Pending | Owned-container Strong authority operations and failure behavior under measured load |
| AKS node/pod/network and long-lived socket limits | Pending | Live 100/30-minute baseline, 200 within five minutes and 30-minute sustain |
| Speech/greeting/handover latency and admission | Pending | Approved live SC-002/SC-003 samples, not HTTP means or virtual timing |
| Planned-removal preservation | Pending | Actual persistent deletion guard, UID-bound retirement, zero planned disconnects |
| Monitoring/firewall/egress storage budgets | Pending | Approved caps, actual ingestion/egress, retention and on-call delivery |

The local coordinator requires explicit slots/aggregate/model/pending limits and retains
conservative reservations on ambiguous writes. Reducing quota blocks new demand rather than
dropping existing calls. Runtime resources are instance-local; global consensus, controller HA
and restart reconciliation are not established. See [scaling](runbooks/scaling.md).

Measure only after separately authorized T099 qualification and live T100/T118 execution.
Record approved environment/image/review references and safe aggregate results without caller
payloads, credentials or invented prices. Missing measurements block release but do not prevent
local deterministic development. [Live environment status](acceptance/live-environment.md) and
[release gates](acceptance/release-gates.md) remain the authoritative pending classifications.
