# Ralph Workflow (Python)

> **Write the spec. Wake up to working software.**

Ralph Workflow is a Python 3.12+ CLI for spec-driven, unattended AI coding runs. You describe the task in `PROMPT.md`, point Ralph Workflow at the agent CLIs you already use, and let it run. When the workflow finishes, you come back to code changes, logs, artifacts, and review output you can inspect in your normal git workflow.

For Claude-based runs, Ralph Workflow ships both a default `claude` transport and an explicit `claude-headless` transport. The headless path is the documented non-interactive option; transport details for the default Claude path live in the reference docs rather than this onboarding README.

This package is a good fit when you want more than a demo. Ralph Workflow is designed for the kind of bounded engineering work that should leave you with a working feature, a verified refactor, a serious production-bound draft, or a reviewable implementation foundation.

## What you get

- **Spec-driven unattended runs** for real engineering work such as refactors, test generation, documentation sweeps, and migrations
- **Repo-native workflow files** instead of hidden product state
- **Agent-reviewed output** instead of a long interactive transcript
- **Flexible agent routing** across Claude Code, Codex CLI, OpenCode, and your own configured agents
- **Flexible Claude transport choices** including the explicit `claude-headless` path for documented non-interactive runs
- **Cross-agent routing on supported platforms** — use Claude Code, Codex CLI, OpenCode, or your own configured agents based on your workflow needs
- **A practical default workflow** you can use before inventing anything custom

## Install

### PyPI

```bash
pip install ralph-workflow
ralph --help
```

### pipx

```bash
python -m pip install pipx
python -m pipx ensurepath
pipx install ralph-workflow
ralph --help
```

### From source

```bash
git clone https://codeberg.org/RalphWorkflow/Ralph-Workflow.git
cd Ralph-Workflow/ralph-workflow
pip install -e .
ralph --version
```

Requires Python 3.12+.

## Before your first run

1. Install the agent CLIs you want Ralph Workflow to call.
2. Authenticate those CLIs normally.
3. Pick one small, concrete task for the first run.

Ralph Workflow reuses your existing agent CLI authentication. You do not need to copy provider credentials into a separate hosted system first.

## Quick start

```bash
cd /path/to/your/project
ralph --init
ralph --diagnose
$EDITOR PROMPT.md
ralph
```

What happens in that flow:

- **`ralph --init`** creates the local `.agent/` support files.
- **`ralph --diagnose`** checks whether your configured agents and MCP setup are reachable.
- **`PROMPT.md`** becomes the task spec for the run.
- **`ralph`** starts the unattended workflow.

After `ralph --init`, review the generated `.agent/` support files. If this repository needs a project-local main-config override, run `ralph --init-local-config` to create `.agent/ralph-workflow.toml`, then point the workflow at the agent CLIs you already use for planning, development, and review.

## What to expect from a run

Ralph Workflow is meant to get you to a strong implementation starting point while you are away, not to replace engineering judgment.

A good run should leave you with:

- code that compiles, tests, or clearly shows where work remains
- review artifacts and logs that explain what happened
- a result that is worth continuing from, not discarding and restarting

That may be a finished small task, or it may be a substantial first pass toward production on a larger one.

## Good first tasks

Start with work that is easy to verify:

- add tests to an existing module
- fix known lint failures
- refactor one narrow subsystem
- update documentation backed by existing code

## Depth presets

```bash
ralph -Q     # quick: small fixes, single iteration
ralph        # standard: most features and tasks
ralph -T     # thorough: complex refactors, ten iterations
```

## When Ralph Workflow fits

- Multi-step tasks that outgrow one prompt
- Work you want to review after the fact instead of steering live
- Teams that want AI execution to stay in the repo
- Runs where you want to mix stronger and cheaper models by phase

## When it does not fit

- One-shot interactive prompts
- Pair-programming sessions with constant human steering
- Tiny tasks where setup overhead is not worth it
- Workflows that need unpredictable mid-run human input

## Where the name comes from

Ralph Workflow builds on the original Ralph idea: repeat a strong prompt until the model can make real progress. That loop was simple and powerful. In practice, Ralph Workflow is the Ralph loop on steroids: planning before implementation, verification after development, agent fallbacks, agent-agnostic execution, and customizable pipelines so unattended runs keep moving and teams can review the results with confidence.

## Development and verification

If you are changing Ralph Workflow itself, start with [`CONTRIBUTING.md`](CONTRIBUTING.md) and run the canonical verification command before you finish:

```bash
make verify
```

## Documentation

Use the website and docs for the deeper material this README intentionally leaves out:

- **Homepage:** <https://ralphworkflow.com>
- **Docs:** <https://ralphworkflow.com/docs>
- **Documentation map:** [`../docs/README.md`](../docs/README.md)
- **Maintained Sphinx docs:** [`docs/sphinx/`](docs/sphinx/)
- **Quickstart:** [`docs/sphinx/quickstart.md`](docs/sphinx/quickstart.md)
- **Developer reference:** [`docs/sphinx/developer-reference.md`](docs/sphinx/developer-reference.md)
- **Python API reference:** [`docs/sphinx/modules.rst`](docs/sphinx/modules.rst)
- **Source repository:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Issue tracker:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>

## License

[AGPL-3.0-or-later](LICENSE).

The framework is copyleft. The code Ralph Workflow generates belongs to you — no license encumbrance on outputs. Use it commercially. Use it privately. Use it however you want.
