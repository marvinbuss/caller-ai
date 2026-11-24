# Quickstart: Validating shared conversation history

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

## 0. Prerequisites

- A working copy with the local environment from `docs/setup.md` (`.venv` per application; `code/.local/feeds.ps1` for the package feeds).
- For int steps: owner approval, a deployed build of this feature, and Log Analytics access as in `docs/runbooks/monitoring.md`.
- The synthetic acceptance configuration from the spec:
  - Billing and Technical Support, both routable, each with two agents; each community's first agent is its entry agent;
  - one community that is not routable;
  - a relationship between Billing's two agents;
  - a connection from Billing to Technical Support only.

  It is published through the editor before the runs.

## 1. Probes (int, synthetic content)

P1–P3 run before implementation; P4 runs after the first deployment. The int model account accepts only private network traffic, so P1–P3 run inside the int network: with owner approval, copy `code/tools/scripts/probes/*.py` into the int runtime container (`az containerapp exec -n ca-caller-int-runtime -g <group> --command sh`) and run them there with `/app/.venv/bin/python`, which has the runtime's settings and managed identity. Remove the files afterwards. Elsewhere, run them from the repository root with `code\backend\.venv\Scripts\python.exe`, signed in with `az login` to an account that can call the model deployments, and with the runtime's `CALLER_LIVE_ENDPOINT`, `CALLER_LIVE_VOICE`, `CALLER_TEXT_ENDPOINT` and `CALLER_TEXT_DEPLOYMENT` set. `--preview` prints the planned calls without any network call, and P1's `--control` runs without the update as a baseline. Record each result under R15 in [research.md](research.md):

