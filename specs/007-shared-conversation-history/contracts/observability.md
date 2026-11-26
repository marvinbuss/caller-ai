# Contract: Observability events

**Owner**: runtime `observability.py` (closed sets). Documented in `docs/runbooks/monitoring.md` in the same change.

| Operation | Outcome | Reasons | Fields | Emitted when |
|-----------|---------|---------|--------|--------------|
| `test.handover` | success | `applied` | `source_role`, `destination_role`, `path` | A handover was applied |
| `test.handover` | failure | `refused_graph`, `refused_stale`, `refused_budget`, `refused_step_limit` | `source_role`, `destination_role`, `path` | A handover was refused at apply time |
| `test.delegation` | success / failure | `ready`, `invalid_input` (unchanged) | new: `first_role`, `handled_role`, `handed_on` (bool), `untranscribed` (bool) | A voice delegation's work item ended |
| `test.history` | success | `ready` | `history_kib` (range) | At every applied handover |
| `test.history` | success / failure | `condensed` / `limit` | `history_kib` (range) | Condensing ran / the limit ended the conversation |
| `test.latency` | success | `ready` | measurement `handover_seconds`, field `path` | From the handoff call to the destination's first output (SC-005) |
| `test.latency` | success | `ready` | measurement `transcription_wait_seconds` | The actual transcription wait of a voice delegation (R9, probe P4) |

**Closed sets**:

| Field | Values |
|-------|--------|
| Roles | `welcome`, `routing`, `goodbye`, `ordinary` |
| `path` | `peer`, `direct`, `routing`, `lifecycle` |
| `history_kib` | `<16`, `16-64`, `64-128`, `128-192`, `>=192` |

All events also carry the mode, logged as `kind` (`text` or `voice`), and the reason as `reason_code`, like the existing events. No event carries messages, transcripts, tool arguments or results, instructions, descriptions or configuration (FR-018). `tests/unit/test_observability.py` asserts the closed sets and the absence of content.

## Alert: handover failures (FR-019)

```kusto
ContainerAppConsoleLogs_CL
| where Log_s has "test.handover"
| extend entry = parse_json(Log_s)
| where tostring(entry["event"]) == "test.handover"
| summarize total = count(), failed = countif(tostring(entry["outcome"]) == "failure")
| where total >= 10 and todouble(failed) / total > 0.2
| summarize Count = count()
```

- **Threshold**: more than 20 % failures with at least 10 handovers, in the monitoring module's 15-minute window, evaluated every 5 minutes (alert when `Count > 0`).
- **Owner**: the runtime on-call.
- **Runbook**: `docs/runbooks/monitoring.md#handover-failures`.
- **Delivery**: through the existing action group, as a query alert in `code/infra/terraform/modules/monitoring/main.tf` (`local.query_alerts`).

## SC-005 query

```kusto
ContainerAppConsoleLogs_CL
| where Log_s has "handover_seconds"
| extend entry = parse_json(Log_s)
| where tostring(entry["measurement"]) == "handover_seconds"
| summarize p50 = percentile(todouble(entry["value"]), 50),
    p90 = percentile(todouble(entry["value"]), 90), n = count()
    by path = tostring(entry["path"]), mode = tostring(entry["kind"])
```

Results are recorded in `docs/capacity.md`. The p50 and p90 have no pass threshold in this feature. `kind` is a reserved word in Log Analytics, so the queries name the mode `mode`.

## SC-005 target check

The work budget is enforced, so an answer after a handover that does not start within it ends the work with `work_deadline_exceeded`, or the handover is refused with `refused_budget`. The target is met when no acceptance run ends with `work_deadline_exceeded` (shown on the Test page) and this query returns 0:

```kusto
ContainerAppConsoleLogs_CL
| where Log_s has "test.handover"
| extend entry = parse_json(Log_s)
| where tostring(entry["event"]) == "test.handover" and tostring(entry["reason_code"]) == "refused_budget"
| summarize refused_budget = count()
```

The load tests (T078) assert the same with test doubles.

## Handover outcomes (FR-019)

Handover outcomes by mode, outcome, reason and path, in 5-minute bins:

```kusto
ContainerAppConsoleLogs_CL
| where Log_s has "test.handover"
| extend entry = parse_json(Log_s)
| where tostring(entry["event"]) == "test.handover"
| summarize handovers = count() by bin(TimeGenerated, 5m), mode = tostring(entry["kind"]),
    outcome = tostring(entry["outcome"]), reason = tostring(entry["reason_code"]),
    path = tostring(entry["path"])
```

## SC-009 check

After the acceptance runs, the event counts match the handovers and voice delegations recorded in `checklists/review.md`, and `max_delay_s` is at most 300:

```kusto
ContainerAppConsoleLogs_CL
| where Log_s has "test.handover" or Log_s has "test.delegation"
| extend entry = parse_json(Log_s)
| where tostring(entry["event"]) in ("test.handover", "test.delegation")
| extend delay_s = datetime_diff("second", ingestion_time(), todatetime(entry["timestamp"]))
| summarize events = count(), max_delay_s = max(delay_s)
    by event = tostring(entry["event"]), mode = tostring(entry["kind"])
```

## P4: transcription wait

After the first deployment and at least 20 voice delegations (R9, R15):

```kusto
ContainerAppConsoleLogs_CL
| where Log_s has "test.delegation" or Log_s has "transcription_wait_seconds"
| extend entry = parse_json(Log_s)
| extend wait = iff(tostring(entry["measurement"]) == "transcription_wait_seconds",
    todouble(entry["value"]), real(null))
| summarize delegations = countif(tostring(entry["event"]) == "test.delegation"),
    untranscribed = countif(tostring(entry["event"]) == "test.delegation"
        and tostring(entry["untranscribed"]) == "true"),
    wait_p50 = percentile(wait, 50), wait_p90 = percentile(wait, 90)
| extend untranscribed_share = todouble(untranscribed) / delegations
```

If `untranscribed_share` is above 0.1, the operator raises `CALLER_TRANSCRIPTION_WAIT_SECONDS` within 0–2 s.
