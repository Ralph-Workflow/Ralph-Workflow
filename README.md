# Ralph Workflow

> **The operating system for autonomous coding.**

[![Codeberg](https://img.shields.io/badge/Codeberg-Primary-blue?logo=codeberg)](https://codeberg.org/RalphWorkflow/Ralph-Workflow)
[![GitHub Mirror](https://img.shields.io/badge/GitHub-Mirror-lightgray?logo=github)](https://github.com/Ralph-Workflow/Ralph-Workflow)

> **GitHub is the mirror. Codeberg is the primary repo.**
> Inspect, star, watch, fork, and open issues on Codeberg first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>

Ralph Workflow is a free and open-source **AI agent orchestration CLI** for substantial, well-specified software engineering on your own machine.
It is not the right tool for tiny tweaks, vague exploration, or narrow chores that a single agent can finish quickly.

It takes the simple Ralph loop idea and turns it into a **composable workflow system** for planning, implementation, verification, review, and agent routing.
The core stays simple. That simplicity is what makes more complex workflows easier to build, easier to configure, and easier to extend.

Ralph Workflow also ships with a **strong default workflow for writing software**.
You can use that default as-is, or build on top of it when you need something more advanced.

## The route to use

1. [START_HERE.md](START_HERE.md) — copy-paste first run on one real task
2. [docs/first-task-guide.md](docs/first-task-guide.md) — pick a task you can judge honestly tomorrow morning
3. [content/examples/workflow_composition_example.md](content/examples/workflow_composition_example.md) — see how the task, implementation, verification, and review handoff fit together
4. [content/examples/tomorrow_morning_scorecard.md](content/examples/tomorrow_morning_scorecard.md) — judge the first run in 10 minutes instead of trusting the summary
5. [content/examples/review_bundle_example.md](content/examples/review_bundle_example.md) — see the morning-after handoff you should expect before you trust the run
6. [docs/when-to-use-ralph-workflow.md](docs/when-to-use-ralph-workflow.md) — decide if you need a workflow instead of another chat or editor loop
7. [docs/README.md](docs/README.md) — curated docs switchboard after that

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
