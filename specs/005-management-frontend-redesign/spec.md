# Feature Specification: Management Frontend Redesign

**Feature Branch**: `005-management-frontend-redesign`

**Created**: 2026-11-11

**Status**: Draft

**Input**: User description: "Redesign the management frontend pages to improve usability and maintainability: a login page, a dashboard, General Settings, Community and Agent, Test and Status pages behind a sidebar and a top bar; default communities and pre-defined agents that are fixed in the community and agent graph; custom communities and agents; per-node settings below the graph; visual cues, interaction, live updates and clear errors in the graph; and a reviewed, consistent color scheme with clear feedback for user actions."

## Clarifications

### Session 2026-11-11

- Q: Which moderation and monitoring functions should the Community and Agent page include in this feature? → A: Show each agent's enabled state and last published change, from existing data only; no usage metrics and no content moderation.
- Q: Which voice, text and other service-wide settings should an editor be able to change on the General Settings page? → A: A defined set published with the configuration and applied to new conversations: voice selection, reply length limit and idle timeout, within the existing limits; all other settings are read-only.
- Q: What should the sidebar "Settings" page contain, given that the sidebar also has "General Settings" and the top bar has "User Settings"? → A: The sidebar has one Settings entry for the service settings (voice, reply length, idle timeout and the read-only values); there is no separate General Settings entry. The top-bar User Settings shows only a "coming soon" notice for now; personal preferences such as dark mode come in a later spec.
- Q: Which default communities should always exist in the graph, each holding its pre-defined agent? → A: Welcome, Routing and Goodbye; each holds only its pre-defined agent, which is its entry agent.
- Q: Should the graph also update live when another manager publishes a change, or only reflect the current manager's own edits? → A: Own edits appear at once; when another manager publishes, a non-blocking notice offers a reload, and the open draft is never changed silently.
- Q: When a manager publishes from the Settings page, should their unpublished graph edits from the Community and Agent page be published at the same time? → A: No; separate drafts. Each page publishes only its own changes as a new revision based on the current active revision.
- Q: Should a community's collapsed or expanded state in the graph be saved for everyone with the configuration, or only for the manager who changed it? → A: Personal: kept per manager on their browser and never published; node positions stay shared with the configuration (superseded below: node positions are personal too).
- Q: What should happen when a manager publishes while an enabled custom community cannot be reached from the Routing community, because no connection leads to it? → A: Publication is allowed, but the community is marked as unreachable on the graph and listed in the publish confirmation.
- Q: What should happen to a manager's unpublished draft when their sign-in session expires? → A: Warn 5 minutes before expiry with a "stay signed in" action; if the session still expires, the draft is lost and the login page says so.
- Q: Which pages should a signed-in user see who holds only the tester role and not the editor role? → A: Dashboard, Status and the Test page, including running tests; the Community and Agent page and Settings stay editor-only (later gated behind `TESTER_ONLY_TESTS`, off until the security review; see FR-003).
- Q: Where should managers manage tools and MCP connections (the external actions agents may call), given that the new sidebar has no entry for them? → A: In a Tools section on the Community and Agent page; agent settings assign them to custom agents.
- Q: When a manager drags a node to a new place, should the new position be saved at once for everyone, or only become part of the next publication? → A: Neither; positions are personal per manager on their browser, like the collapsed state, and are not shared or published.
- Q: Inside a custom community, should managers still draw handover links between agents (which agent may pass the conversation to which), as the current editor allows? → A: Yes; editors draw and remove handover links between agents of the same custom community on the expanded community; default communities have none.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Sign in and move around the application (Priority: P1)

A manager opens the management frontend. Before anything else is shown, a login page with the product logo and a sign-in button appears. The sign-in button takes the manager to the organization's sign-in service; after a successful sign-in the manager lands on the dashboard. Every page from then on shows the same frame: a sidebar with Dashboard, Community and Agent, Test, Status and Settings, and a top bar with the manager's profile and user settings. The manager can reach any page in one click from the sidebar and always sees which page is current.

