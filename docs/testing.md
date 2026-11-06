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
not fail the cases after it. The speech cases press Mute only when no audio is queued on the console socket, because the console ends a session that is muted with audio still queued.

Markers: `quick` (fast tier) and `browser` (needs prepared Chromium). `--strict-markers` is on.

## In-process end-to-end journey

`code/e2e` composes the runtime and the management app from their live settings in one process, with
fake stores, a local identity authority and a streamed model. One test publishes a configuration, reads
it as a tester application, requests a signed ticket, runs a text turn through the console socket and the
runtime, and checks the stored history and the one correlation id in both apps' events. CI runs it with
`ci.py e2e` (a locked environment that installs both apps). To run it beside the other tests without that
environment, use the management environment: `uv run --directory code/management python -m pytest ../e2e/tests`
borrows the runtime's source and packages from `code/backend/.venv`.

Where the public package index is unreachable, install the same locked environment through a mirror that
you set in your own shell (see `code/.local`, which is not committed). The lock is never rewritten: export
its hash-pinned requirements to a temporary file and install them with hash checking, so the mirror can
only serve the exact files the lock names. From `code/e2e`:

```powershell
uv export --frozen --no-header --no-emit-package caller-runtime --no-emit-package caller-management --no-emit-package caller-e2e --output-file $env:TEMP\e2e-requirements.txt
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe --require-hashes --no-deps -r $env:TEMP\e2e-requirements.txt
uv pip install --python .venv\Scripts\python.exe --no-deps ..\backend ..\management
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
```

Do not run `uv lock` or `uv sync --locked` with a mirror set: the lock names the public index, so they
either fail or rewrite it. `ci.py check` rejects a mirror host in any committed file.
