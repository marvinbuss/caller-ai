# Contract: Frontend shell, routes, roles and events

## Routes

| Route | Page | Roles | Notes |
|-------|------|-------|-------|
| `/login` | Login | none | Logo and sign-in button only |
| `/` | Dashboard | editor or tester | Landing page |
| `/communities` | Community and Agent | editor | Graph, settings panel, Tools section |
| `/test` | Test | editor and tester; tester alone when `TESTER_ONLY_TESTS` is on | Existing console |
| `/status` | Status | editor or tester | [status.md](status.md) |
| `/settings` | Settings | editor | Conversation settings |
| `/configuration`, `/configuration/communities/[id]` | redirect | — | → `/communities` |
| `/configuration/test` | redirect | — | → `/test` |

Any route without a session shows the login page; a session without either role shows "no access"; a route the roles do not allow shows a "role required" message and no page data. Every page data event is role-checked on the server.

## Sign-in and session

| Endpoint | Change |
|----------|--------|
| `GET /auth/login?returnTo=<path>` | New optional `returnTo`; accepted only if it is one of the page routes above (exact match, no scheme, host, `//`, `\`, query or fragment); otherwise ignored |
| `POST /auth/callback` | Redirects to the accepted `returnTo`, else `/` (was `/configuration`) |
| `GET /auth/session` | Adds `roles` (`editor`, `tester`), `displayName`, `account`, `expiresAt`, `tokenExpiresAt` |
| `POST /auth/session/extend` | **New.** CSRF required. Moves `expiresAt` to now + 15 minutes, capped by `tokenExpiresAt`. `200` with the new `expiresAt`; `401 session_expired` if already expired |
| `POST /auth/logout` | Unchanged |

## Server role checks

| Operation | Check today | Check after this feature |
|-----------|-------------|--------------------------|
| Configuration read/write | editor | editor (unchanged) |
| Console ticket, console socket (browser) | editor **and** tester | editor **and** tester while `TESTER_ONLY_TESTS` is off (shipped default); tester when it is turned on after the security review |
| Status | — | editor or tester |
| Session extend | — | any signed-in session |

## Frame

- Logo: the login page shows the full Caller AI logo (`assets/caller-ai-logo.svg`); the top bar shows the mark (`assets/caller-ai-mark.svg`) with the text "Caller AI" and links to `/`. The favicon is derived from the mark. Design files: [caller-ai-logo.svg](../assets/caller-ai-logo.svg), [caller-ai-mark.svg](../assets/caller-ai-mark.svg).
- Sidebar entries in this order: Dashboard, Community and Agent, Test, Status, Settings; only entries the roles allow are shown; the current entry is highlighted and marked `aria-current="page"`.
- Below 768 px the sidebar collapses behind a menu button.
- Top bar: User Profile (name, account, roles; read-only), User Settings ("coming soon"), Sign out.
- Session warning from `expiresAt` − 5 minutes: "Stay signed in" calls `/auth/session/extend`.
- Leaving the Test page with a running test stops it like an explicit stop and shows a notice.

## Design tokens

One module defines every color, size and font. Required contrast: WCAG 2.1 AA (4.5:1 text, 3:1 large text and essential graphics).

| Token | Use |
|-------|-----|
| `brand` (teal `#197767`) | Primary actions, current page, custom borders |
| `surface`, `surface-muted`, `border` | Page, panels, dividers |
| `text`, `text-muted` | Body and secondary text |
| `default-fill` (slate) + lock icon + "Default" | Default communities and pre-defined agents |
| `custom-fill` (white) + `brand` border + pencil icon + "Custom" | Custom communities and agents |
| `entry` + flag icon + "Entry" | Entry agent |
| `warning` + icon + "Unreachable" | Unreachable community |
| `danger` + icon | Errors and invalid nodes |
| `focus` ring | Visible focus on every control and node |

## Feedback

| Situation | Behavior |
|-----------|----------|
| Choosing a voice | Drop-down of the runtime's voice catalog, grouped by language, labeled "Name — Language (accent), presentation", default marked; read-only when the catalog cannot be loaded |
| Wait over 1 second | Loading indicator on the affected area |
| Delete, publish, remove tool in use | Confirmation naming what changes |
| Save, publish, delete outcome | Success or error message, announced to assistive technology |
| Refused rule | Message naming the rule and the item; nothing changes |
| Another manager published | Non-blocking notice with revision number and Reload |

## Events (management)

| Event | Fields (besides the standard schema fields) |
|-------|--------------------------------------------|
| `ui.page_view` | `page`, `outcome` (`shown`, `login`, `no_access`, `role_required`) |
| `auth.sign_in` | `outcome`, `reason` |
| `auth.session_extend` | `outcome` |
| `configuration.rule_refused` | `rule`, `item_kind` |
| `status.check` | `outcome`, `text`, `voice` (each `reachable`, `unreachable`, `unknown`) |

No event carries names, accounts, prompts, settings values or tokens.
