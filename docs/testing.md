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
runtime process for each editor host (the compiled frontend bakes in the host's origin port, so it
is not shared between hosts). The editor-host cases are timing-sensitive: do not run other heavy
commands (the documentation render, another suite) at the same time, because a build or startup
that exceeds its deadline fails the test.

Browser assertions wait 5 s on a workstation and 15 s when `CI` is set, because CI runners are
slower; the assertions themselves are the same. Each console case (text and speech) runs as its own
manager identity, so a test session left in `cleanup pending` holds only that case's slot and does
not fail the cases after it.

Markers: `quick` (fast tier) and `browser` (needs prepared Chromium). `--strict-markers` is on.
