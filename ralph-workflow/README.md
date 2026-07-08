# ralph-workflow

> **Codeberg is primary:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
> GitHub is a read-only mirror:
> <https://github.com/Ralph-Workflow/Ralph-Workflow> (verify: repo-exists)

> **Autopilot for coding agents** — run Claude Code, Codex, OpenCode,
> Nanocoder, AGY & Pi unattended with verification, recovery,
> orchestration.

This README is the PyPI-facing surface. The canonical product
positioning, install steps, first-run path, trust and safety boundaries,
and finish-receipt example live in the
[repository root `README.md`](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/README.md).
That page is the single source of truth; PyPI readers should follow
the link above for the install → init → diagnose → spec → run → review
walkthrough.

## At a glance

- **Python:** `>=3.12`
- **License:** AGPL-3.0-or-later
- **Runtime:** local-first; no required cloud account
- **Auth:** your agent CLIs authenticate as they always do — Ralph Workflow does not store, read, or proxy credentials

## Install and prove the install

```bash
pipx install ralph-workflow
ralph --version
ralph --diagnose     # optional pre-flight check
```

## Supported agents

| Agent | Notes |
|---|---|
| **Claude Code** | Anthropic's CLI for Claude (interactive + headless). Canonical reference agent. |
| **Codex** | OpenAI's Codex CLI. |
| **OpenCode** | Open-source terminal coding agent. |
| **Nanocoder** | Local-only TUI coding agent. |
| **Google Anti Gravity (AGY)** | Google's Antigravity CLI (`agy`, v1.0.9+). |
| **Pi** | Minimal coding agent. Headless mode is `pi --mode json <prompt>`. |

## Where to go next

- [Repository root `README.md`](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/README.md)
- [Operator manual `docs/sphinx/index.rst`](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/ralph-workflow/docs/sphinx/index.rst)
- [Issue tracker](https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new)