**Why this priority**: Nothing else in the redesign is reachable without the sign-in and the shared frame. It also closes the gap where an unauthenticated visitor sees a page before signing in today.

**Independent Test**: Open the frontend without a session, sign in, and visit each sidebar entry. Delivers a protected, navigable shell even if the other pages only show their titles.

**Acceptance Scenarios**:

1. **Given** a visitor without a session, **When** they open any address of the management frontend, **Then** they see only the login page with the logo and a sign-in button, and no management content.
2. **Given** the login page, **When** the visitor selects sign in and completes the organization's sign-in successfully, **Then** they arrive on the dashboard with the sidebar and top bar shown.
3. **Given** a visitor who opened a deep link (for example the Test page) without a session, **When** they sign in, **Then** they arrive on the page they asked for instead of the dashboard.
4. **Given** a signed-in manager, **When** they select a sidebar entry, **Then** that page opens and the entry is highlighted as current.
5. **Given** a person who signs in successfully but holds no management role, **When** the sign-in completes, **Then** they see a clear "no access" message with a sign-out option and no management content.
6. **Given** a signed-in manager, **When** they choose sign out from the top bar, **Then** their session ends and they return to the login page.
7. **Given** a signed-in manager whose session expires in 5 minutes, **When** the warning appears and they choose "stay signed in", **Then** the session is extended and their draft is kept; **When** they ignore it and the session expires, **Then** their next action returns them to the login page with a message that the session ended and unpublished changes were discarded, and no unsaved change is reported as saved.

---

### User Story 2 - Shape the community and agent network (Priority: P1)

On the Community and Agent page, a manager sees one graph with the default communities (Welcome, Routing and Goodbye) and their pre-defined agents, plus every custom community and custom agent. Default items look clearly different from custom items. The manager adds custom communities and custom agents, connects custom communities to the Routing community or to each other, drags nodes into place, zooms, and expands or collapses communities. Selecting a node opens that node's settings below the graph; nothing else is shown there until a node is selected. Actions that would break the fixed structure are refused with a clear message.

**Why this priority**: Configuring communities and agents is the core purpose of the management frontend, and the protected default structure prevents managers from breaking the conversation entry and exit paths.

**Independent Test**: Starting from a configuration with only the default communities, add a custom community with two agents, connect it to Routing, change its entry agent, try to delete a default community and a pre-defined agent, and publish. Delivers a working, protected configuration even without the other pages.

**Acceptance Scenarios**:

1. **Given** any configuration, **When** the manager opens the Community and Agent page, **Then** the graph shows the Welcome, Routing and Goodbye communities with their pre-defined agents, and the Welcome community is connected to the Routing community.
2. **Given** the graph, **When** the manager looks at the nodes, **Then** each default community and pre-defined agent is marked as fixed by color, shape and a text label, and each custom item is marked as custom in the same three ways.
3. **Given** no node is selected, **When** the manager looks below the graph, **Then** no settings panel is shown; **When** they select an agent node, **Then** that agent's settings appear below the graph; **When** they select a community node, **Then** that community's settings appear instead.
4. **Given** a default community is selected, **When** the manager tries to delete it, rename it, change its connections, or add an agent to it, **Then** the action is refused, an explanation names the rule, and the graph is unchanged.
5. **Given** a pre-defined agent is selected, **When** the manager edits its settings, **Then** the allowed settings (name and instructions) can be changed; **When** they try to delete it or move it to another community, **Then** the action is refused with an explanation.
6. **Given** the manager adds a custom community, **When** it is created, **Then** it appears in the graph at once with exactly one entry agent and can be connected to the Routing community or to another custom community.
7. **Given** a custom community with several agents, **When** the manager chooses a different entry agent in the community settings, **Then** the graph shows the new entry agent at once; **When** they try to delete the current entry agent or leave the community without one, **Then** the action is refused with an explanation.
8. **Given** a large graph, **When** the manager drags a node, zooms, or collapses a community, **Then** the graph responds immediately, a collapsed community shows its agent count, and the node keeps its new position after the page is reloaded in the same browser, without creating an unpublished change.
9. **Given** unpublished changes, **When** the manager publishes, **Then** a confirmation shows the revision that became active; **When** another manager published first, **Then** the conflict is explained and the manager's draft is kept.
10. **Given** an agent node, **When** the manager looks at it or selects it, **Then** the node shows whether the agent is enabled, and its settings show when and by whom it was last changed in a published revision, or that it is not yet published.

