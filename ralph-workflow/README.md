# ralph-workflow

> **Primary:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
> GitHub mirror: <https://github.com/Ralph-Workflow/Ralph-Workflow> (verify: repo-exists)

This README is the PyPI-facing surface. Product positioning, install
steps, first-run path, and trust boundaries live in the repository
root [`README.md`](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/README.md) — that page is the single source of truth.

## Install and prove the install

```bash
pipx install ralph-workflow
ralph --version
ralph --diagnose   # optional pre-flight check
```

## Supported agents

| Agent | Notes |
|---|---|
| **Claude Code** | Anthropic's CLI for Claude (interactive + headless). |
| **Codex** | OpenAI's Codex CLI. |
| **OpenCode** | Open-source terminal coding agent. |
| **Nanocoder** | Local-only TUI coding agent. |
| **Google Anti Gravity (AGY)** | Google's Antigravity CLI (`agy`, v1.0.9+). |
| **Pi** | Minimal coding agent. Headless mode is `pi --mode json <prompt>`. |

See the repository root [`README.md`](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/README.md) for the install → init → diagnose → spec → run → review walkthrough, and the [operator manual](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/ralph-workflow/docs/sphinx/index.rst) for configuration, diagnostics, and troubleshooting reference.
