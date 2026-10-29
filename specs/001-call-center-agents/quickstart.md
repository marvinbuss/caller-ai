# Quickstart and Validation Guide

**Status**: Planned commands for implementation acceptance. Application projects, lockfiles,
tests, images, and workflows do not exist yet. Do not treat this guide as an executed test report.

This guide accepts the repository foundation, not real call handling or functional agent
management. Use [plan.md](plan.md), [data-model.md](data-model.md), and the
[interface contract](contracts/interfaces.md) for responsibilities and later integration rules.

## Prerequisites and Dependency Installation

- Native Windows with PowerShell or Linux with a shell; initial CI targets Windows Server 2022
  and Ubuntu 24.04. Document the tested local OS versions in the delivered implementation guide.
- Git, pinned uv, and a security-patched Python 3.12 interpreter installed or provisioned by uv.
- Reflex's pinned frontend build tools and generated frontend dependency lock; installation
  may need internet and native platform prerequisites. Native Windows cannot be replaced by a
  WSL-only acceptance run. Follow the pinned Reflex installation requirements.
- Chromium/Playwright and its required OS libraries for browser smoke tests. OS-level package
  installation, if necessary, is an administrator prerequisite, not an elevation prompt in CI.
- Docker with Linux containers for image verification. Native tests on Windows do not require
  Docker; the Docker CI job is Ubuntu-only.
- Terraform and provider downloads only when validating authored Terraform. No Azure login,
  Cosmos account/emulator, phone number, model endpoint, or external-service secret is required.

Run the following intended commands from repository root after projects and committed locks
have been implemented:

```text
uv sync --project code/backend --locked --group dev --python 3.12
uv sync --project code/management --locked --group dev --python 3.12
uv sync --project code/tools --locked --python 3.12
uv run --project code/tools --locked pre-commit install --config code/.pre-commit-config.yaml
uv run --directory code/management --locked reflex init
uv run --directory code/management --locked playwright install chromium
```

The project must already have its configured Reflex application and frontend lock. `reflex init`
is the pinned release's toolchain initialization step, not permission to replace an existing app
with a new template; implementation must document its verified non-overwriting initialization
behavior. On Linux provision Playwright system libraries beforehand; do not hide prompts through
pipes. Initial dependency/toolchain/browser downloads may use the network. Application startup
after that phase must not contact live Azure, phone, speech, or tool services.

The backend dependency lock must include the OpenAI Agents SDK realtime extra
(`openai-agents[realtime]`). Verify its Python/FastAPI/Pydantic compatibility in the backend's
own environment; do not add it to the management environment solely for configuration editing.
Installing the SDK must not introduce a startup requirement for `OPENAI_API_KEY`, Azure credentials,
or a live model. SDK and realtime tracing are disabled by default. Explicit application opt-ins
use the independent flags and privacy/destination checks in the interface contract; unsafe
configuration is rejected rather than enabling content capture. Both services still emit
application-owned metadata-only distributed traces with safe cross-service correlation.

## Start Both Applications

In two separate terminals, set local mode and run the appropriate server. Local mode must not
discover Azure credentials or construct networked SDK clients.

PowerShell, backend terminal:

```powershell
$env:CALLER_MODE = 'local'
uv run --directory code/backend --locked uvicorn caller_runtime.main:app --host 127.0.0.1 --port 8000 --loop asyncio
```

PowerShell, management terminal:

```powershell
$env:CALLER_MODE = 'local'
uv run --directory code/management --locked reflex run --env dev --frontend-port 3000 --backend-port 8001 --backend-host 127.0.0.1
```

Linux, backend terminal:

```bash
CALLER_MODE=local uv run --directory code/backend --locked uvicorn caller_runtime.main:app --host 127.0.0.1 --port 8000 --loop asyncio
```

Linux, management terminal:

```bash
CALLER_MODE=local uv run --directory code/management --locked reflex run --env dev --frontend-port 3000 --backend-port 8001 --backend-host 127.0.0.1
```

