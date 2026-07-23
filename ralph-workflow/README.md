# ralph-workflow

Ralph Workflow is a free, open-source orchestrator for AI coding agents.
Hand it a well-specified task, let agents plan, build, verify, and fix,
and come back to reviewable, tested work.

## Install

```bash
pipx install ralph-workflow
ralph --version
ralph --diagnose   # optional pre-flight check
```

`pipx` keeps the install isolated from your other Python projects; the
post-condition is that `ralph --version` prints the installed package
version.

The canonical first-run walkthrough — install → init → diagnose → edit
PROMPT.md → run — is in
[Getting started](docs/sphinx/getting-started.md). It is the single home
for the first-run sequence and does not require opening any other config
file before your first run.

## Supported agents

Eight built-in agents ship with Ralph Workflow:

| Agent | Notes |
|---|---|
| **Claude Code** | Anthropic's CLI for Claude (interactive, PTY transport). |
| **Claude Code (Headless)** | Same `claude` binary in headless subprocess mode (`claude-headless`). |
| **Codex** | OpenAI's Codex CLI. |
| **OpenCode** | Open-source terminal coding agent. |
| **Nanocoder** | Local-only TUI coding agent. |
| **Google Anti Gravity (AGY)** | Google's Antigravity CLI (`agy`, v1.0.9+). |
| **Pi** | Minimal coding agent. Headless mode is `pi --mode json <prompt>`. |
| **Cursor** | Cursor Agent CLI (`agent`), headless `--print` mode. |

Pick one, authenticate it on your machine once, and Ralph Workflow uses
it. The selection and trust-boundary story is in the maintained
[Sphinx manual](docs/sphinx/index.rst) under
[agents](docs/sphinx/agents.md) and
[agent-compatibility](docs/sphinx/agent-compatibility.md).

## Requirements

- Python ≥ 3.12
- Local execution; no daemon, no cloud dependency
- One supported agent CLI installed and authenticated

## License

AGPL-3.0-or-later.

## Documentation

The maintained operator manual is at
[`docs/sphinx/index.rst`](docs/sphinx/index.rst) — tutorial,
configuration reference, MCP / artifact / pipeline configuration,
concepts, troubleshooting, diagnostics, and developer internals.

## Project home

- **Repository:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **PyPI:** <https://pypi.org/project/ralph-workflow/>
- **Issue tracker:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>
- **Contribution route:**
  [`CONTRIBUTING.md`](CONTRIBUTING.md)
