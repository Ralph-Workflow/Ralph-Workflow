# Ralph Workflow

> **The operating system for autonomous coding.**

[![PyPI](https://img.shields.io/pypi/v/ralph-workflow.svg)](https://pypi.org/project/ralph-workflow/)
[![Python](https://img.shields.io/pypi/pyversions/ralph-workflow.svg)](https://pypi.org/project/ralph-workflow/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

> **GitHub is the mirror. Codeberg is the primary repo.**
> Inspect, star, watch, fork, and open issues on Codeberg first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>

Ralph Workflow is a free and open-source **AI agent orchestrator** for substantial, well-specified software engineering on your own machine.

Ralph Workflow is an improvement on Ralph: it takes the simple Ralph-loop idea and turns it into a **composable workflow system** for planning, implementation, verification, review, and agent routing.
The core stays simple. That simplicity is what makes more complex workflows easier to build, easier to configure, and easier to extend.

Ralph Workflow also ships with a **strong default workflow for writing software**.
You can use that default as-is, or build on top of it when you need something more advanced.

## The route to use

1. [START_HERE.md](START_HERE.md) — shortest honest first run
2. [docs/README.md](docs/README.md) — curated docs switchboard
3. [ralph-workflow/docs/sphinx/index.rst](ralph-workflow/docs/sphinx/index.rst) — operator manual and configuration reference

## What it is not for

Ralph Workflow is not for tiny edits, vague exploration, or work where setup would dominate the task.
Start with a substantial, well-specified repo task that has a visible finish line.

## Install

```bash
pipx install ralph-workflow
ralph --help
```

Requires Python 3.12+.

## Before your first run

Make sure the agent CLIs you want Ralph Workflow to call are already installed and authenticated.
Ralph Workflow does not replace those coding agents. It orchestrates them.

Use your existing agents. Keep your existing setup. Keep your keys to yourself.
Ralph Workflow should not become the place where your model-provider secrets live unless you explicitly choose that path.

## License

[AGPL-3.0-or-later](LICENSE).
