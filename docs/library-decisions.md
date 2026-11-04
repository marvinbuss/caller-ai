# Library decisions

Each capability below names the official or standard library in use, or the reason a small amount of
custom code is retained. Add a row when a dependency is added or custom code replaces one.

## Backend (`code/backend`)

| Capability | Library | Notes |
| --- | --- | --- |
| HTTP, SSE and WebSocket server | FastAPI, Uvicorn | Standard stack; no custom server code. |
| Settings | pydantic-settings | Typed environment settings. |
| Agent runtime (text and delegation) | `openai-agents` | Runs agents against Foundry; the `realtime` extra is not used. |
| GPT-Live transport | `openai` SDK `AsyncOpenAI(...).live.connect()` | `adapters/gpt_live.py` calls it through `sdk_socket`. Connection limits (256 KiB frames, one queued frame, TLS context, no proxy, no compression) are passed as `websocket_connection_options`. |
| GPT-Live event validation | Retained custom code (`GptLiveProtocol`) | The SDK parses events without schema checks. The runtime needs duplicate-key rejection, bounded sizes, exact started-session binding and closure receipts, and keeps them as a small validator over raw frames. |
| GPT-Live token | `azure-identity` credential, token read once per start | `get_bearer_token_provider` would hide the token expiry. The adapter must prove the token outlives the session boundary before it connects, so it reads the token itself and passes it as the SDK `api_key`. |
| WebSocket client/server | `websockets` (also used by the SDK and by tests) | Declared directly (`>=15,<17`) because the SDK `realtime` extra pins `<16` and the repository runs 16. |
| Cosmos DB | `azure-cosmos` | Authority container and text history container. |
| Azure credentials | `azure-identity` | Managed identity and workload identity. |
| Permit and token verification | `PyJWT[crypto]` | Standard JWT library. |
| JSON schema checks | `jsonschema` | Capability argument validation. |
| Telemetry | OpenTelemetry API and SDK | Content-free events and metrics. |

## Management (`code/management`)

| Capability | Library | Notes |
| --- | --- | --- |
| Console UI and state | Reflex | One framework for pages and state. |
| API surface | FastAPI, httpx | Configuration API and the in-process editor requests. |
| Entra sign-in | `msal`, `PyJWT[crypto]` | Official Microsoft library for the OIDC flow; JWT verification of access tokens. |
| Capability tools | `mcp` | Official MCP client for approved tool calls. |
| Backend upstream socket | `websockets` | See the decision below. |
| Cosmos DB and credentials | `azure-cosmos`, `azure-identity` | Configuration store and managed identity. |

### Management upstream `websockets` usage

Kept. The voice bridge in `adapters/test_session_client.py` relays audio frames between the browser
gateway and the backend `caller.voice.v1` endpoint. The backend endpoint is a plain WebSocket
contract, not an OpenAI Live session, so the OpenAI SDK client does not apply. The text relay uses
`httpx` streaming and needs no socket. The upstream socket sets the same bounds the backend
enforces (frame size, queue, no proxy, no redirects), so no second client library is warranted.

## Tools (`code/tools`)

| Capability | Library | Notes |
| --- | --- | --- |
| Lint, format, types | ruff, mypy | Standard tooling. |
| Secret scanning, hooks | detect-secrets, pre-commit | Standard tooling. |
| Dependency advisories | pip-audit | Python advisories; image and frontend gates are in `scripts/audit_dependencies.py`. |
| Tests | pytest, psutil | Shared fixtures. |
| Parallel tests | `pytest-xdist` (not adopted) | Available on the approved mirror and it cut the backend suite from about 65 s to about 40 s locally with all 510 tests passing. Adding it needs lock entries whose artifact URLs point at the public file host; that host is unreachable from the workstation, so the lock cannot be produced and verified here. Add `pytest-xdist` to the backend dev group and `-n auto` to its `addopts` in a change that can run `check_publication.py relocate`. The management and tools suites stay serial: their browser and process cases share ports and builds. |
| Config and docs parsing | pyyaml, json5, markdown-it-py, cel-python | Workflow, Terraform JSON and doc link checks. |

## Retained custom code

- `GptLiveProtocol` validation and closure-receipt handling (above).
- Management gateway relay and per-turn permit builder: they implement the console boundary (the
  browser never reaches the backend directly) and have no library equivalent.
- Terraform delivery helpers in `code/tools/scripts/terraform_delivery.py`: thin wrappers over the
  Terraform CLI that enforce state-key and approval checks.