Use separate free ports if these are occupied and update the smoke-test environment consistently.
The implemented harness must allocate known ports, bound readiness polling to 120 seconds after
dependency preparation, and clean up all child processes on success or failure. Silent auto-port
selection cannot satisfy tests aimed at a different process.

## Verify Health, Readiness, and the Browser

PowerShell:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/healthz
Invoke-RestMethod http://127.0.0.1:8000/readyz
Invoke-RestMethod http://127.0.0.1:8001/healthz
Invoke-RestMethod http://127.0.0.1:8001/readyz
```

Linux:

```bash
curl --fail http://127.0.0.1:8000/healthz
curl --fail http://127.0.0.1:8000/readyz
curl --fail http://127.0.0.1:8001/healthz
curl --fail http://127.0.0.1:8001/readyz
```

Expected: health returns `status=ok`, the correct service name, `mode=local`, and version;
readiness is 200/ready with both feature capability flags false. Responses contain no secrets or
external endpoint details. Validate against [foundation.openapi.json](contracts/foundation.openapi.json).
After ready, controlled health tests require response within one second.

Open `http://localhost:3000`. The actual Reflex shell must render the product identity, remain
usable at widths 360, 768, and 1440, have no overlapping or hidden primary elements, and establish
its normal framework event connection. Browser smoke fails on unexpected page errors or missing
assets. The shell must not simulate a working configuration editor or expose unauthenticated
management data. No browser call playground is introduced by the synthetic transport tests.

## Run Starter Tests and Static Checks

```text
uv run --directory code/backend --locked pytest
uv run --directory code/management --locked pytest
uv run --directory code/management --locked pytest tests/browser
uv run --directory code/backend --locked mypy src
uv run --directory code/management --locked mypy management_app
uv run --project code/tools --locked pre-commit run --config code/.pre-commit-config.yaml --all-files
```

Both applications need distinct tests, not duplicated always-passing placeholders. Expected cases:

1. Health schema and correct service identity, startup/lifespan, cleanup, local readiness, safe
   diagnostics, and live-mode missing-configuration failure without fallback.
2. Synthetic WebSocket probe acknowledgment, malformed message/oversize rejection, close behavior,
   bounded buffering, and cancellation. Use the exact contract, not arbitrary echo assertions.
3. Both apps start and tests pass with cloud credentials absent and live-provider destinations
   denied. No Cosmos emulator or identity credential chain is constructed in the default path.
4. Composed management health plus real Reflex shell/browser checks at required viewports.
5. Log event schema, metadata-only distributed spans in both services, cross-service context
  propagation, safe attributes, stable errors, and credential/prompt/audio/transcript/tool-payload
  exclusion; local tests use in-memory collectors and require no external telemetry service.
6. All assertions execute natively on Windows and Linux. Stale locks, missing tests, crashes,
   and nonzero exits fail verification; do not skip a failing platform to obtain green CI.
7. The backend SDK adapter imports/configures without credentials, never constructs a live
  connection in local startup, and exports no SDK or realtime tracing data by default. Synthetic
  opt-in tests cover each independent application flag, safe collectors and rejected unsafe
  content/destination settings. When orchestration
  is implemented, exercise `RealtimeAgent`/`RealtimeRunner` with the pinned SDK's documented
  scripted realtime model or an injected model double. Assert fresh-session context filtering,
  source retention on failed preparation, single committed output epoch, and rejection of
  forbidden tool calls before external execution. These tests do not claim live Azure support.

Expected test commands collect and run the documented suites and fail on broken behavior.
During acceptance, deliberately break one starter assertion on a disposable implementation branch
or test fixture, verify the corresponding CI job fails, then restore it without changing expected
behavior. A successful dependency installation or empty test run is not acceptance evidence.

## Docker Image Verification

After Dockerfiles and image build assets are implemented, build each service using its own context:

```text
docker build --tag caller-runtime:local code/backend
docker build --tag caller-management:local code/management
docker run --detach --rm --name caller-runtime-smoke --env CALLER_MODE=local --publish 127.0.0.1:8000:8000 caller-runtime:local
docker run --detach --rm --name caller-management-smoke --env CALLER_MODE=local --publish 127.0.0.1:3000:3000 caller-management:local
```

The runtime must bind internally to `0.0.0.0:8000`; the pinned Reflex production image serves
unified UI/API on `0.0.0.0:3000`. Host publishing stays loopback. Check runtime health on 8000,
management health/readiness and the rendered frontend on 3000. Development management backend
port 8001 is not the production container port. Images must contain their compiled assets and
locked Python dependencies, start without package downloads or Azure credentials, and run
without root. Browser verification must exercise the image, not only a development server.

Clean up explicitly:

```text
docker stop caller-runtime-smoke caller-management-smoke
```

CI uses guaranteed cleanup even if readiness/browser tests fail. Image support is proven by
build and run results, not by the existence of two Dockerfiles. Publication and deployment remain
separate, privileged operations; this planning workflow performs neither.

## GitHub Actions and Infrastructure Checks

Workflow discovery YAML lives under `.github/workflows/`. Everything it executes lives under
`code/`, including pre-commit config and helper scripts. Required jobs are native Windows/Linux
locked tests, browser smoke, static/security checks, and Ubuntu-only image build/run. Use minimal
permissions, full-SHA action pins, no cloud credentials, and no privileged pull-request execution.

Audit each project's hashed locked export with pip-audit, and check frontend/image dependencies
separately once those artifacts exist. Advisory access is expected; an audit fetch failure is not
a clean security result. Never commit scanner output containing secret candidates.

Once Terraform sources exist, formatting and backend-disabled validation are credential-free:

```text
terraform -chdir=code/infra/terraform/environments/dev fmt -check -recursive
terraform -chdir=code/infra/terraform/environments/dev init -backend=false
terraform -chdir=code/infra/terraform/environments/dev validate
```

Validate modules and bootstrap roots too. Provider/module acquisition may require internet.
These checks do not establish a usable Azure plan, remote-state provisioning, network security,
or deployment. Real Terraform plans require a reviewed environment and identity; never run apply
from this guide's foundation CI path.

## Later Feature Acceptance

Local acceptance means deterministic tests pass without cloud resources. Each local story
records its live evidence as pending, not passed or prerequisite-complete. Before any live suite,
complete the task list's explicit authorized-environment gate: reviewed provisioning or validation
of pre-provisioned resources, approved image deployment, identity, networking, provider access and
quotas. Terraform apply or deployment always needs separate authorization; reading this guide is
not permission to provision resources. Unavailable live prerequisites do not block local acceptance.

Before enabling later interfaces, verify atomic Cosmos publication/ETag conflicts and actual
restart durability; protected-role integrity, peer isolation and context filtering; current-policy
revocation during pinned sessions; denial of private/mutating tools; authenticated manager flows;
and the spec's call lifecycle. Use separately approved live credentials for those opt-in suites.
Verify that the OpenAI Agents SDK adapter maps revisioned definitions to realtime agents without
replaying old conversation history, that tracing is disabled by default and any explicit opt-in
retains the approved metadata-only controls, and that live
Azure endpoint authentication/token renewal and fresh-session handover latency meet the contracts.

Before accepting live phone calls, qualify provider lifecycle/licensing, bidirectional audio,
codec/interruption/hangup, model availability and quotas, identity, residency/disclosure and audit
retention. Run active criteria SC-001 through SC-007, SC-009 through SC-014, and SC-018 through
SC-019, including 100-to-200 call load,
failure injection, pre-drain without dropped calls, and five-minute volatile-content cleanup.
The foundation's green CI does not waive these gates or constitute production approval.
SC-008's participant-based manager usability study is deferred. Automated editor, authorization,
responsive-layout and accessibility tests remain required; no participant recruitment is needed.
