# Contract: Voice session (GPT-Live)

**Owner**: runtime `adapters/gpt_live.py`, `api/voice.py`, `services/delegation_update.py`. Documented in `docs/conversation-endpoints.md` and `docs/runbooks/scaling.md`.

## Session lifetime (FR-013)

- One `session.start` per voice test. The session is closed only when the test ends (`session.close`, then `session.closed`).
- An agent change raises `agentEpoch` only. No socket is closed and `generation` does not change.
- The Live attempt in the slot's resources is settled once, at session end (recovery as in T099).

## Start instructions

`instructions` = the voice conversation instructions (published or shipped) + the delegation suffix (feature 006). When `CALLER_VOICE_DELEGATION_UPDATES=false`, the static delegation block is appended (≤ 4 KiB, same lines and cut rule as the delegation update below):

```text
Delegation scope for this call:
Backend tools:
- <one line per enabled, routable community: its routing description, or its name when the description is empty, cut to 160 characters>
Generate <|SpawnThinking|> when the request needs one of these capabilities.
If no listed capability fits, say briefly that this service cannot help with it and offer what it can do.
```

The total stays at most 16 KiB. `session.input` stays `[]`.

## Delegation update (`CALLER_VOICE_DELEGATION_UPDATES=true`)

`session.instructions.append` with `delegation_id: null`, sent right after `session.started` (before the greeting text is appended, so it cannot cut the greeting) and after an applied handover when the reachable set changed:

```text
Delegation update. This replaces all earlier delegation updates.
Backend tools:
- <routing description cut to 160 characters>
...
Generate <|SpawnThinking|> when the request needs one of these capabilities.
If no listed capability fits, say briefly that this service cannot help with it and offer what it can do.
```

- **Delegation phrase**: `Generate <|SpawnThinking|>`, the `gpt-live-1` wording that the GPT-Live prompting guide prescribes instead of "Delegate to the backend", exactly as in the shipped voice conversation instructions.
- **Capabilities, not names**: following the delegation policy of the GPT-Live prompting guide, each line says what the backend can do: the community's routing description. Community names are not listed, because the voice assistant only decides whether to delegate; the agents choose the community. The only exception is a community with an empty routing description, which is listed by its name.
- **Reachable set**: the active agent's own community, communities connected from it, and the routable communities reachable through Routing. Before a community is active (Welcome, Routing), it is all routable communities.
- **Size**: at most 2,000 UTF-8 bytes. When not all communities fit, the last tool line is "- …and more capabilities", and "For other requests, Generate <|SpawnThinking|> too; the backend decides whether it can help." replaces the "If no listed capability fits" sentence, so no request is refused because its community was cut. Parts of at most 500 UTF-8 bytes (the adapter's existing append limit) are split at line boundaries, the first part carrying the "replaces" sentence.
- **Acknowledgement**: `session.instructions.appended` matched by `client_event_id`. A missing acknowledgement within 5 s is logged (`test.session` failure, reason `unavailable`) but does not end the session.
- **Content**: application-authored text from the published configuration only. No user text.

## Delegation handling

1. `session.delegation.created` → quiet delegation update (feature 006, when on) → transcription wait.
2. **Transcription wait**: up to `CALLER_TRANSCRIPTION_WAIT_SECONDS` (default 0.5), ending early when a caller transcript delta with `end_ms >= offset_ms` arrives.
3. The pending `caller_speech` is recorded (`partial` if the wait ended without catching up) and the clarity check runs with the whole history (R8).
4. The work item runs on the active agent with the shared history ([shared-history.md](shared-history.md)).
5. Results go back through `session.commentary.append` in parts (feature 006), recorded as `delegation_result`.

## Microphone and loudspeaker (Test page controls)

The caller interrupts the voice assistant by speaking over it; the full-duplex voice model stops its answer. The Test page has no Interrupt button.

- **"Mute input"** stops or resumes recording from the microphone.
- **"Mute output"** (replaces "Interrupt output") silences or restores playback on the user's device by setting the playback volume to zero. Audio keeps playing silently, so `audio.played` and output pacing are unchanged, and turning it off resumes at the current point.

Both switches act only in the browser and send no message of their own. The voice session, the agents and their work continue, progress keeps showing in the chat, and the history still records what the voice assistant said.

`generation` stays the same for the whole voice test.

## Removed

- the runtime `interrupt` command (`ConversationPolicy.interrupt`) and its session replacement;
- the `output.interrupt` and `input.mute` messages in the browser, relay and runtime, and the GPT-Live input mute (`session.input_audio.mute` and `unmute`, `LiveSegment.set_muted`);
- `SpeechSegments.retire`/`continue_with` on agent changes;
- the `live_segment_not_closed` check in `admission.transition`;
- the quiet context note (`session.thinking.append`, feature 006 FR-038).
