# US1 Local Journey Evidence

## Decision

Provider-neutral local media, cleanup and metadata audit components are implemented under the
user's explicit **Proceed local-only; keep external gates** authorization on 2026-10-26.
This does not qualify a phone provider or approve its wire protocol, real credentials,
environment access, calling, deployment or publishing. T042 and live T099/T118 remain open.
Default application business routes/capabilities remain unavailable.

The existing callback/media contracts now execute instead of failing on a missing module.
Additional tests cover competing sockets, decoded size/duration, obsolete output epochs,
audio-scope disposal and original failure budgets. Cleanup is integrated into the existing
CallLifecycle rather than merely defined beside it. Runtime audit models/repositories enforce
metadata-only UTC day-partitioned records and reject invalid model-copy persistence.

The actual local lifecycle journey runs **200 synthetic sequences** with one pinned revision,
Welcome first, Routing/ordinary entry, Goodbye last, one hangup/terminal result, immediate
context purge and 200 safe audit records. Cancellation disposal retains no owned mutable audio
or conversation references. Management's rejected-credential privacy case proves no partial
revision/audit persistence. Focused test results and full regression classification are recorded
in [release gates](release-gates.md), not represented as a live pass here.

## Separate Live Profile: Not Executed

The authorized live profile requires a supported provider/scenario, actual bidirectional
codec/control behavior, Azure model/identity/quotas, private metadata access and accountable
privacy/disclosure/residency/retention approval, validated at T099. Later T118 execution must
measure at least 99% of 200 eligible actual journeys, Welcome-first/normal Goodbye-last,
one-second interruption, phone closure within five seconds after Goodbye, bounded failure
and no sensitive trace/audit export. Each missing prerequisite is blocked, not skipped/passed.

No live executor, automatic provisioning, phone number, provider selection, real model call or
destructive cleanup is included in this local profile. A fake codec name, unit sample duration
or typed provider port cannot satisfy these obligations. Logical disposal is not a provider/
backup physical-erasure guarantee. Read the call runbook (retired with feature 003) and
[pending environment record](live-environment.md).
