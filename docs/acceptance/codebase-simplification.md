# Feature 003 acceptance map

Status: implemented for the backend endpoints, text history, management console, GPT-Live on the
OpenAI SDK, file splits, library decisions, Terraform stage neutrality, documentation, Spec Kit
hooks, the wording pass (T054 to T057) and the test-speed work (T071 to T076, with the reviewed
decisions in `specs/003-codebase-simplification/research.md`, "Implemented and deferred"). Every
check below runs against controlled doubles; no live provider, cloud account or real environment
was used (FR-027).

| Requirement | Covering test or check |
|-------------|------------------------|
| FR-001, FR-003 | `code/backend/tests/contract/test_route_inventory.py`, `test_text_endpoint.py`, `test_voice_endpoint.py` |
| FR-002 | `test_route_inventory.py::test_retired_routes_are_not_found`; `code/tools/tests/test_static_checks.py::test_active_files_name_no_retired_endpoint_path`; `check_modules.py --strict` |
| FR-004, FR-007, FR-008 | `code/backend/tests/unit/test_connection_contract.py`; settings cases in `test_text_endpoint.py` and `test_voice_endpoint.py`; `code/management/tests/unit/test_backend_connection.py` |
| FR-005, FR-006, FR-009 | `test_backend_connection.py`; `code/management/tests/unit/test_test_console.py`; `tests/contract/test_browser_tests.py::test_owned_manager_relay_enforces_stable_actor_and_five_occupied_slots` (voice and two-turn text through the real relay and runtime) |
| FR-010 | `check_modules.py --strict` (zero unreachable modules, one reasoned allow-list entry) |
| FR-011 | `check_modules.py` purpose-docstring check; every module in the backend, management app and tools scripts has a multi-line docstring with why-comments |
| FR-012, FR-013 | `code/backend/tests/contract/test_gpt_live.py` (local-server cases for the SDK transport: Live path, bearer token, 256 KiB frame limit, cross-origin redirect refusal); `docs/library-decisions.md`; `pyproject.toml` without the `[realtime]` extra |
| FR-014 | Breaking renames recorded in `specs/003-codebase-simplification/contracts/` and `docs/conversation-endpoints.md` |
| FR-015 | Retained admission, limit, privacy and cleanup cases in `tests/unit/test_conversation.py`, `tests/unit/test_admission.py`, `tests/integration/test_conversation.py`, `test_content_privacy.py` |
| FR-016, FR-017, FR-018 | `code/tools/tests/test_static_checks.py` (stage-name check, example pairs for any stage), `test_terraform_delivery.py`, Terraform native tests via `verify_terraform.py check` |
| FR-019 | `test_terraform_delivery.py` (OIDC-only, protected environment, exact saved plan) |
| FR-020, FR-021 | `quick` tier in all three projects; the shared editor frontend build; the duplicate SDK startup test removed (see Removed tests); `docs/testing.md` |
| FR-022 | Baseline and after-values in `specs/003-codebase-simplification/research.md` |
| FR-023, FR-024, FR-025 | `code/tools/tests/test_spec_hooks.py` |
| FR-026 | `docs/conversation-endpoints.md`, `docs/runbooks/text-history.md`, `docs/security.md`, `docs/architecture.md`, `docs/spec-workflow-hooks.md` |
| FR-028, FR-029, FR-030 | `tests/unit/test_text_history_repository.py`; history cases in `tests/contract/test_text_endpoint.py`; `test_health.py` and the composition probe test; Terraform ownership check |
| FR-031 | `tests/integration/test_content_privacy.py::test_text_content_exists_only_in_the_history_container`; `history.store` telemetry test |

## Validation record (2026-11-04)

| Gate | Result |
|------|--------|
| Backend suite | 505 passed; the four `test_foundation_recovery.py` cases fail as in the baseline (the uv cache cannot build the project offline) |
| Management suite | 732 passed, 0 failed, 8 min 26 s (an earlier run had one browser timing flake that passed on every rerun) |
| Tools suite | 325 passed |
| `ci.py check` | lint, format, both type gates, whitespace, reachability and docstrings (strict), Terraform checks, secret scan and publication guard all pass |
| `verify_terraform.py check` | 38 native tests across 3 roots |
| `verify_docs.py` | 68 documents, 46 Mermaid diagrams rendered |
| `audit_dependencies.py` | no Python findings; one frontend finding that is an approved mirror gap |
| Network | default suites keep `--disable-socket` with localhost allowed; no live call or credential was used |

## Quickstart walk-through

| Quickstart step | Evidence |
|-----------------|----------|
| 1.1 to 1.4 endpoint surface, retired paths, non-empty `settings` | `tests/contract/test_route_inventory.py`, `test_text_endpoint.py`, `test_voice_endpoint.py` |
| 2.1, 2.2, 2.6 console reaches the backend only through the public endpoints | `tests/unit/test_backend_connection.py`, `tests/unit/test_test_console.py`, `tests/contract/test_browser_tests.py` (voice and text through the real relay and runtime) |
| 2.3 second text turn continues the stored history | the two-turn case in `test_browser_tests.py`; `tests/integration/test_conversation.py` |
| 2.4 retention notice | `tests/unit/test_test_console.py` (notice text) |
| 2.5 history store down gives `history_unavailable` and `/readyz` `history_store_unavailable` | `test_text_endpoint.py` (history failure cases), `tests/contract/test_health.py` |
| 3 reachability, docstrings, docs | `ci.py check`, `verify_docs.py` |
| 4 Terraform pairs, stage-name check, fourth stage | `test_static_checks.py` (`test_example_pairs_validate_for_any_stage_without_code_edits`, `test_terraform_static_check_rejects_stage_name_logic`), `test_terraform_delivery.py` (any stage name) |
| 5 quick and full tiers with timings | `research.md` "Final suite timings (T078)" |
| 6 hooks prompt and skip cleanly | `code/tools/tests/test_spec_hooks.py`; the commit hook and the push/pull-request command were run against this branch (pull request #5) |

## Removed tests

Call-era tests were deleted with the behavior they covered: call lifecycle, handover, drain, owner
routing, capacity and rollout repositories, audit and the call-media and provider-callback routes.
The text-over-WebSocket cases of the old test socket moved to `test_text_endpoint.py`; the
WebSocket cases kept their coverage in `test_voice_endpoint.py` and the ported integration tests.
`tests/unit/test_sdk_startup.py` was a duplicate of `tests/integration/test_sdk_lifecycle.py`: its
startup-policy case and its unsafe-opt-in case both exist there, and the integration test gained
the environment-variable and restart assertions that the unit test alone had.