---

### User Story 3 - Test voice and text conversations (Priority: P2)

A manager opens the Test page and runs the existing voice and text tests against the published configuration, with the same controls and limits as today, inside the new frame.

**Why this priority**: The test console exists and works; it must keep working in the new structure, but it adds no new capability.

**Independent Test**: Open the Test page, run a text conversation and a voice conversation, and stop each. Delivers the existing test capability in the new layout.

**Acceptance Scenarios**:

1. **Given** a user with the editor and tester roles (or, once tester-only test access is turned on after the security review, a user with the tester role alone), **When** they open the Test page, **Then** they can choose text or voice, pick microphone and speaker for voice, start, interrupt, mute and stop a test, and see the session status and the revision being tested.
2. **Given** a running test, **When** the manager navigates to another page, **Then** the test is stopped and cleaned up the same way as an explicit stop, and the manager is told so.
3. **Given** a user with the editor role but without the tester role, **When** they open the Test page, **Then** they see an explanation of the missing role and no start control.
4. **Given** a user with only the tester role, **When** they look at the sidebar, **Then** they see Dashboard and Status while tester-only test access is off (the shipped default), and get the role explanation on the Test page, or Dashboard, Test and Status once it is turned on; **When** they open the Community and Agent or Settings address directly, **Then** they see an explanation that the editor role is required and no configuration content.

---

### User Story 4 - Check the service status (Priority: P2)

A manager opens the Status page to see which Foundry service and which model deployments the service is currently connected to, and whether each is reachable.

**Why this priority**: It answers the most common support question ("what is this environment connected to?") without access to the cloud portal.

**Independent Test**: Open the Status page in an environment and compare the shown service and model names with the environment's configuration. Delivers read-only visibility on its own.

**Acceptance Scenarios**:

1. **Given** a signed-in manager, **When** they open the Status page, **Then** they see the connected Foundry service name, the text model deployment and the voice model deployment, each with a reachable or unreachable indicator and the time it was last checked.
2. **Given** the conversation service cannot be reached, **When** the Status page loads, **Then** it shows the service as unreachable with the time of the last successful check, and no other page is blocked.
3. **Given** the Status page, **When** the manager inspects the values shown, **Then** no secret, key, token or credential appears.

---

### User Story 5 - Adjust service settings (Priority: P3)

A manager opens Settings from the sidebar to review and change voice settings, text settings and other service-wide options in one place, with clear indication of what is saved and when it takes effect.

**Why this priority**: Useful consolidation, but the conversation service already runs with reviewed defaults.

**Independent Test**: Change one voice setting and one text setting, save, and confirm in a new test session that the change took effect. Delivers central settings management on its own.

**Acceptance Scenarios**:

1. **Given** a manager with the editor role, **When** they open Settings, **Then** they see the voice settings, the text settings and the other service-wide settings grouped by topic, each with its current value and a short explanation; only voice selection, reply length limit and idle timeout are editable.
2. **Given** a changed setting, **When** the manager saves and publishes, **Then** the page confirms it and states that the change applies to conversations started afterwards.
3. **Given** a manager with the editor role, **When** they enter a value outside its allowed range, **Then** the field explains the range and nothing is saved.

---

### User Story 6 - Profile and sign-out in the top bar (Priority: P3)

