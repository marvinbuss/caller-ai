# Contract: Observability events

**Owner**: runtime `observability.py` (closed sets); management `observability.py` for relay events. Documented in `docs/runbooks/monitoring.md` in the same change.

| Operation | Outcome | Reasons | Measurement | Emitted when |
|-----------|---------|---------|-------------|--------------|
| `test.greeting` | success | `ready` | `greeting_seconds` | The first greeting output (voice: first output audio or transcript delta; text: first text delta) arrives within 5 s of ready |
| `test.greeting` | success | `fallback` | `greeting_seconds` | Text only: the fixed fallback greeting was sent |
| `test.greeting` | failure | `deadline` | `greeting_seconds` (time of first audio, or 5.0 if none) | Voice: no greeting audio within 5 s; the request is cancelled. Text: the greeting arrived late |
| `test.greeting` | success | `skipped` | none | Voice only: the caller spoke or a delegation arrived before the greeting (FR-004) |
| `test.speech_parts` | success | `ready` | `parts`, `dropped_parts` | A spoken reply needed more than one part, or parts were dropped |
| `test.progress` | success | `still_working`, `waiting_for_input`, `completed`, `failed`, `cancellation_confirmed` | none | A progress update was delivered (slice 2) |
| `test.progress` | failure | `rate_limited`, `stale` | none | An update was suppressed (FR-024, FR-025) |
| `test.progress` | failure | `fallback_line` | none | The fixed failure line was used (FR-023) |

All events also carry `mode` (`text` or `speech`). No event carries greeting, answer, note or progress text (FR-035).

## SC-001 query (int, Log Analytics)

```kusto
ContainerAppConsoleLogs_CL
| where Log_s has "test.greeting"
| extend entry = parse_json(Log_s)
| where tostring(entry.event) == "test.greeting" and tostring(entry.kind) == "voice"
    and tostring(entry.reason_code) != "skipped"
| summarize total = count(),
    on_time = countif(tostring(entry.outcome) == "success" and todouble(entry.value) <= 5.0)
| extend rate = todouble(on_time) / total
```

Pass condition: `total >= 20` and `rate >= 0.9`. The column names were confirmed against the structured event schema during implementation: events are JSON lines in `ContainerAppConsoleLogs_CL.Log_s` with `event`, `outcome`, `reason_code`, `kind` (`voice` or `text`), `measurement` and `value` (a string).
