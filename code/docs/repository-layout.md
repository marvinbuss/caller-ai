# Repository Layout

Caller AI has two independent Python applications: a FastAPI call-runtime service and a
Reflex management application. Each owns its dependency lock and environment. The initial
delivery is a credential-free local foundation. Default startup still has no live call
handling or editor; separately authorized synthetic local editing is described in the
[configuration guide](configuration-guide.md) and [recovery runbook](runbooks/configuration.md).

## Ownership

| Path | Responsibility |
|------|----------------|
| `code/backend/` | Call-runtime application, tests, dependency lock and Docker image |
| `code/management/` | Management application, tests, frontend lock and Docker image |
| `code/tools/` | Independent development tools and portable verification scripts |
| `code/contracts/` | Accepted versioned interface schemas, not shared persistence code |
| `code/docs/` | Setup, architecture, operational guidance and acceptance evidence |
| `code/infra/terraform/` | Later Terraform-only infrastructure; not needed for local startup |
| `.github/workflows/` | Required GitHub workflow discovery metadata only |
| `specs/001-call-center-agents/` | Requirements, design and reviewer-owned checklists |

Workflow helpers and pre-commit configuration belong under `code/`. Neither application may
read another application's private database. Live management owns its configuration; call
runtime consumes authenticated configuration contracts. No cloud client is needed for health.

## Developer Platforms

Support native Windows with PowerShell and Linux with a shell. Use Python 3.12 and `uv`;
prepare the pinned Reflex frontend tools and Chromium before browser verification. Initial
package, frontend-tool and browser installation may require internet. Subsequent local
startup and starter tests require no Azure resources, cloud credentials, phone number, model
endpoint, or Cosmos emulator. Native Windows evidence cannot be replaced by WSL alone.

Linux Docker images require a Docker-capable host for actual build/run verification. A
Dockerfile alone is not proof of a working image. GitHub Actions must verify both native
platforms; local evidence must never be represented as an observed remote CI result.

## Dependency Feed

Shared source, workflows and Python locks reference only public registries/artifacts. The
frontend lock is registry-relative with unchanged versions/integrities. Private feed URLs
and original mirror locks are confined to Git-ignored local settings/backups under
`code/.local/`, never publishable source. The npm and Bun project configuration defaults
are public; explicit local process variables can select approved workstation mirrors.
Always retain normal TLS verification and run the [publication check](setup.md) before
committing. Do not publish a lock rewritten by a local private registry.

```powershell
. ./code/.local/feeds.ps1
uv run --directory code/management --locked --no-sync reflex export --frontend-only --no-zip
```

Native Windows dependency installation and production frontend export succeeded with
Reflex 0.10.0, Bun 1.4.2 and Node 24.22.0. The saved `reflex.lock/` manifest and Bun lock
match the generated build, and a subsequent `bun install --frozen-lockfile` passed.
That earlier preparation used local mirrors. The publishable lock origins have since been
relocated without dependency changes; see [setup](setup.md) for verified scope and public
download/TLS limits. Preparation is not application, native Linux or remote-CI acceptance.

## Change Rules

Run repository-root static checks with
`uv run --locked --project code/tools pre-commit run --config code/.pre-commit-config.yaml --all-files`.
Install the hook with
`uv run --locked --project code/tools pre-commit install --config code/.pre-commit-config.yaml`.
Hooks use the isolated locks, check both application type surfaces, and scan secrets offline.
Prepare all three environments before running hooks, and retain the required Python-feed
environment variable if a command needs to install a locked dependency. Before the first
commit, use `--files` with explicit paths because `--all-files` includes only Git-tracked files.

Create directories only for actual files. Preserve the existing Spec Kit artifacts, user
changes, and reviewer-owned checkbox states. Do not commit, change branches, publish images,
or provision infrastructure without an explicit request. T031 is the original foundation
acceptance boundary. The user separately authorized deterministic local work beyond it;
that continuation does not accept the foundation or waive security/live gates. Current local
configuration scope and pending external evidence are recorded in [US2 acceptance](acceptance/us2.md).

The independent local runtime continuation is recorded in [US3 acceptance](acceptance/us3.md)
and the [community runbook](runbooks/communities.md). These services are explicitly composed
for synthetic qualification and do not mount live call routes or waive provider/release gates.
