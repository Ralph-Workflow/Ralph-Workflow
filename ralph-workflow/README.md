# ralph-workflow

Ralph Workflow is a free, open-source AI agent orchestrator for coding
work. You give it one well-specified task; it runs a **Ralph loop**
(plan → build → verify → fix) with your chosen coding agent, then you
come back to inspect the result. Adopt the default workflow as-is
first; extend it later.

It fits developers and small teams with work that is too big to babysit
and too risky to trust blindly. It is not for vague prompts or repos
without tests or other guardrails.

## Install

```bash
pipx install ralph-workflow
ralph --version
```

`pipx` keeps the install isolated from your other Python projects; the
post-condition is that `ralph --version` prints the installed package
version. `pip install ralph-workflow` also works.

## First run

1. Install Ralph Workflow (`pipx` or `pip`, above).
2. In your project: `ralph --init` — creates user-global config and a
   starter `PROMPT.md`. Project-local config is optional later via
   `ralph --init-local-config`.
3. Confirm a supported agent CLI is on your `PATH` and authenticated
   (`ralph --list-agents`).
4. Run `ralph --diagnose` and fix any reported problem until every line
   is green.
5. Edit `PROMPT.md` with the outcome and checks you expect. Remove the
   `<!-- ralph:starter-prompt ... -->` sentinel at the top — Ralph Workflow
   refuses to run while it remains. Or, before `PROMPT.md` exists, seed a
   task-shaped starter with `ralph --init feature-spec`, `guardrail`,
   `refactor`, `test-coverage`, or `docs`.
6. Run `ralph`. When it finishes, read the summary of what changed and
   which checks passed, then verify the feature yourself.

The canonical first-run walkthrough is
[Getting started](docs/sphinx/getting-started.md). For agent-specific
model-string formats, see
[Agent compatibility](docs/sphinx/agent-compatibility.md). For
configuration after the first run, open the
[operator manual](docs/sphinx/index.rst).

## Supported agents

Eight built-in agents ship with Ralph Workflow:

| Agent | Notes |
|---|---|
| **Claude Code** | Anthropic's CLI for Claude (interactive, PTY transport). |
| **Claude Code (Headless)** | Same `claude` binary in headless subprocess mode (`claude-headless`). |
| **Codex** | OpenAI's Codex CLI. |
| **OpenCode** | Open-source terminal coding agent. |
| **Nanocoder** | Local-only TUI coding agent. |
| **Google Anti Gravity (AGY)** | Google's Antigravity CLI (`agy`). Re-check after AGY updates. |
| **Pi** | Minimal coding agent. Headless mode is `pi --mode json <prompt>`. |
| **Cursor** | Cursor Agent CLI (`agent`), headless `--print` mode. |

Pick one, authenticate it on your machine once, and Ralph Workflow uses
it. Selection and trust-boundary details are in
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

- **Repository:** <https://github.com/Ralph-Workflow/Ralph-Workflow>
- **PyPI:** <https://pypi.org/project/ralph-workflow/>
- **Issue tracker:** <https://github.com/Ralph-Workflow/Ralph-Workflow/issues/new>
- **Contribution route:**
  [`CONTRIBUTING.md`](CONTRIBUTING.md)
