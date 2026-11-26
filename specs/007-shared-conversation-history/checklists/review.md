# Release Review: Shared Conversation History Across Agents

**Purpose**: Record the acceptance runs on int that close the feature (T087, T088, T089)
**Feature**: [spec.md](../spec.md)
**Environment**: `int`. Runs of record on 2026-11-26 with images `sha-54b2201c28cc55b243fc58db49c68f643193d847` (feature 007 with #58–#64), deployed by the owner at 09:04 UTC; first attempts on 2026-11-25 with earlier images.

## Synthetic configuration

Published by the owner on 2026-11-25 as revision 7, per the spec's [Acceptance Scenario Set](../spec.md#acceptance-scenario-set). "Announce agent changes" off; shipped platform rules.

| Community | Routable | Entry agent | Second agent | Connections |
|-----------|----------|-------------|--------------|-------------|
| Billing | Yes | Billing Desk | Order Status (link from Billing Desk) | Direct handover to Technical Support |
| Technical Support | Yes | Tech Desk | Device Repair (link from Tech Desk) | None |
| Archive | No | Archive Desk | | None |

Tool for A6: none. int has no reviewed tool, so A6 (tool result reused after a handover) cannot run there. It is covered only by the automated tests of feature 007 (tool calls and results kept in the shared history and rendered to the next agent).

## Acceptance runs (T087, SC-001, SC-002, SC-003, SC-011)

Run by the owner, per [quickstart.md](../quickstart.md#3-acceptance-set-on-int-a1a9-sc-001sc-003). The runs of record are those of 2026-11-26; the first attempts of 2026-11-25 (A1 and A2) found the voice delegation and held-slot defects fixed in #58–#61 and are not counted. Context use, quietness and the end state are the owner's observations. Paths and roles are taken from `test.handover` events, which name roles and paths but not agents; the agent reached is the owner's observation. Times are UTC.

| ID | Mode | Expected destination | Destination reached (path) | Context used without asking | Quiet (no announcement; entry agent and Routing only clarify) | Ended without `work_deadline_exceeded` |
|----|------|----------------------|----------------------------|-----------------------------|-----------------------------------------------------------------|----------------------------------------|
| A1 | Voice | Order Status | Order Status (routing, peer), 09:09; Routing asked once to clarify the opening sentence | Yes | Yes | Yes |
| A1 | Text | Order Status | Order Status (routing, peer), 11:38. A repeat at 11:44 stayed with Billing Desk, which asked again instead of handing over; the owner counts the 11:38 run | Yes | Yes | Yes |
| A2 | Voice | Tech Desk (direct) | Tech Desk (routing, direct), 09:11 | Yes | Yes | Yes |
| A2 | Text | Tech Desk (direct) | Tech Desk (routing, direct), 11:45 | Yes | Yes | Yes |
| A3 | Voice | Billing Desk (routing) | Billing (routing, direct, routing back to Billing), 09:12; GPT-Live did not confirm the close at Stop, the slot was released by recovery at 09:21 (#65) | Yes | Yes | Yes |
| A3 | Text | Billing Desk (routing) | Billing (routing, direct, routing back to Billing), 11:46 | Yes | Yes | Yes |
| A4 | Voice | The agent handling the delegation | Billing (routing), 09:40; the handover took 6.9 s. An attempt at 09:39 got two clarification requests and was stopped | Yes | Yes | Yes |
| A4 | Text | The agent after Routing's question | Billing (Routing asked first, then routing), 11:47 | Yes | Yes | Yes |
| A5 | Voice | The next agent that answers | Billing (routing), 09:44; the same agent answered after the interruption; Goodbye took over when the call ended | Yes | Yes | Yes |
| A5 | Text | The next agent that answers | Billing (routing), 11:48; the same agent answered the follow-up | Yes | Yes | Yes |
| A6 | Voice | Order Status, lookup not repeated | Not run: no reviewed tool on int (see above) | | | |
| A6 | Text | Order Status, lookup not repeated | Not run: no reviewed tool on int (see above) | | | |
| A7 | Voice | No handover; explanation | No handover (Routing explained), 09:48 | Yes | Yes | Yes |
| A7 | Text | No handover; explanation | No handover (Routing explained), 11:49 | Yes | Yes | Yes |
| A8 | Voice | Tech Desk, not Billing | Technical Support (routing, then peer), 09:50; Routing asked three times to clarify the invoice opening; the slot was held after Stop and released at 11:30 (#65) | Yes | Yes | Yes |
| A8 | Text | Tech Desk, not Billing | Tech Desk (routing, direct), 11:50 | Yes | Yes | Yes |
| A9 | Voice | Order Status, corrected fact | Order Status (routing, peer), 11:36; Routing asked twice to clarify first | Yes | Yes | Yes |
| A9 | Text | Order Status, corrected fact | Order Status (routing, peer), 11:51 | Yes | Yes | Yes |

| Criterion | Target | Result |
|-----------|--------|--------|
| SC-001 expected destination | 18 of 18 | 16 of 16 runs; A6 not run on int (no reviewed tool), covered by the automated tests |
| SC-002 listed context used | 18 of 18 | 16 of 16 runs |
| SC-003 voice and text pairs match | 9 of 9 | 8 of 8 pairs; in A8 voice reached Technical Support through Routing, text through the direct connection |
| SC-011 quiet runs (best effort) | at least 16 of 18 | 16 of 16 runs |
| A7 no handover and an explanation | both modes | Both modes |

**Result**: Pass for the 16 runs possible on int. A6 needs a reviewed tool on int and stays covered by the automated tests only. Two voice sessions (A3, A8) left their slot held after Stop because GPT-Live did not confirm the close; #65 releases such slots automatically and logs why.

## Measurements (T086, T088, SC-005, SC-009)

Queries from [observability.md](../contracts/observability.md); results also recorded in `docs/capacity.md` and, for P4, under R15 in [research.md](../research.md).

| Check | Result |
|-------|--------|
| SC-005 target: no `work_deadline_exceeded`, `refused_budget` = 0 | Met: 0 `work_deadline_exceeded`, 0 `refused_budget`, no refused handover, no model or work failure (2026-11-26 09:04–12:00) |
| SC-009: events match the runs, `max_delay_s` at most 300 | Met: 36 voice and 31 text handovers and 32 voice delegations (the runs of record plus the attempts and short starts listed above), all applied; `max_delay_s` 226 (voice handover), 194 (delegation), 47 (text) |
| P4: voice delegations, untranscribed share, wait p50 and p90 | First measurement, 2026-11-25 14:48–14:57 with the 0.5 s default: 11 delegations, 7 untranscribed (64 %). Second, 2026-11-26 09:04–12:00: 32 delegations, 18 untranscribed (56 %), wait p50 0.50 s and p90 0.50 s, because the 2 s wait of #62 is not yet applied on int (the images were deployed without `terraform-app-environment`). 10 delegations were sent back for clarification, 7 of them untranscribed. Above the 10 % threshold: the 2 s wait stays and is measured again after the next apply. Third, 2026-11-26 16:55–17:50 with the 2 s wait: 42 delegations, 17 untranscribed (40 %), wait p50 0.00 s and p90 2.00 s. No wait on int ever ended by catching up (94 waits), so int now uses 0 s ([R15](../research.md#p4-results-int-t086)) |

## Alert delivery (T089, FR-019)

| Check | Result |
|-------|--------|
| Temporary condition (any `test.handover`) fired after one A1 run | Yes: with the owner's approval the condition was changed at 12:07 UTC; after the owner's A1 text run the alert fired at 12:42, 12:47 and 12:52 UTC (one instance per 5-minute evaluation) |
| Notification reached the action group and links to the runbook | The fired instances show the action group `ag-caller-int` run (actions not suppressed); the rule's description links to the monitoring runbook. The email itself was not checked: the operations mailbox is not set up yet, and the owner accepted the check without it |
| Condition restored | Yes, at 13:21 UTC from the saved rule; query, threshold, window, frequency and action group equal the original |