| Probe | Command | Expected record |
|-------|---------|-----------------|
| P1 | `code\backend\.venv\Scripts\python.exe code\tools\scripts\probes\live_instructions_append.py` | Whether an append during speech cut the output transcript → default for `CALLER_VOICE_DELEGATION_UPDATES` |
| P2 | `... probes\reasoning_minimal.py` | `minimal` accepted with tools → default for `CALLER_REASONING_EFFORT` |
| P3 | `... probes\replay_foreign_items.py` | Replayed function and handoff items of another agent accepted with `store=False` → native rendering or the text fallback |
| P4 | After the first deployment: the [P4 query](contracts/observability.md#p4-transcription-wait) in Log Analytics | Share of delegations that ended the wait untranscribed, and the actual wait; raise `CALLER_TRANSCRIPTION_WAIT_SECONDS` if the share is above 10 % |

## 2. Automated tests (CI)

Runtime (`code/backend`): `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider`

| Test file | Covers | Requirements |
|-----------|--------|--------------|
| `tests/unit/test_shared_history.py` | Recording and rendering of every entry kind; order by `at_ms`; no instructions, layers, tool definitions or reasoning items; untrusted text stays content | FR-001–FR-006, SC-004 |
| `tests/unit/test_handover_rules.py` | Destinations per role from the graph; descriptions; Routing `community_id` enum; pending-destination `is_enabled` | FR-008, FR-027–FR-029 |
| `tests/unit/test_conversation.py` (handover cases) | SDK handoff applied with checks; refusal at apply time reported to the agent; chain within budget and step limit; full history after handover; Welcome → Routing after the greeting | FR-007–FR-011, FR-030 |
| `tests/unit/test_condensing.py` | Trigger at 192 KiB or 2,048 entries; kept entries; `condensed` entry; end at 256 KiB | FR-016, SC-006 |
| `tests/unit/test_delegation_update.py` | Update text, "replaces" sentence, capability lines without community names, 2,000-byte cap and cut rule, parts, sent only on change; static block when updates are off | FR-013 |
| `tests/integration/test_voice_continuous_session.py` | One Live session across agent changes; no generation change during the test; stale results dropped by epoch; reproductions of the T095–T098 incidents pass | FR-010, FR-013 |
| `tests/integration/test_voice_delegation_context.py` | Transcription wait (catch-up and timeout); `partial` marking; history-aware clarity check accepts "and the second one?"; A9 narrowing and correction reach the second agent with scripted model doubles | FR-003, FR-012, FR-023, FR-024, SC-010 |
| `tests/integration/test_voice_interruption_record.py` | Interrupted answer: transcript up to the interruption, result kept apart, unsent parts `not_delivered` | FR-004, SC-007 |
| `tests/contract/test_text_endpoint.py` | v2 save and resume with tool entries; pre-upgrade document → new conversation; `history.condensed`; limit error | FR-015, FR-025, SC-008 |
| `tests/unit/test_configuration_cache.py` | 2.0.0–2.3.0 load; description and `announceAgentChanges`; context fields ignored; connections as direct paths | FR-021, FR-029, SC-008 |
| `tests/unit/test_observability.py` | `test.handover`, `test.history`, delegation fields, `handover_seconds` closed sets; no content | FR-018, SC-009 |
| `tests/load/test_handover_capacity.py` | 5 concurrent voice sessions with agent changes, refusals, condensing and slow transcripts at the slot limit; no extra refusals; slots released | Constitution VI |

The existing feature 006 tests (greeting, spoken parts, progress, formatted text, layers) must still pass unchanged, apart from the replaced context note (FR-022).

Management (`code/management`): `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --deselect tests/integration/test_foundation_recovery.py`, then `git checkout -- code/management/reflex.lock`.

| Test file | Covers | Requirements |
|-----------|--------|--------------|
| `tests/contract/test_configuration_api.py` | 2.3.0 written only with new fields; description limit; editor-only visibility | FR-021, FR-026, FR-029 |
| `tests/unit/test_default_prompts.py` | Platform rules contain the handover rules, byte-equal with the runtime copy | FR-028 |
| `tests/browser/test_configuration_editor.py` | Agent description field; context-field checkboxes gone; connection label | FR-021, FR-029 |
| `tests/browser/test_settings_page.py` | "Announce agent changes" off by default, publish and restore | FR-026 |
| `tests/unit/test_test_console.py`, `tests/browser/test_test_console_formatting.py`, `tests/browser/test_test_page.py` | `agent.changed` without a row; `history.condensed` status row; microphone and loudspeaker switches act locally and send nothing; custom-rules notice | FR-013, FR-016, FR-026 |

Full checks: `. .\code\.local\feeds.ps1; Remove-Item Env:UV_DEFAULT_INDEX -ErrorAction SilentlyContinue; uv run --project code/tools --locked python code/tools/scripts/ci.py check`, and `verify_docs.py`.

## 3. Acceptance set on int (A1–A9, SC-001–SC-003)

With the synthetic configuration published, run each scenario of the spec's [Acceptance Scenario Set](spec.md#acceptance-scenario-set) once by voice and once by text on the Test page (18 runs). In A5 by voice, interrupt the answer by speaking over it.

Record for each run:

- the destination reached (from `agent.changed` metadata and `test.handover` events);
- whether the context listed for the scenario was used without asking again;
- whether the voice and text runs of the pair reached the same destination and outcome;
- whether any handover was announced, and whether an entry agent or Routing spoke for anything other than resolving something unclear ("Announce agent changes" off);
- whether the run ended with `work_deadline_exceeded` (SC-005).

**Pass**:

- 18 of 18 runs reach the expected destination (SC-001);
- 18 of 18 use the listed context (SC-002);
- 9 of 9 pairs match (SC-003);
- at least 16 of 18 runs stay quiet (SC-011, best effort);
- A7 shows no handover and an explanation.

Record the results in `specs/007-shared-conversation-history/checklists/review.md`.

## 4. Measurements on int (SC-005, SC-009)

1. Within 5 minutes of the runs, every handover and voice delegation appears in Log Analytics (SC-009). Run the [SC-009 check](contracts/observability.md#sc-009-check), and check the [handover outcomes](contracts/observability.md#handover-outcomes-fr-019) by mode and reason (FR-019).
2. Check the declared SC-005 target per [observability.md](contracts/observability.md#sc-005-target-check): no run ended with `work_deadline_exceeded`, and no handover was refused with `refused_budget`. Record `handover_seconds` p50 and p90 per `path` and mode, model input size per turn (`input_tokens`) and cost per conversation in `docs/capacity.md` (SC-005).
3. Record the option A criteria from [research.md](research.md#r1-how-a-request-reaches-the-right-agent) in `docs/capacity.md`.

## 5. Alert delivery (FR-019)

With owner approval, change the alert's condition on int temporarily so that any `test.handover` event fires it (at least 1 handover, no failure needed), and run one scenario with a handover, for example A1. Confirm that the alert fires, reaches the action group and links to the runbook. Then restore the condition. A refused handover cannot be caused on purpose from the Test page; the automated tests (T063, T073) show that refusals emit the failure events the real condition counts.
