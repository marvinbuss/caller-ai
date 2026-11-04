# Testing

All tests run against controlled doubles; none reaches a live provider, a cloud account or a real
environment. No test is skipped or weakened for speed.

| Tier | Command | Content |
|------|---------|---------|
| Quick | `uv run --project code/tools python code/tools/scripts/ci.py quick` (or `pytest -m quick` in `code/backend` or `code/management`) | Unit and contract tests that build no frontend and start no host |
| Full | `ci.py test` and `pytest code/tools/tests` | Every test, including integration, owned-host and browser cases (the required gate) |
| Checks | `ci.py check` | Lock, pre-commit (including `check_modules.py --strict`), Terraform verification and tooling tests |
| Opt-in | not run by CI | Live provider, real-environment and device acceptance; they need separately approved access |

The full management suite takes several minutes because it builds a frontend and starts an owned
runtime process; `test_owned_manager_relay_enforces_stable_actor_and_five_occupied_slots` is
load-sensitive and passes when run alone.

Markers: `quick` (fast tier) and `browser` (needs prepared Chromium). `--strict-markers` is on.
