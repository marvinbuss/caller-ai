# Quickstart: Verifying the Simplification

Run from the repository root. Each project uses its own virtual environment; commands below are the validation entry points once the feature is implemented.

## 1. Endpoint surface

1. Run the backend contract tests (`code/backend/tests/contract`), which start the app with controlled doubles; no local provider-backed run exists.
2. Confirm the live routes are exactly `/healthz`, `/readyz`, `/v1/conversations/text` and `/v1/conversations/voice`.
3. Request each retired path from [contracts/conversation-endpoints.md](contracts/conversation-endpoints.md) and confirm not-found.
4. Send a `settings` value that is not empty to either endpoint and confirm `settings_not_supported`.

## 2. Management console

1. Start the management app and open the test feature.
2. Run one text turn and one voice session; both reach the backend through the public endpoints.
3. Run two text turns in one test; the second continues from the history the backend stored under the same conversation identifier.
4. Confirm the text test mode shows the notice that text test conversations are stored for 30 days and visible only to their owner.
5. Stop the history-store test double and confirm a text request fails with `history_unavailable` (nothing written, no reply) and `/readyz` reports `history_store_unavailable`; restart the double and confirm both recover.
6. Confirm the backend request equals what a plain client sends (relay tests assert this).

## 3. Code quality checks

- Backend, management and tools test suites pass in each project's environment.
- The reachability check reports no unreachable production module.
- The docstring check reports every production module has a purpose docstring.
- Documentation verification passes with updated Mermaid diagrams.

## 4. Terraform

1. Validate the three example pairs (`dev`, `test`, `prod`) through the delivery helper.
2. Run the static check that no Terraform source branches on an environment name.
3. Add a throwaway fourth pair and confirm validation passes with no source change.

No cloud apply is part of this feature; real-environment verification is a follow-up.

## 5. Tests and timing

1. Run the quick tier, then the full tier; record both durations next to the baselines in [research.md](research.md).
2. Compare test counts and step durations; record the reason for any advisory target missed.

## 6. Hooks

1. Read the hook list in the contributor documentation.
2. Run a workflow step with a hook and confirm it prompts before acting and skips cleanly when `gh` is unavailable.
