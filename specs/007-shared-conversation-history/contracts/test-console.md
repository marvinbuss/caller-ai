# Contract: Test page (test console)

**Owner**: management `components/live_audio_js.py`, `adapters/text_relay.py`, `adapters/relay_frames.py`, `adapters/test_session_client.py`, `pages/test_console.py`. Documented in `docs/management-frontend.md`.

## Control messages from the runtime

| Message | Change |
|---------|--------|
| `agent.changed` | Voice: `generation` no longer changes with it. The browser updates the active agent and community in the metadata only; no conversation row (quiet handovers, FR-026). |
| `generation.changed` | No longer sent: `generation` stays the same for the whole voice test. Management still accepts it with a higher generation from a runtime that predates this feature, during the rollout (management deploys first). |
| `history.condensed` | New, both modalities. Adds a status row "Older parts of this conversation were condensed." (`data-kind="status"`, announced politely as feature 006 status rows). Never spoken. |

## Controls from the browser

| Control | Change |
|---------|--------|
| Interrupt output | Removed. The caller interrupts by speaking over the voice assistant. |
| Mute output | New, replaces Interrupt output. Silences or restores playback on the device by setting the playback volume to zero; audio keeps playing silently, so `audio.played` and pacing are unchanged. Sends no message of its own. Progress keeps showing in the chat. |
| Mute input | Stops or resumes recording from the microphone. No longer sent to the relay or runtime (`input.mute` is removed). |

## Relay frames (voice)

- Audio and capture of the current `generation` are accepted across agent changes; the generation does not change during a voice test.
- The T095–T098 tolerances for a generation that is ahead of the policy during an agent change are removed, because an agent change no longer changes the generation.

## Notices

When the published platform rules of a modality are custom and contain no "Handover rules:" section, the Test page shows: "Custom platform rules have no handover rules. Handovers may be less accurate." It links to the Settings section.
