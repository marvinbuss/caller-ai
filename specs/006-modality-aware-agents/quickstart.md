# Quickstart: Validating Modality-Aware Agent Interaction

**Feature**: [spec.md](spec.md) | **Contracts**: [voice-live](contracts/voice-live.md), [text-endpoint](contracts/text-endpoint.md), [configuration-v2.2](contracts/configuration-v2.2.md), [test-console](contracts/test-console.md), [observability](contracts/observability.md)

## Prerequisites

- Python 3.12 virtual environments in `code/backend` and `code/management` (`uv sync --locked`).
- Load the package feeds in the same command:

  ```powershell
  . code\.local\feeds.ps1; Remove-Item Env:UV_DEFAULT_INDEX -ErrorAction SilentlyContinue
  ```
- Node on the PATH (for the Reflex build in the management tests).
- For the int checks: an editor account on `int`, and access to the int Log Analytics workspace.

## 1. Automated checks (every build)

### Backend

Run in `code/backend`:

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m ruff format --check src tests; .\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src
```

The tests that cover this feature:

| Area | What it checks | Requirements |
|------|----------------|--------------|
| `tests/contract/test_gpt_live.py` | `session.input` stays `[]`; greeting, context note and quiet handover appends are at most 500 bytes | FR-001, FR-016, FR-038 |
| `tests/unit/test_greeting.py` | The voice greeting is requested from the Welcome agent after ready, sent as commentary, once per session, cancelled when the caller speaks first or when no text has arrived by 5 s; `greeting_seconds` runs to the first greeting audio | FR-001, FR-004, FR-005 |
| `tests/unit/test_speech_parts.py` | Splitting by sentence, then word, then code point; every part at most 500 bytes; order kept; interruption drops the rest and sends one quiet note | FR-008 |
| `tests/unit/test_conversation.py` | No failure line for long replies; greeting history entry; slice 2 progress tool: states, stale drop, rate limit, history entries, fallback line only without a final state | FR-008, FR-021–FR-025, FR-039, FR-040 |
| `tests/contract/test_text_endpoint.py` | Open request → greeting stream; fallback after 4 s with a slow fake; `text` without `conversationId` → no greeting; `conversationId` without `text` refused; `progress` events | FR-002, FR-003, FR-039 |
| `tests/unit/test_observability.py` | The new operations and reasons are closed sets with no content | FR-035 |
| `tests/unit/test_agent_composition.py` (slice 2) | Layer order; the speaking model never receives layers; limits | FR-028, FR-033 |
| `tests/load/test_greeting_capacity.py` | 5 concurrent voice sessions and 5 concurrent text conversations at the declared slot limit: greetings within 5 s, voice cancellation at 5 s, text fallback at 4 s, no extra slot hold or admission refusal | FR-001–FR-003, SC-001, SC-002, Constitution VI |

### Management

Run in `code/management`, then restore the lock file:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit tests/contract tests/integration -q -p no:cacheprovider -m "not browser" --deselect tests/integration/test_foundation_recovery.py
git checkout -- code/management/reflex.lock
```

The tests that cover this feature:

| Area | What it checks | Requirements |
|------|----------------|--------------|
| `tests/unit/test_formatted_text.py` | Markdown → tree: allowlisted nodes only; raw HTML stays literal text; size and depth limits fall back to plain text | FR-010, FR-011 |
| `tests/unit/test_default_prompts.py` | Defaults are non-empty and follow the published voice structure headings; the runtime slice 1 copy equals the management default | FR-013, FR-014 |
| `tests/integration/test_apply_default_prompts.py` | One revision is published; a second run publishes nothing; other fields are unchanged | FR-013 |
| `tests/integration/test_configuration_persistence.py` | Revisions from earlier releases (2.0.0, 2.1.0) load, are verified twice, projected and re-published with 2.2.0 fields; absent fields are never written | FR-034, SC-008 |
| `tests/contract/test_configuration_api.py` (slice 2) | `configuration_text_too_large`, `platform_rules_unconfirmed`, editor-only fields | FR-029, FR-030, FR-033, FR-037 |
| `tests/unit/test_text_relay.py` | The open request is sent first on ready; `text.formatted` comes after `text.completed`; `progress` is relayed | FR-002, FR-019 |

### Browser

```powershell
.\.venv\Scripts\python.exe -m pytest tests/browser -q -m browser -k "test_console"
```

These tests check that:

- formatted rows render from the tree;
- `javascript:`, `data:` and `http:` links and images are not clickable or loaded;
- `https` links open in a new tab;
- status rows carry the `Status:` prefix inside the polite log;
- greeting rows appear before the first user row.

Requirements: FR-010, FR-011, FR-019, FR-041.

## 2. Deploying slice 1 to int

1. Publish the runtime and management images and pin both tags in `code/infra/environments/int/int.tfvars.json`. Run the protected `terraform-deploy` for `int`.
2. Run the one-time default prompts command once, as described in the configuration runbook:

   ```powershell
   az containerapp exec -n ca-caller-int-management -g rg-caller-int-apps --command "/app/.venv/bin/python -m management_app.services.apply_default_prompts"
   ```

   Expected output: `published revision N` on the first run, `already current` on a second run.

## 3. Manual Test-page review (SC-005, SC-009)

Run each of the six scenarios once by voice and once by text on the int Test page:

1. Greeting.
2. Routing to a custom community.
3. A tool or MCP call with progress.
4. A failing call.
5. A long, structured answer.
6. Goodbye.

Record for each pair:

- whether the substance matches and the form suits the modality;
- whether the final state matches the outcome;
- whether a duplicate acknowledgement was heard.

Pass: 6 of 6 pairs consistent, 6 of 6 correct final states, and at least 5 of 6 voice runs without a duplicate acknowledgement.

## 4. Voice greeting rate on int (SC-001)

1. Start at least 20 voice test sessions without speaking. Stop each one after about 10 s.
2. Run the query in [contracts/observability.md](contracts/observability.md#sc-001-query-int-log-analytics).

Pass: `total >= 20` and `rate >= 0.9`.

## 5. Slice 2 editor checks (SC-007)

1. Change the text global prompt to "Answer in bullet points." and a custom agent's voice addition, then publish.
2. Start a new text session and a new voice session.

Expected:

- Only the text answers follow the bullet-point rule.
- Only that agent's voice answers follow its addition.
- Restoring defaults and publishing returns the earlier behavior.