From the top bar, a manager sees who is signed in and their roles, sees that personal user settings are coming soon, and signs out.

**Why this priority**: Sign-out is needed, but this does not change what the service does.

**Independent Test**: Open the profile and user settings from the top bar and sign out. Delivers identity visibility and sign-out on its own.

**Acceptance Scenarios**:

1. **Given** a signed-in manager, **When** they open User Profile, **Then** they see their display name, sign-in account and their management roles, read-only.
2. **Given** a signed-in manager, **When** they open User Settings, **Then** they see a "coming soon" notice and no controls.

---

### Edge Cases

- A stored configuration from before this feature (pre-defined agents not in any community): the Community and Agent page shows the default communities with the pre-defined agents inside them, without any migration; default communities are always drawn from the pre-defined agents and are never stored.
- A stored configuration lacks a pre-defined agent: the page shows the problem as a blocking error on the graph and refuses to publish until it is resolved; it never silently recreates or deletes items.
- A custom community whose entry agent is disabled: the community is marked as invalid in the graph and publishing is refused with a message naming the community.
- An enabled custom community that Routing cannot reach through any connection: it is marked as unreachable on the graph and named in the publish confirmation, and publication still proceeds.
- A manager deletes a custom community that other custom communities connect to: the manager is asked to confirm, told which connections will be removed, and the graph updates at once after confirming.
- Two managers edit at the same time: when the first publishes, the second sees a notice with a reload option while keeping their draft; if the second publishes without reloading, the publication is refused as a conflict and the draft is kept for review.
- A manager has unpublished graph edits and publishes a settings change (or the reverse): only the published page's changes go into the new revision; the other draft stays unpublished, and its page shows the newer active revision notice before its next publication.
- The graph has many nodes (for example 20 communities and 100 agents): collapsed communities keep the overview readable and interaction stays responsive.
- A screen narrower than a desktop: the sidebar collapses to a menu control; the graph stays usable with zoom and pan.
- The sign-in service is unavailable or the visitor cancels sign-in: the login page shows a retry message and no partial session is created.
- A page fails to load its data: it shows an error with a retry action instead of an empty or stale view.

## Requirements *(mandatory)*

### Functional Requirements

**Access and frame**

- **FR-001**: The system MUST show a login page with the product logo and a sign-in button to every visitor without a valid session, for every address of the management frontend, and MUST show no management content before sign-in.
- **FR-002**: The sign-in button MUST start the organization's existing single sign-on flow and, on success, MUST return the user to the dashboard, or to the page they originally requested if they followed a deep link within the management frontend.
- **FR-003**: Access MUST follow the user's roles: the editor role opens the Community and Agent page and Settings and allows editing and publishing; the tester role opens the Test page and allows starting tests, without requiring the editor role once tester-only test access is enabled after the security review (until then the Test page requires both roles, as today); either role opens Dashboard and Status. The sidebar MUST show only the pages the user's roles allow, and opening another page's address MUST show an explanation of the missing role and no content of that page. A signed-in user with neither role MUST see a "no access" message and a sign-out option only. Every role check MUST also be enforced by the server, not only by hiding controls.
- **FR-004**: Every page after sign-in MUST show a sidebar with the entries Dashboard, Community and Agent, Test, Status and Settings, limited to the entries the user's roles allow (FR-003), and a top bar with User Profile, User Settings and sign out.
- **FR-005**: The sidebar MUST highlight the current page, MUST reach any page in one selection, and MUST collapse into a menu control on narrow screens.
- **FR-006**: The system MUST warn a signed-in user 5 minutes before their session expires and offer a "stay signed in" action that extends the session without losing any draft. When a session ends or expires, the next action MUST return the user to the login page with an explanation that names any unpublished changes as discarded, and MUST NOT report an unsaved change as saved.

**Dashboard**

- **FR-007**: The dashboard MUST be the landing page after sign-in and MUST, in this feature, show only a welcome heading and links to the main pages; it MUST be laid out so that widgets can be added later without changing the frame.

