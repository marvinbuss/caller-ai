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
runtime process. The editor hosts of one session share a single compiled frontend and its ports (see
`_EDITOR_BUILD` in `code/tools/scripts/smoke.py`); each host still gets its own certificate, server
processes and state. The editor-host cases are timing-sensitive: do not run other heavy commands
(the documentation render, another suite) at the same time, because a build or startup that exceeds
its deadline fails the test.

Markers: `quick` (fast tier) and `browser` (needs prepared Chromium). `--strict-markers` is on.
