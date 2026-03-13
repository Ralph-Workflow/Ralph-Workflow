# Ralph Workflow - Wireframe System Reference

Terminology in this document follows `ralph-gui/docs/glossary.md`.

This file replaces the former monolithic GUI design document.

The canonical screen and layout source is now the wireframe system in
`ralph-gui/docs/wireframes/`. Product behavior, UX review, acceptance criteria,
visual rules, and backend contracts should all point to those focused wireframe
documents instead of maintaining duplicate layout specs here.

## Canonical Wireframe Entry Point

- `ralph-gui/docs/wireframes/README.md`

## Wireframe Documents

- `ralph-gui/docs/wireframes/01-shell-and-workspaces.md`
  - App shell, workspace tabs, welcome state, workspace open error
- `ralph-gui/docs/wireframes/02-dashboard-and-sessions.md`
  - Dashboard, active runs, attention states, session list, batch progress
- `ralph-gui/docs/wireframes/03-new-session-wizard.md`
  - Prompt authoring, AI Assist, configuration, review, launch failure
- `ralph-gui/docs/wireframes/04-run-monitoring.md`
  - Running, paused, failed, completed, diff review
- `ralph-gui/docs/wireframes/05-worktrees-configuration-and-preferences.md`
  - Worktrees, worktree creation, configuration, raw TOML, preferences
- `ralph-gui/docs/wireframes/06-supporting-flows.md`
  - Onboarding, search, notifications, templates, help, agent tools manager

## How To Use This Document Set

- Use `ux-acceptance-criteria.md` for UX review rules
- Use `acceptance-criteria.md` for implementation completion checks
- Use `design-criteria.md` for visual and interaction standards
- Use the relevant file in `ralph-gui/docs/wireframes/` for layout, structure,
  states, and annotations

If a layout needs to change, update the applicable wireframe file rather than
expanding this reference document.
