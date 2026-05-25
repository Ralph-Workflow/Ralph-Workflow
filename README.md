# Ralph Workflow

> **The operating system for autonomous coding.**

[![PyPI](https://img.shields.io/pypi/v/ralph-workflow.svg)](https://pypi.org/project/ralph-workflow/)
[![Python](https://img.shields.io/pypi/pyversions/ralph-workflow.svg)](https://pypi.org/project/ralph-workflow/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

> **GitHub is the mirror. Codeberg is the primary repo.**
> Inspect, star, watch, fork, and open issues on Codeberg first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>

Ralph Workflow is a free and open-source **AI agent orchestrator** for substantial, well-specified software engineering on your own machine.

It takes the simple Ralph-loop idea and turns it into a **composable workflow system** for planning, implementation, verification, review, and agent routing.
The core stays simple. That simplicity is what makes more complex workflows easier to build, easier to configure, and easier to extend.

Ralph Workflow also ships with a **strong default workflow for writing software**.
It follows a convention-over-configuration approach: start with the shipped path, then build on top of it only when you need something more advanced.

Bring the coding agents you already trust.
Ralph Workflow plugs into your existing setup instead of turning “hand over your API keys” into the main product contract.

## The route to use

1. [START_HERE.md](START_HERE.md) — shortest honest first run
2. [docs/README.md](docs/README.md) — curated docs switchboard
3. [ralph-workflow/docs/sphinx/index.rst](ralph-workflow/docs/sphinx/index.rst) — operator manual and configuration reference

## Install

```bash
pipx install ralph-workflow
ralph --help
```

Requires Python 3.12+.

## Before your first run

Make sure the agent CLIs you want Ralph Workflow to call are already installed and authenticated.
Ralph Workflow does not replace those coding agents. It orchestrates them.

## License

[AGPL-3.0-or-later](LICENSE).
