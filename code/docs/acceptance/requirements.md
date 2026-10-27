# Requirement Evidence Matrix

This matrix covers all FR-001 through FR-034 and SC-001 through SC-019. **No row grants release
acceptance.** Local component tests are distinguished from pending integrated/deployed proof;
the latest combined regression passes 384 runtime/129 tools cases but management has 403 cases
with 24 editor-export setup errors. Earlier all-green local results remain historical.
The machine inventory keeps all entries
`pending_qualification` except explicitly `deferred` SC-008. Read [release gates](release-gates.md).

| Requirement | Local evidence/source | Remaining qualification |
|-------------|-----------------------|-------------------------|
| FR-001 two-way call/interruption | [Call lifecycle](us1.md), injected media/cleanup contracts | Qualified provider/model speech and integrated live media |
| FR-002 single session/owner/revision | [Calls](us1.md), [scaling](us6.md), conditional metadata tests | Live ownership, restart and multi-worker behavior |
| FR-003 protected roles | [Management](us2.md), schema/bootstrap/validation tests | Deployed management/storage enforcement |
| FR-004 Welcome/Routing/Goodbye | 200 synthetic journeys in [US1](us1.md) | Eligible actual call journeys/phone closure |
| FR-005 membership/entry | [Management](us2.md), [visibility](us3.md) | Live approved revisions |
| FR-006 foreign-agent denial | [US3](us3.md), guessed-target tests | Integrated deployed negative scenarios |
| FR-007 shared lifecycle roles | [US2](us2.md), [US3](us3.md) | Live orchestration and role review |
| FR-008 allowed transfer paths | [US3](us3.md), Routing/relationship validation | Qualified media/handover composition |
| FR-009 volatile approved context | [US3](us3.md), transfer filtering/privacy | Live source/destination data handling |
| FR-010 committed owner epoch | [US3](us3.md), conditional commit/race tests | Actual persistence and phone continuity |
| FR-011 handover rate limit | [US3](us3.md), rolling-window tests | Live clarification/closure behavior |
| FR-012 bounded interruption/failure | [US1](us1.md), [US6](us6.md), deadline/cancellation tests | Actual phone/model timing |
| FR-013 manager CRUD | [US2](us2.md), API contracts and historical editor passes | Current full editor repair, deployed identity/storage and second-platform evidence |
| FR-014 public tool/MCP catalogs | [US5](us5.md), reviewed immutable definitions | Actual reviewed external connectors |
| FR-015 durable complete revisions | [US4](us4.md), local/Cosmos-double atomic writes | Real Cosmos restart/durability |
| FR-016 validated references | [US2](us2.md), [US4](us4.md), strict graph/cache tests | Deployed rejected-save guarantees |
| FR-017 stale manager conflict | [US2](us2.md), [US4](us4.md), conditional saves | Live concurrent persistence |
| FR-018 new-call refresh and old pins | Twenty virtual changes and SDK pin tests in [US4](us4.md) | Measured deployed 60-second propagation |
| FR-019 immediate revocations | [US5](us5.md), [US4](us4.md), fresh gateway ordering | Live current authority/partition behavior |
| FR-020 management auth | [US2](us2.md), token/session/CSRF tests | Actual Entra/app-role/session delivery |
| FR-021 per-action policy | [US5](us5.md), epoch and destination/gateway tests | Real reviewed adapters and effective egress |
| FR-022 secret references/detection | [Security](../security.md), [US2](us2.md), audit privacy tests | Actual approved registry/retention review |
| FR-023 isolation and disposal | [US1](us1.md), SDK/session/cleanup privacy tests | Live provider retention and full owned-content paths |
| FR-024 metadata-only audit | [US1](us1.md), [US2](us2.md), strict audit repositories | Actual access/retention/recovery |
| FR-025 responsive/keyboard management | [US2](us2.md), historical native browser/editor passes | Current full editor repair, second-platform and deployment qualification |
| FR-026 call-preserving scaling | [US6](us6.md), guarded drain doubles | Persistent deployed guards and live zero-disconnect profile |
| FR-027 bounded admission/quota | [US6](us6.md), conservative capacity tests | Actual quotas/cost/global authority |
| FR-028 telemetry and traces | [Monitoring](../runbooks/monitoring.md), SDK safe opt-in/export tests | Qualified cloud collection/alerts and real speech signals |
| FR-029 guidance/runbooks | [Runbook index](../runbooks/README.md) | Operator review in qualified environment |
| FR-030 foundation/CI | [Foundation](foundation.md), local health/image/failure and full regression | Observed remote CI and native second-platform evidence |
| FR-031 no-cloud local startup | [Foundation](foundation.md), blocked-egress/offline images | Native second-platform observation |
| FR-032 Windows/Linux support | Native Windows and real Linux images in [foundation](foundation.md) | Actual native Linux host/remote jobs |
| FR-033 one organization | [US2](us2.md), fixed default partition/schema | Approved deployed organization boundaries |
| FR-034 public read-only only | [US5](us5.md), assigned forbidden-action tests | Real reviewed connectors and live explanations |
| SC-001 eligible journeys | 200 synthetic sequences in [US1](us1.md) | At least 99% of 200 eligible actual journeys |
| SC-002 baseline capacity | [US6](us6.md), virtual/profile guards | 100 actual calls/30 minutes and measured service targets |
| SC-003 scale to 200 | [US6](us6.md), stable virtual owners | Five-minute scale/live sustain/zero planned disconnects |
| SC-004 speech handover p95 | [US3](us3.md), real SDK scripted models | Actual resumed-speech p95, not control or HTTP timing |
| SC-005 prohibited disclosure/action | [US3](us3.md), [US5](us5.md), local negative tests | Integrated deployed negative profile |
| SC-006 invalid edits/protected roles | [US2](us2.md), [US4](us4.md), atomic rejection | Effective live storage/identity |
| SC-007 twenty updates/revocations | [US4](us4.md), virtual cadence and fresh admission | Measured deployed 60-second visibility |
| SC-008 participant study | Explicitly deferred | Not passed; no recruitment or invented threshold |
| SC-009 responsive/keyboard tasks | [US2](us2.md), historical browser assertions | Current full editor repair, second-platform and deployment qualification |
| SC-010 outage/overload/failure | [US1](us1.md), [US5](us5.md), [US6](us6.md) | Actual provider/model/storage/owner failure timing |
| SC-011 interruption/closure | Cleanup/lifecycle tests in [US1](us1.md) | Actual one-second clear and phone acknowledgment |
| SC-012 durable restart | [US2](us2.md), [US4](us4.md), injected Cosmos profiles | Authorized real persistence/restart execution |
| SC-013 content cleanup/privacy | [US1](us1.md), terminal/cancel disposal and audit tests | Real provider/owned-buffer/retention review |
| SC-014 operator diagnosis | Fixed telemetry and [runbooks](../runbooks/README.md) | Ten approved operator cases within five minutes |
| SC-015 local foundations | [Foundation](foundation.md), real health/shell/images/full regression | Native second-platform/remote evidence |
| SC-016 cross-platform setup | Native Windows/local Linux images | Actual native second-host execution |
| SC-017 CI failure signal | [Assertion gate](ci-failure-gate.md), local 0/1/0 | Observed remote assertion failure and restoration |
| SC-018 permissions/management | [US2](us2.md), auth/concurrency and full editor regression | Deployed authority and native second-platform evidence |
| SC-019 no private/mutating tools | [US5](us5.md), current gateway denials | Integrated reviewed live connectors |

The runner is intentionally not an automatic acceptance oracle. Raw JUnit and candidate
payloads are temporary; only bounded counts/categories enter its report. Qualification and
operator evidence must be separately reviewed and cannot be self-certified by a manifest.