**Community and Agent page**

- **FR-008**: The Community and Agent page MUST show one graph containing the default communities Welcome, Routing and Goodbye, each holding only its pre-defined agent (Welcome, Routing and Goodbye) as its entry agent, and every custom community and custom agent; the Goodbye community MUST remain reachable from every community as the conversation end.
- **FR-009**: Default communities MUST NOT be deletable, renamable or otherwise modifiable, MUST NOT accept new agents, and MUST always be present; the Welcome community MUST always be the conversation entry point and MUST always be connected to the Routing community, and no change to the Welcome community or its connections MUST be possible.
- **FR-010**: Pre-defined agents MUST always be present and MUST NOT be deletable or movable to another community; only their name and instructions MUST be editable.
- **FR-011**: Managers MUST be able to add, edit and delete custom communities and custom agents; custom agents MUST belong to a custom community.
- **FR-012**: Managers MUST be able to connect a custom community to the Routing community and to other custom communities, and to remove such connections; connections required by the default structure MUST NOT be removable. An enabled custom community that no chain of connections from the Routing community reaches MUST be marked as unreachable on the graph and listed in the publish confirmation; it MUST NOT block publication.
- **FR-013**: Every community MUST have exactly one entry agent at all times; managers MUST be able to choose a different entry agent of a custom community in the community settings, and the system MUST refuse to delete or disable the current entry agent or to leave a community without one.
- **FR-014**: Agent settings MUST appear below the graph only when an agent node is selected, and community settings only when a community node is selected; with nothing selected, no settings panel is shown.
- **FR-015**: Default communities and pre-defined agents MUST be distinguished from custom ones by at least two visual means (for example color and shape) plus a text label, so the difference does not rely on color alone.
- **FR-016**: The graph MUST support dragging and repositioning nodes, zooming in and out, panning, fitting the whole graph to the view, and collapsing and expanding communities; a collapsed community MUST show its agent count. Node positions and the collapsed or expanded state MUST be kept per manager on their browser, MUST NOT create a draft change, and MUST NOT be shared or published; a node without a personal position MUST be placed by an automatic arrangement that does not overlap other nodes.
- **FR-017**: Every add, edit, connect or delete by the current manager MUST be reflected in the graph immediately, without reloading the page. When another manager publishes a new revision while the page is open, the system MUST show a non-blocking notice naming the new revision and offering a reload, and MUST NOT change the open graph or draft until the manager chooses to reload.
- **FR-018**: An action that violates a rule in FR-009 to FR-013 MUST be refused before it changes anything, and the system MUST show a message that names the rule and the affected item; controls for forbidden actions on fixed items MUST be hidden or disabled with an explanation.
- **FR-019**: Deleting a custom community or agent, and publishing, MUST ask for confirmation that names what will change; publishing MUST keep the existing draft, conflict and validation behavior and MUST show the revision that became active.
- **FR-020**: Each agent node and agent settings panel MUST show the agent's enabled state, and the agent settings panel MUST show when and by whom the agent was last changed in a published revision; this uses existing revision and audit data only. Usage metrics and content moderation are out of scope for this feature.
- **FR-037**: The Community and Agent page MUST include a Tools section where editors manage tools and MCP connections with the existing rules, and the settings of a custom agent MUST let editors assign and remove those tools; pre-defined agents MUST NOT receive tools, as today. Removing a tool or connection that agents use MUST ask for confirmation naming the affected agents.
- **FR-038**: Editors MUST be able to draw and remove directed handover links between agents of the same custom community on the expanded community, with the existing handover rules; handover links between agents of different communities MUST NOT be possible, and default communities MUST have no agent handover links. Deleting a custom agent MUST remove its handover links after a confirmation that names them.

**Test page**

- **FR-021**: The Test page MUST provide the existing text and voice test functions with their current controls, limits and storage rules, available to any user with the tester role as set out in FR-003.
- **FR-022**: Leaving the Test page during a running test MUST stop and clean up the test the same way as an explicit stop, and MUST tell the user.

