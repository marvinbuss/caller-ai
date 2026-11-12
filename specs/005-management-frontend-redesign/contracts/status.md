# Contract: Service status

Two read-only endpoints. Neither returns secrets, keys, tokens, credentials, or full endpoint URLs with paths or queries.

## Runtime: `GET /v1/status`

| Item | Value |
|------|-------|
| Caller | Management app only |
| Authentication | Workload bearer token, same checks as the conversation endpoints (audience `CALLER_WORKLOAD_AUDIENCE`, role `CALLER_WORKLOAD_ROLE`, principal `CALLER_WORKLOAD_PRINCIPAL_ID`) |
| Failure | `401`/`403` with the existing fixed codes; `503 status_unavailable` if the probe state cannot be read |
| Cache | Probe results reused for 60 seconds; at most one probe per model in flight |

Response `200`:

```json
{
  "text": {
    "endpointHost": "caller-int-foundry.openai.azure.com",
    "deployment": "gpt-6-1-sol",
    "reachable": true,
    "checkedAt": "2026-11-11T10:00:00Z",
    "lastCallOutcome": "success",
    "lastCallAt": "2026-11-11T09:58:12Z"
  },
  "voice": {
    "endpointHost": "caller-int-foundry.openai.azure.com",
    "region": "swedencentral",
    "model": "gpt-live-1",
    "reachable": false,
    "checkedAt": "2026-11-11T10:00:00Z",
    "lastCallOutcome": null,
    "lastCallAt": null
  },
  "supportedVoices": [
    { "id": "marin", "name": "Marin", "language": "English", "accent": null, "presentation": null },
    { "id": "quartz", "name": "Quartz", "language": "English", "accent": "Australian", "presentation": "Feminine" }
  ],
  "defaults": { "voice": "marin", "maxOutputTokens": 1024, "idleTimeoutSeconds": 120 },
  "limits": { "maxOutputTokens": 1024, "idleTimeoutSeconds": { "min": 30, "max": 120 } }
}
```

- `voice.model`: the model name the runtime sends in `session.start` (today `gpt-live-1`); it is the voice model deployment the Status page shows (FR-023).
- `reachable`: a token for the Foundry scope was obtained and a TLS connection to `endpointHost` succeeded within 2 seconds. No model request is sent.
- `lastCallOutcome`: `success`, `failure` or `null`, from the runtime's most recent real model call.
- Voice entries above are examples; the full catalog is the runtime's reviewed constant ([research.md R15](../research.md#r15-voice-catalog-and-drop-down)). `accent` and `presentation` may be `null`.

## Management: `GET /v1/status`

| Item | Value |
|------|-------|
| Caller | Signed-in browser session |
| Roles | Editor or tester |
| CSRF | Not required (read) |
| Cache | 60 seconds; `?refresh=1` bypasses the cache at most once per 10 seconds per session |

Response `200`:

```json
{
  "runtime": { "reachable": true, "checkedAt": "2026-11-11T10:00:00Z" },
  "status": { "...": "the runtime body above, unchanged" }
}
```

When the runtime cannot be reached:

```json
{
  "runtime": { "reachable": false, "checkedAt": "2026-11-11T10:00:00Z", "lastSuccessAt": "2026-11-11T09:40:00Z" },
  "status": null
}
```

The Settings page builds the voice drop-down from `supportedVoices` (grouped by `language`, default marked) and uses `defaults` and `limits` from the same response to validate input; if `status` is `null`, the editable settings are shown read-only with an explanation.
