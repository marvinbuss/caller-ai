# US5 Capability Authorization Increment

## Status

T076-T088 provide locally qualified capability contracts, strict permission metadata, conditional
authority storage, reviewed public-read policy, the sole execution gateway, bounded HTTP/MCP
adapters, explicitly composed APIs, signed workload identity and gateway-only SDK wrappers.
Default app startup still mounts no capability routes and grants no tools without explicit
composition. No live calls, credentials, resources, deployments or business connectors were used.

**T087 and T088 are locally complete.** The final uninstrumented management regression run
passed **all 344 tests**, including 29 browser cases, without skips/deselection. The normal
Reflex export, original 180-second build/120-second readiness bounds and genuine browser
assertions were retained. This is local qualification, not foundation or live-release acceptance.

Earlier complete runs reported **320 passed, 24 errors**, with no skips/deselection. All
24 errors were editor-fixture `editor_frontend_build_failed`: bundling completed, then React
Router root prerender aborted on timeout. No cause or permanent framework fix is established.

An unchanged focused keyboard-only editor run passed at 360/768/1440 pixels (3 passed,
23 deselected). The following complete management rerun again reported 320 passed/24 errors.
The narrow success alone did not qualify acceptance; both complete failures and the isolated
success are retained as evidence.

The subsequent investigation temporarily added a metadata-only Node lifecycle probe. Its first
Windows preload path was malformed and produced 4 recovery failures/316 passes/24 editor
errors; that diagnostic run is not application evidence. After correcting only that probe,
the focused keyboard run passed and a complete instrumented run passed all 344 tests. The
probe and its environment injection were then deleted. The final original, uninstrumented
full suite again passed all 344 in 215.02 seconds. No runtime or test-harness fix was retained.
The earlier timeout is an unresolved intermittent export risk, not a proven repaired defect;
any recurrence must fail the gate normally rather than trigger hidden retries or higher bounds.

The complete backend run reports **233 passed, 13 errors**, without skips/deselection. Those
13 errors are the existing deferred `caller_runtime.api.media` contracts. This is not a green
full-backend gate. Historical editor failures and current deferred-media failures are retained,
not waived. The current backend qualification rerun reproduced 233 passes/13 deferred errors.

## Observed Local Evidence

| Slice | Result and meaning |
|-------|--------------------|
| Capability HTTP contracts | 15 pass: explicit composition, authentication before parsing/effects, manager/runtime separation, 403/412/422/428/503/504 and content-free errors |
| Authority/gateway ordering | 19 pass: strict metadata, conditional ordering, revoked attempts denied before effects, admitted reads may finish, fresh retry admission, stable request/deadline, no later-grant expansion |
| Reviewed/MCP policy | 22 pass: current/pinned/review intersection, disabled/protected/unassigned/private/mutating denial, unsafe origins, public-IP pinning, no redirects and real MCP SDK hostile discovery |
| Configuration persistence | 34 pass: previous local/Cosmos-double publication coverage plus same-partition admissions/revocations and no partial failed admission batch |
| Identity | All 45 unit cases pass, including seven actual signed-JWT runtime audience/role/app-client boundaries and all previous manager/browser/CSRF/OIDC checks |
| Runtime SDK/client | 13 pass: actual FunctionTool invocation/materialization, immutable call references, obsolete dispatch/result rejection, managed-identity scope, fixed origin and safe errors |
| Runtime SDK lifecycle | Previous 17 cases still pass, including fresh sessions, history/tracing policy and cleanup |
| Complete management regression | 344 pass, including 29 actual browser cases, with the original uninstrumented export and no skips/deselection |
| Static types | Management 29 production modules and runtime 20 production modules pass strict mypy |
| Changed lock audit | Strict hashed management export: 92 packages, zero known vulnerabilities, advisory access succeeded |

Management's locked MCP SDK is 1.30.0. The approved Python proxy resolved it without changing
existing package versions; additions are MCP, httpx-sse, sse-starlette, uvicorn and Windows
pywin32. No alternate installation feed or TLS bypass was used. The older tools virtualenv
advisory remains a separate open release gate; this audit does not waive it or qualify images.

```powershell
uv run --directory code/management --locked --no-sync pytest tests/contract/test_capability_gateway.py tests/unit/test_permission_ordering.py tests/integration/test_mcp_policy.py tests/integration/test_configuration_persistence.py tests/unit/test_security.py -q
uv run --directory code/backend --locked --no-sync pytest tests/integration/test_authorized_capabilities.py tests/integration/test_sdk_lifecycle.py -q
uv run --directory code/management --locked --no-sync pytest -q --tb=line
uv run --directory code/backend --locked --no-sync pytest -q --tb=line
```

## Scope of the Security Evidence

SC-005/SC-019 cases reject tested guessed/unassigned, forged private/mutating and remotely
advertised actions without private-field disclosure or business-data mutation. Reviewed public
results alone are returned. SC-010 ordering/failure cases deny unknown authority, preserve the
original deadline and fence stale runtime results. These are local assertions, not full live
success-criterion acceptance. Independent service tests use real HTTP/API/SDK contracts and
injected wire/storage doubles, not a qualified deployed cross-service topology.

The maintained MCP SDK initializes, discovers and invokes exactly the reviewed action over an
HTTPX mock wire. Extra advertised tools and read-only hints do not grant authority. DNS tests
prove the boundary pins a public resolved address and carries the original Host/TLS server name;
no live DNS/TLS handshake, remote MCP service or Azure workload token exchange was qualified.

Public classification and field allowlisting cannot prove arbitrary returned prose is public.
Review must establish the endpoint, action implementation, output-field semantics and bounded
schemas independently. Canonical recognized-secret scanning is not complete secret detection.
No customer/private-data or business-changing action becomes permitted merely by assignment.

## Composition and Remaining Work

The runtime must obtain an approved pinned projection and a trusted physical-owner epoch
checker. The existing US3 test profile continues to project no capabilities; this increment
adds an explicit SDK factory path, not automatic production activation. Future composition
must retain the fresh-session and single-owner handover gates. Ordinary configuration refresh
is never permission authority, and neither service may read the other's private containers.

T087/T088 close the deterministic local capability and documentation checkpoint only. The
intermittent editor export risk remains documented and is not a framework stability guarantee.
Real Cosmos durability/Strong consistency, Azure identity/networking, actual TLS/provider access,
retention/disclosure approvals, live latency and image/release gates remain pending. No foundation
acceptance, CVE mitigation or production permission is implied by this increment.

See the [capability runbook](../runbooks/capabilities.md) for explicit interfaces and failure rules.
