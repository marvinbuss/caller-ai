# Quickstart: Validate the Management Frontend Redesign

How to prove the feature works, with automated checks only. Contracts: [ui-shell.md](contracts/ui-shell.md), [status.md](contracts/status.md), [configuration-v2.1.md](contracts/configuration-v2.1.md). Data: [data-model.md](data-model.md). Manual checks are deferred (see the last section).

## Prerequisites

- Windows with PowerShell 7, or Linux; Python through `uv`; Node.js on `PATH` (the browser and JavaScript tests need it).
- In the mirror-feed development setup: `. .\code\.local\feeds.ps1` and then `Remove-Item Env:UV_DEFAULT_INDEX` before `uv run --locked`.

## 1. Run the checks

```powershell
uv run --project code/tools --locked python code/tools/scripts/ci.py check
uv run --directory code/backend --locked pytest tests -q
uv run --directory code/management --locked pytest tests -q
uv run --project code/tools --locked python code/tools/scripts/verify_docs.py
```

Expected: all pass, locally and in Foundation CI on both OS jobs.

## 2. What the automated tests prove

Each row is covered by browser tests against the local editor fixture (`scripts/smoke.py --editor` starts the same fixture by hand if needed) or by unit and contract tests.

| Scenario | Expected | Spec |
|----------|----------|------|
| Open any page without a session | Login page with the Caller AI logo and sign-in only; no page data requested | FR-001, SC-001 |
| Sign in with a deep link | Lands on the requested page; unsafe `returnTo` values fall back to `/` | FR-002 |
| Sign in as editor + tester, tester only, editor only, no role | Sidebar entries and page access match [ui-shell.md](contracts/ui-shell.md#routes); server refuses every forbidden call | FR-003, FR-004 |
| Tester-only user with `TESTER_ONLY_TESTS` off (default), then on (T041) | Off: Dashboard and Status only, Test page needs both roles; on: Test page available; configuration always refused | FR-003, research R11 |
| Cancel or fail the fixture sign-in (T016) | Login page with a retry message; no session created | Edge cases |
| Top bar | Mark and "Caller AI"; profile shows name, account, roles; User Settings says "coming soon" | FR-028, FR-029 |
| Session 5 minutes before expiry; choose "Stay signed in"; let another session expire | Warning shown; extension keeps the draft; expired session lands on login with the discarded-changes message | FR-006 |
| Sign-in token about to expire; choose "Stay signed in" (T017) | Drafts saved in the tab, sign-in again returns to the same page, drafts restored for the same user only, storage cleared; another user or sign-out never sees them | FR-006, research R10 |
| Community and Agent page | Welcome, Routing, Goodbye shown as Default with lock icon and label; Welcome → Routing drawn | FR-008, FR-015 |
| Delete Routing, rename Welcome, add an agent to Goodbye, delete or move a pre-defined agent | Each refused with the rule named; graph and draft unchanged | FR-009, FR-010, FR-018, SC-004 |
| Add community with two agents, link Routing → it, change entry agent, draw a handover, publish | Graph updates at once; confirmation shows the new revision; scripted flow completes within 5 minutes | FR-011 to FR-013, FR-038, SC-005 (proxy) |
| Remove the Routing link and publish | Community marked Unreachable and listed in the confirmation; publication succeeds | FR-012 |
| Drag, collapse, reload | Position and collapsed state kept in the browser; no unpublished-change marker | FR-016 |
| Select nothing, an agent, a community | No panel; agent settings with "last changed"; community settings | FR-014, FR-020 |
| Tools section | Add, assign to a custom agent, remove with confirmation naming the agents; pre-defined agents cannot receive tools | FR-037 |
| Keyboard-only walkthrough of every page, including the graph outline list | Every action reachable; focus visible | FR-032, SC-008 |
| Token contrast check; grayscale render of default vs custom nodes | All tokens meet WCAG 2.1 AA; nodes differ by shape, icon and label | FR-030, SC-007 (proxy) |
| Settings: voice drop-down | Lists the runtime's catalog grouped by language with the default marked | FR-025 |
| Settings: idle timeout 20; publish a voice change with a graph edit pending | Field refuses the value with the 30–120 range; only the settings change is published | FR-025, FR-026 |
| Status page with the fixture runtime up, then stopped | Hosts, deployment, region and reachability shown; unreachable runtime shown with last success; no secrets in the response | FR-023, FR-024, SC-009 |
| Slow and failing data loads on the Community and Agent and Status pages (T028, T047) | Loading indicator after 1 second; failing load shows an error with Retry that recovers | FR-031, edge cases |
| Start a text test, then open Status | Test stopped like an explicit stop; notice shown | FR-022 |
| Publish from a second browser context | First context shows the new-revision notice within 30 seconds; draft unchanged | FR-017, SC-011 |
| 20 communities and 100 agents | Drag, zoom and collapse feedback within 100 ms; edits visible within 1 second | SC-006 |
| Runtime with a `2.0.0` and a `2.1.0` revision | Both accepted; Routing offers routable communities only; settings applied at admission | [configuration-v2.1.md](contracts/configuration-v2.1.md) |

## 3. On an environment

After the runtime and then the management app are deployed, the pipeline's environment check runs unchanged:

```powershell
uv run --project code/tools --locked python code/tools/scripts/check_environment.py --environment int --manifest <manifest.json> --functional
```

## Deferred manual checks

Deferred to a later stage by the owner on 2026-11-11; not part of this feature's completion:

- Security review of tester-only test access ([research.md R11](research.md#r11-tester-only-access-to-dashboard-status-and-test)). Until it is recorded, `TESTER_ONLY_TESTS` stays `False` and the Test page requires both roles.
- Usability checks with managers behind SC-003, SC-005 and SC-007 (automated proxies above).
- Manual walkthrough of the pages on `int`, including comparing the Status page with the environment files.

## Rollback

Republish the last `2.0.0` revision if the runtime must be rolled back; a management rollback alone needs nothing (see [configuration-v2.1.md](contracts/configuration-v2.1.md#versions)).
