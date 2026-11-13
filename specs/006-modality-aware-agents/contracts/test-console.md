# Contract: Test console (management relay ↔ browser)

**Owner**: management (`adapters/text_relay.py`, `components/live_audio_js.py`, `pages/test_console.py`).

## Text session opening (slice 1)

When the console text session becomes ready, the relay sends the runtime **open request** ([text-endpoint.md](text-endpoint.md)) before any queued user turn. The greeting arrives as an ordinary `text.delta` / `text.completed` pair.

The 5 s target (SC-002) is measured from the browser's `ready` to the first greeting `text.delta`.

## New relay → browser events

| Event | Slice | Fields | Browser behavior |
|-------|-------|--------|------------------|
| `text.formatted` | 1 | `messageId`, `tree` | Replaces the plain-text row of `messageId` with nodes built from `tree` ([data-model.md](../data-model.md#formatted-text-tree-management-relay--browser-slice-1)). Never parses HTML. Keeps `href` and `src` only when the protocol is `https:`. Anything invalid keeps the plain-text row. Sent once, after `text.completed`. |
| `progress` | 2 | `messageId` (text) or none (voice), `state`, `text` | Adds a status row: `data-kind="status"`, the visible prefix `Status: `, distinct style, kept in history (FR-019/020/041). |

All events keep the envelope `type`, `sessionId`, `sequence`, `generation`, with the existing strict field check.

## Rendering rules (FR-010, FR-011)

- The row is built only with `document.createElement` and `textContent`.
- Links: `target="_blank"`, `rel="noopener noreferrer"`, `referrerPolicy="no-referrer"`; they open on click only.
- Images: `https:` only, with `referrerPolicy="no-referrer"`, `loading="lazy"`, `alt` text, and a maximum width of 100% of the row.
- `http:`, `data:`, `javascript:` and relative URLs render as plain text.
- Markdown with raw HTML is parsed with `html=False`, so the HTML shows as literal text.

## Accessibility (FR-041)

- `#test-messages` stays `role="log"` with `aria-live="polite"`.
- Status rows and the greeting are announced politely through it.
- Swapping in formatted content changes the existing row in place, without a second announcement.

## Settings and agent panels (slices 1 and 2)

- **Slice 1:** the agent settings panel for Welcome, Routing and Goodbye gains **Restore default**, which replaces the draft prompt with the shipped default.
- **Slice 2:** the Settings page gains:
  - a **Voice** section: voice conversation instructions, platform rules, global prompt, and the quiet handover switch;
  - a **Text** section: platform rules and global prompt.
- **Slice 2:** the agent settings panel gains a voice addition and a text addition.
- Every slice 2 field shows its byte count against its limit and a **Restore default** action.
- Saving changed platform rules or voice instructions opens a confirmation dialog: "This can break voice or text behavior. Publish anyway?" (FR-030, FR-037).
- Tester-only users never receive these fields (FR-029); role checks are server-side.