**Status page**

- **FR-023**: The Status page MUST show the connected Foundry service, the text model deployment and the voice model deployment, each with a reachable or unreachable indicator and the time of the last check.
- **FR-024**: The Status page MUST NOT show secrets, keys, tokens or credentials, and MUST remain usable when the conversation service is unreachable.

**Settings**

- **FR-025**: The Settings page (formerly referred to as "General Settings"), opened from the sidebar, MUST show voice settings, text settings and other service-wide settings grouped by topic, with current values and short explanations. Editors MUST be able to change exactly these settings: voice selection (from the voices the service supports), reply length limit and idle timeout, each only within the limits the service already enforces. The reply length limit applies to text replies and delegated work, not to spoken output, and the page says so next to the field. These settings MUST have their own draft, separate from the graph draft, and publishing from the Settings page MUST create a new revision that contains only the settings changes on top of the current active revision; unpublished graph edits MUST NOT be published with them, and publishing the graph MUST NOT publish unpublished settings changes. Published settings MUST apply to conversations started after publication; running conversations keep their values. All other settings, including the connected service and model, MUST be read-only.
- **FR-026**: Saving settings MUST confirm success and state that the change takes effect for new conversations once published, and MUST refuse out-of-range values with a field-level explanation of the allowed range; users without the editor role cannot open Settings (FR-003).
- **FR-027**: The sidebar MUST have exactly one settings entry, Settings, which opens the page in FR-025; there MUST be no separate General Settings entry.

**User Profile and User Settings**

- **FR-028**: User Profile MUST show the signed-in user's display name, sign-in account and management roles, read-only.
- **FR-029**: User Settings in the top bar MUST show a "coming soon" notice and no controls in this feature; personal preferences such as dark mode are out of scope until a later spec.

**Visual design and feedback**

- **FR-030**: All pages MUST use one documented color scheme and set of visual styles, including distinct, consistent styles for default and custom communities and agents, and MUST meet WCAG 2.1 AA contrast for text and essential graphics.
- **FR-031**: The interface MUST highlight the selected item, show a loading indicator for any wait longer than one second, confirm critical actions before they run, and confirm the outcome of every save, publish and delete.
- **FR-032**: Every page and the graph MUST be fully operable with the keyboard and by assistive technology, including selecting nodes and opening their settings.

**Preserved behavior and quality**

- **FR-033**: The redesign MUST preserve existing behavior not explicitly changed here, including the published configuration contract read by the conversation service, revision history, audit records, and text history retention and ownership.
- **FR-034**: The user documentation, runbooks and design documents (with diagrams of the page structure and the default community structure) MUST be updated in the same change, and obsolete descriptions of the old pages MUST be removed.
- **FR-035**: Page views, sign-in outcomes, publications and refused rule violations MUST be recorded as structured events without personal content, consistent with the existing event schema.
- **FR-036**: Automated tests MUST cover sign-in gating, navigation, every rule in FR-009 to FR-013 including refusal messages, node selection and settings display, the Tools section, the Test page, the Status page and the settings pages, and MUST run in continuous integration.

### Key Entities *(include if feature involves data)*

