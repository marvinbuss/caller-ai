# Feature 003 acceptance map

Status: implemented for the backend endpoints, text history, management console, Terraform stage
neutrality and Spec Kit hooks; code-structure, library and test-speed work is partly deferred (see
`specs/003-codebase-simplification/research.md`, "Implemented and deferred"). Every check below runs
against controlled doubles; no live provider, cloud account or real environment was used (FR-027).

| Requirement | Covering test or check |
|-------------|------------------------|
| FR-001, FR-003 | `code/backend/tests/contract/test_route_inventory.py`, `test_text_endpoint.py`, `test_voice_endpoint.py` |
| FR-002 | `test_route_inventory.py::test_retired_routes_are_not_found`; `code/tools/tests/test_static_checks.py::test_active_files_name_no_retired_endpoint_path`; `check_modules.py --strict` |
| FR-004, FR-007, FR-008 | `code/backend/tests/unit/test_connection_contract.py`; settings cases in `test_text_endpoint.py` and `test_voice_endpoint.py`; `code/management/tests/unit/test_backend_connection.py` |
| FR-005, FR-006, FR-009 | `test_backend_connection.py`; `code/management/tests/unit/test_test_console.py`; `tests/contract/test_browser_tests.py::test_owned_manager_relay_enforces_stable_actor_and_five_occupied_slots` (voice and two-turn text through the real relay and runtime) |
| FR-010 | `check_modules.py --strict` (zero unreachable modules, one reasoned allow-list entry) |
| FR-011 | `check_modules.py` purpose-docstring check; the module documentation pass is partly deferred |
| FR-012, FR-013 | Deferred: the GPT-Live SDK rewrite and the `[realtime]` extra removal were not done |
| FR-014 | Breaking renames recorded in `specs/003-codebase-simplification/contracts/` and `docs/conversation-endpoints.md` |
| FR-015 | Retained admission, limit, privacy and cleanup cases in `tests/unit/test_conversation.py`, `tests/unit/test_admission.py`, `tests/integration/test_conversation.py`, `test_content_privacy.py` |
| FR-016, FR-017, FR-018 | `code/tools/tests/test_static_checks.py` (stage-name check, example pairs for any stage), `test_terraform_delivery.py`, Terraform native tests via `verify_terraform.py check` |
| FR-019 | `test_terraform_delivery.py` (OIDC-only, protected environment, exact saved plan) |
| FR-020, FR-021 | `quick` tier in all three projects; the duplicate-removal pass is partly deferred |
| FR-022 | Baseline and after-values in `specs/003-codebase-simplification/research.md` |
| FR-023, FR-024, FR-025 | `code/tools/tests/test_spec_hooks.py` |
| FR-026 | `docs/conversation-endpoints.md`, `docs/runbooks/text-history.md`, `docs/security.md`, `docs/architecture.md`, `docs/spec-workflow-hooks.md` |
| FR-028, FR-029, FR-030 | `tests/unit/test_text_history_repository.py`; history cases in `tests/contract/test_text_endpoint.py`; `test_health.py` and the composition probe test; Terraform ownership check |
| FR-031 | `tests/integration/test_content_privacy.py::test_text_content_exists_only_in_the_history_container`; `history.store` telemetry test |

## Removed tests

Call-era tests were deleted with the behavior they covered: call lifecycle, handover, drain, owner
routing, capacity and rollout repositories, audit and the call-media and provider-callback routes.
The text-over-WebSocket cases of the old test socket moved to `test_text_endpoint.py`; the
WebSocket cases kept their coverage in `test_voice_endpoint.py` and the ported integration tests.
