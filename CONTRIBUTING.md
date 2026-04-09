# Contributing to Ralph Workflow

This document is the contributor-focused counterpart to the project style and architecture guides.
If anything here conflicts with `AGENTS.md`, follow the stricter rule.

## Getting Started

1. Fork the repository on Codeberg
2. Clone your fork locally
3. Build and run the fast test path:

```bash
cargo xtask test -p ralph-workflow --lib --all-features
```

## Source of Truth (Read These First)

- Code style + testing philosophy: `CODE_STYLE.md`
- Required verification commands (no ERROR/WARNING output): `docs/agents/verification.md`
- Reducer/event-loop architecture: `docs/architecture/event-loop-and-reducers.md`
- Effect system + filesystem rules: `docs/architecture/effect-system.md`
- Workspace trait rules (`std::fs` is almost always forbidden): `docs/agents/workspace-trait.md`
- Integration test rules: `docs/agents/integration-tests.md` and `tests/INTEGRATION_TESTS.md`
- Codebase map / where modules live: `docs/architecture/codebase-tour.md`

## Required Verification (Before PR/Completion)

Canonical command:

```bash
cargo xtask verify
```

See `docs/agents/verification.md` for the full checklist (and reference commands).

- Verification passes when required checks complete successfully with **no ERROR/WARNING diagnostics** (informational output is acceptable)
- If any command fails or emits ERROR/WARNING diagnostics, fix it before continuing

This is intentionally stricter than a typical "fmt/clippy/test" checklist because the repository has additional compliance checks.

## Code Style

Ralph is a Rust workspace with strong architectural constraints.

- Default to boring, readable Rust; refactor instead of adding tech debt.
- Keep files/modules/functions small; see limits in `CODE_STYLE.md`.

### Architecture Constraints (Non-Negotiable)

If you change pipeline behavior (phases, retries/fallback, effect sequencing, checkpoint/resume, reducer/event/effect shapes), treat these docs as mandatory reading:

- `CODE_STYLE.md`
- `docs/architecture/event-loop-and-reducers.md`
- `docs/architecture/effect-system.md`

The core contract is:

```
State -> Orchestrator -> Effect -> Handler -> Event -> Reducer -> State
```

## Testing

- TDD is required for code changes: write a failing test first.
- Prefer unit tests for pure logic.
- Integration tests are a separate crate: `tests/` (package `ralph-workflow-tests`).

### Integration Tests (CRITICAL)

Before adding/changing integration tests, read:

- `docs/agents/integration-tests.md`
- `tests/INTEGRATION_TESTS.md`

If you need a starting point, use `tests/integration_tests/_TEMPLATE.rs`.

## Pull Requests

### Guidelines

- Keep PRs focused: one feature/fix per PR.
- Explain the "why" in the PR description and commit messages.
- Update docs when behavior or public APIs change.

### Title Format

Use conventional commit style:

- `feat: ...`
- `fix: ...`
- `refactor: ...`
- `docs: ...`
- `test: ...`
- `chore: ...`

## Notes for AI Agents

If you are an AI agent contributing to this project, read `AGENTS.md` (and provider-specific instructions like `CLAUDE.md`) before making changes.

Important reminders:

- Ralph runs unattended; do not rely on interactive prompts.
- Agent-generated artifacts live under `.agent/` (for example `.agent/tmp/`).
- Do not create temporary markdown files in the repo root or `docs/`; use `tmp/` at the repo root instead.

Also note: the repository root `README.md` is the workspace README and is not intended to be edited by automated agents; crate/product docs live under `ralph-workflow/`.

## Reporting Issues

When reporting issues, include:

1. What you were trying to do
2. Repro steps
3. Full error output
4. Environment info (OS, Rust version, agent/client versions)

## License

By contributing, you agree your contributions are licensed under AGPL-3.0.