- **Default community**: One of Welcome, Routing or Goodbye; always present, not modifiable, holds exactly one pre-defined agent which is its entry agent. Welcome is the conversation entry point and is connected to Routing. Shown from the pre-defined agents; not stored as a separate item.
- **Pre-defined agent**: The agent of a default community; always present; only its name and instructions can change.
- **Custom community**: A community a manager creates; has a name, routing description, enabled state, exactly one entry agent, and connections to Routing or other custom communities.
- **Custom agent**: An agent a manager creates inside a custom community; has a name, instructions, enabled state and assigned capabilities.
- **Community connection**: A directed link that allows a conversation to move between communities; required links of the default structure are fixed.
- **Agent handover link**: A directed link that allows one agent of a custom community to pass the conversation to another agent of the same community.
- **Graph view preference**: Node positions and the collapsed or expanded state of each community, kept per manager on their browser and never shared or published.
- **Tool and MCP connection**: An external action an agent may call, and the connection it uses; managed in the Tools section of the Community and Agent page and assigned only to custom agents.
- **Service status**: The connected Foundry service, text and voice model deployments, their reachability and the time of the last check; read-only, no secrets.
- **General setting**: A named service-wide value with a type, allowed range, current value and an editable flag. Voice selection, reply length limit and idle timeout are editable and versioned with the published configuration revision; the rest are read-only environment values.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of attempts to open a management page without a session show the login page and no management content.
- **SC-002**: After a successful organization sign-in, users reach the dashboard (or their requested page) within 5 seconds in 95% of attempts.
- **SC-003**: Every page is reachable in one selection from the sidebar, and in a usability check with at least five managers, each finds a named page on the first attempt in at least 90% of tasks. The usability check is deferred to a later stage; this feature verifies one-selection navigation to every named page with automated tests.
- **SC-004**: 100% of attempts to delete or modify a default community, delete or move a pre-defined agent, or leave a community without an entry agent are refused with a message that names the rule, and no such attempt changes the stored configuration.
- **SC-005**: In a usability check, managers add a custom community with two agents, connect it to Routing and publish in under 5 minutes without help in at least 80% of attempts. The usability check is deferred to a later stage; this feature verifies the same flow with an automated test that finishes within 5 minutes.
- **SC-006**: With 20 communities and 100 agents, dragging, zooming and expanding or collapsing communities respond without visible delay (feedback within 100 milliseconds), and a change appears in the graph within 1 second.
- **SC-007**: In a usability check, managers correctly identify whether a node is default or custom in at least 95% of cases, including in grayscale. The usability check is deferred to a later stage; this feature verifies with an automated grayscale rendering check that default and custom nodes differ by shape, icon and label.
- **SC-008**: All text and essential graphics meet WCAG 2.1 AA contrast, and all page functions, including node selection, pass a keyboard-only walkthrough.
- **SC-009**: The Status page matches the environment's actual Foundry service and model deployments in 100% of checks and never shows a secret.
- **SC-010**: Every existing text and voice test scenario passes unchanged on the Test page.
- **SC-011**: When another manager publishes, managers with the page open see the new-revision notice within 30 seconds, and their open draft is unchanged in 100% of cases until they choose to reload.

## Assumptions

- Default communities are a fixed view of the three pre-defined agents, not stored items: existing configurations, in which the Welcome, Routing and Goodbye agents belong to no community, show them inside their default communities without any change to stored data, and no custom item changes.
- "Cannot be removed or replaced" for the entry agent means a community can never be without one; choosing another agent of the same community as entry agent is allowed.
- Only the Welcome community's own settings and connections are fully frozen; the Welcome pre-defined agent's name and instructions follow the same rule as other pre-defined agents and remain editable.
- Sign-in keeps the existing organization single sign-on, session lifetime and role names; no new identity provider or role is introduced. Letting browser users with only the tester role run tests relaxes today's rule that browser tests need both roles; it ships switched off and is enabled only after the security review required by the constitution is recorded, and the existing per-user test limits still apply.
- The current draft, publish, conflict and revision behavior, the published configuration format read by the conversation service (extended only as needed to record which custom communities Routing may reach and the three editable general settings; default communities are not stored and node positions are no longer stored in it), and the test console limits stay as they are.
- The dashboard holds no metrics in this feature; widgets come in later specs.
- The Status page shows only the Foundry service and model deployments in this feature; more details come later.
- The frontend is used on desktop browsers; narrow screens must remain usable but are not optimized beyond the collapsible sidebar.
- Personal user preferences, including dark mode, are deferred to a later spec; the top-bar User Settings entry is a placeholder until then.
