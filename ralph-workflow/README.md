# ralph-workflow

Ralph Workflow is a free, open-source orchestrator for AI coding agents.
Hand it a well-specified task, let agents plan, build, verify, and fix,
and come back to reviewable, tested work.

## Install

```bash
pipx install ralph-workflow
ralph --version
```

`pipx` keeps the install isolated from your other Python projects; the
post-condition is that `ralph --version` prints the installed package
version.

## First run

The complete first-run path is six short steps and does not require
opening any other config file before your first run.

1. **Install Ralph Workflow.** Use `pipx install ralph-workflow` (or
   `pip install ralph-workflow`).
2. **Start in your project.** `cd /path/to/your/project` and run
   `ralph --init`. It creates your user-global config and a `PROMPT.md`;
   project-local config is optional later via `ralph --init-local-config`.
3. **Confirm a coding agent.** Ralph Workflow looks for supported agents
   already on your `PATH` and enables the ones it finds. Install and
   authenticate an agent first if none are found.
4. **Check the setup.** Run `ralph --diagnose` and fix any reported
   problem before starting work.
5. **Describe the task.** Edit `PROMPT.md` with the outcome and checks
   you expect. For a task-shaped starter, use `ralph --init feature-spec`,
   `guardrail`, `refactor`, `test-coverage`, or `docs` before a prompt
   file exists.
6. **Run Ralph Workflow.** Run `ralph`, then read the finish-receipt
   artifact: it names the change, checks run, and review focus before you
   decide what to do next.

The canonical first-run walkthrough is [Getting started](docs/sphinx/getting-started.md).
For agent-specific model-string formats, see
[Agent compatibility](docs/sphinx/agent-compatibility.md).

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
