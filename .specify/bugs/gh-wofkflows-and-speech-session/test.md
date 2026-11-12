# Bug Verification: Image publish and CI fail on HIGH krb5 CVEs; a slow delegated model step ends speech sessions and locks the manager out of new tests

- **Slug**: gh-wofkflows-and-speech-session
- **Tested**: 2026-11-12
- **Assessment**: ./assessment.md
- **Fix**: ./fix.md
- **Result**: partial

## Summary

The fix holds everywhere it could be exercised. No regressions were found.

- **Image scan:** the pinned Trivy reports 16 HIGH `krb5-libs` findings on the pinned base, and 0 once the fix's `apk` upgrade line is added.
- **Stalled-model reproduction:** the assessment's local reproduction (a model double that stalls past the budget) now keeps the session open and frees the slot.
- **Held slot on int:** the recovery script was run with the owner's confirmation and released the held slot. The int authority now holds no slots.
- **Full suites:** backend, tools and management suites, lint and type checks all pass.

The result is `partial` for three reasons:

- The full images were not built locally, because PyPI is blocked in this network. So "Publish Images" and `audit_dependencies.py --images` were not run end to end.
- The runtime fix is not deployed to int yet, so the speech-session behavior (#4) was only verified with automated tests.
- No new test was started from the int console after the slot was released.

## Checks Performed

| Check | Command / Action | Result | Notes |
|-------|------------------|--------|-------|
| Reproduction #1/#2, before fix | Pinned Trivy `aquasec/trivy:0.76.1@sha256:790fd…` with `image --scanners vuln --severity HIGH,CRITICAL --exit-code 1` on the pinned base `python:3.12.15-alpine3.23@sha256:3d0ca66b…` | pass (symptom reproduced) | `Total: 16 (HIGH: 16, CRITICAL: 0)`, all `krb5-libs` 1.22.1-r0, fixed in 1.22.3-r0 |
| Reproduction #1/#2, after fix | Same scan on an image built from the same base plus the fix's line `RUN apk add --no-cache --upgrade 'krb5-libs>=1.22.3-r0'` | pass | `alpine` target: 0 findings; installed `krb5-libs-1.22.3-r0` |
| Full image build and audit | `docker build code/backend`, then Trivy (the steps of `audit_images()`) | not-run | `uv sync --locked` inside the Docker build failed with exit code 2: PyPI is not reachable from this workstation (the same TLS block as the hatchling download). This runs in CI ("Publish Images" and the Foundation CI image job). |
| Reproduction #4 (assessment step 5), local equivalent | `pytest tests/unit/test_conversation.py::test_slow_speech_delegation_holds_then_fails_alone_and_next_delegation_runs` plus the adapter stall tests | pass | Holding line, then "could not finish", session stays open, next delegation runs, late attempt settles, `test.work failure` recorded |
| Reproduction #3, regression (local) | `test_text_stop_clears_volatile_context_but_only_definitive_work_releases[False]` | pass | While the stopped request is in flight, the same manager gets `manager_busy`. After it finishes, the slot is released and the manager can reserve again. |
| Reproduction #3 on int: inspect held slot | Read-only authority read inside `ca-caller-int-runtime` (image `sha-60f740e…`) | pass | 1 slot: `test-ed0c8d8b622e47de9d38fce0a50a79ac`, speech, fenced, started 2026-11-11T19:27:04Z. Model attempt 5 unsettled (settled 4); live 2/2 settled; capability 0/0. Matches the 19:27 incident. |
| Recovery preview on int | `python -m caller_runtime.recovery` (staged script, preview) | pass | `would release 1 slot(s): test-ed0c8d8b622e47de9d38fce0a50a79ac` |
| Recovery apply on int (owner confirmed) | Same script with `--apply` | pass | `released 1 slot(s): test-ed0c8d8b622e47de9d38fce0a50a79ac` |
| Recovery after-check on int | Read-only authority read; preview run again | pass | `slots 0`; `would release 0 slot(s): -` (idempotent). Staged `/tmp` file removed. |
| Start a new test from the int console | Manual console start | not-run | Needs an interactive signed-in manager; the user prefers automated checks. The cause (the held slot) is gone. |
| New and updated tests | `pytest` in `code/backend` on the fix's tests, `-k "slow or early_close or stop_clears or failed_work_keeps or recovery or correlated_fragments or definite_provider_refusal or composed_runtime"` | pass | 32 passed. Includes 4xx-only settlement, no replay (one request), admission-denial logging and recovery scope. An earlier 5× repeat of these tests was stable. |
| Backend regression suite | `pytest tests -q -p no:cacheprovider --deselect tests/integration/test_foundation_recovery.py` (`code/backend`) | pass | 759 passed, 6 deselected. The deselected file needs network via uv. |
| Tools suite (includes the krb5 static test) | `uv run --locked pytest tests` (`code/tools`) | pass | 498 passed, 4 skipped |
| Management suite | `pytest tests/unit tests/contract tests/integration -m "not browser" --deselect tests/integration/test_foundation_recovery.py` (`code/management`) | pass | 1050 passed. `reflex.lock` restored afterwards. |
| Lint and type checks | `ruff format --check`, `ruff check`, `mypy src` (backend); `ruff check` (management) | pass | 71 files already formatted; no lint or type findings |
| Browser tests | `pytest -m browser` | skipped | Not part of the fix's paths. The console change is a fixed string pinned by a unit test. |

## Output Excerpts

```text
caller-krb5-base:bugtest (alpine 3.23.7)
Total: 16 (HIGH: 16, CRITICAL: 0)
krb5-libs  CVE-2026-107364  HIGH  fixed  1.22.1-r0  1.22.3-r0 ...
caller-krb5-fixed:bugtest (alpine 3.23.7)   alpine   0
krb5-libs-1.22.3-r0
```

```text
# int authority, before recovery (read-only)
slots 1
test-ed0c8d8b622e47de9d38fce0a50a79ac speech ending fence True started 2026-11-11T19:27:04.827910+00:00 evidence False
   capability 0 0 None
   live 2 2 live-4a003f0d43d84f87b67ffb6c07c46855
   model 5 4 model-6c0f013f15ae4821a360ce1f89d3418e
would release 1 slot(s): test-ed0c8d8b622e47de9d38fce0a50a79ac
released 1 slot(s): test-ed0c8d8b622e47de9d38fce0a50a79ac
# after
slots 0
would release 0 slot(s): -
```

```text
backend:    759 passed, 6 deselected, 1 warning in 52.42s
targeted:   32 passed, 230 deselected in 15.18s
tools:      498 passed, 4 skipped in 87.06s
management: 1050 passed, 6 deselected, 3 warnings in 201.53s
```

## Residual Risks

- **Image gates not run end to end here.** The full backend and management images (with their Python and frontend dependencies) were not built or scanned locally, so `audit_dependencies.py --images` and "Publish Images" must pass in CI. Python and frontend package findings are covered separately by the dependency audits.
- **Runtime fix not deployed.** Int still runs `sha-60f740e…`. Until the fixed image is deployed, a slow model step can still end a speech session and hold a slot again. If that happens before deployment, re-run the recovery script (preview, then `--apply`).
- **Recovery script on the deployed image.** The script ran on int from a staged copy against the deployed image's modules, which match HEAD for every API the script uses. Its packaged form (`python -m caller_runtime.recovery`) is exercised only by unit tests until the fixed image is deployed.
- **Unchanged rule for 5xx and transport errors.** A model call that ends in one of these before the 60 s cap still holds the slot, as before (see fix.md "Deviations").
- **Expiring audit exception.** The frontend audit exception GHSA-g3hv-75wg-m249 expires 2026-11-17T09:25:22Z and will fail CI again if it is not renewed.

## Recommendation

Hold, then close once CI confirms. The int lockout (#3) is cleared, and the speech-session fix (#4) is verified by the assessment's local reproduction and the regression suites. To close:

- Push the fix and confirm "Publish Images" and the Foundation CI "Linux image health and browser smoke" job pass (#1, #2).
- Deploy the fixed runtime to int.
- Optionally confirm that a new text and speech test starts from the int console.
