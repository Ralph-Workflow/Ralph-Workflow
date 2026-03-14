# Ralph GUI Wireframes

This folder is the canonical wireframe system for Ralph GUI.

Treat these wireframes as living documents. They should be updated as product
understanding improves and as new UX findings change what users need from the
interface.

It replaces the old monolithic layout spec with focused, task-based wireframe
documents, and it has now been reviewed in two passes against both
`ralph-gui/docs/designs/acceptance-criteria.md` and
`ralph-gui/docs/designs/ux-acceptance-criteria.md`.

## Folder Map

- `01-shell-and-workspaces.md` - shell, workspace tabs, welcome state, lifecycle, status, notifications tray
- `02-dashboard-and-sessions.md` - dashboard, session list, batch flows, destructive confirmations
- `03-new-session-wizard.md` - prompting, AI Assist, configuration, preflight review, launch/error flows
- `04-run-monitoring.md` - running/paused/failed/completed states, degraded mode, logs, changes, recovery
- `05-worktrees-configuration-and-preferences.md` - worktrees, config forms, TOML fallback, preferences, validation
- `06-supporting-flows.md` - onboarding, search, notifications, templates, help, concepts, agent tools

## Current Review Outcome

The wireframe set now covers both the main flows and the stricter edge cases that
were still missing after the initial reorganization.

- Added or strengthened unhappy-path coverage across the system: loading, empty, stale, offline, validation, disabled, recovery, and confirmation states
- Tightened shell behavior around workspace lifecycle, tab management, shortcut coverage, sidebar persistence, and live status visibility
- Expanded dashboard/session management to cover quick actions, timing context, batch restrictions, partial-success outcomes, and destructive confirmations
- Expanded the New Session Wizard to cover the full AI Assist behavior, collapsed-vs-expanded Step 2 behavior, launch presets, stronger validation, merged preflight review, and preserved drafts
- Expanded run monitoring to distinguish workflow state from transport state, show review history, support richer diff actions, and document reconnect/replay behavior
- Expanded worktree/config/preferences coverage to include richer form surfaces, source/default/override visibility, contextual help, agent chains and drains, agent tools, and more complete preferences sections
- Expanded supporting flows to include blocked onboarding states, runs in global search, contextual search variants, richer notifications, recently used templates, help-rich empty states, Concepts Guide coverage, and fuller agent-tool install/update/test flows

## Coverage Notes

- Multi-workspace and shell coverage: `AC-1`, `AC-2`
- Dashboard and session management: `AC-3`, `AC-4`
- Run monitoring and changes review: `AC-5`
- Worktrees and settings: `AC-6`, `AC-7`, `AC-8`
- Onboarding, search, notifications, templates, help, and agent tools: `AC-9` through `AC-14`
- Cross-cutting UX focus: self-evidence, mental model clarity, recovery, accessibility, keyboard-first use, and status visibility from `ux-acceptance-criteria.md`

## How To Use This Folder

- Use these files as the canonical screen/layout reference instead of `ralph-gui/docs/designs/gui-design.md`
- Treat the wireframes in this folder as living UX artifacts; if new UX findings change the right interaction, flow, state, or explanation, update the affected wireframe as part of that work
- When a product rule, acceptance criterion, or UX principle changes, update the affected wireframe here rather than adding a new monolithic spec elsewhere
- When reviewing UI work, check both the relevant acceptance criteria and the corresponding wireframe in this folder
- If a screen gains a new loading, error, empty, or degraded state, add it to the wireframe as part of the same change rather than deferring it

When a wireframe and a product rule disagree, follow the acceptance criteria and
UX acceptance criteria, then update the wireframe in this folder.
