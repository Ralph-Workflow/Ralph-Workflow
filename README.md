# Ralph Workflow

> **The operating system for autonomous coding.** Run overnight. Review in the morning.

[![Codeberg](https://img.shields.io/badge/Codeberg-Primary-blue?logo=codeberg)](https://codeberg.org/RalphWorkflow/Ralph-Workflow)
[![GitHub Mirror](https://img.shields.io/badge/GitHub-Mirror-lightgray?logo=github)](https://github.com/Ralph-Workflow/Ralph-Workflow)
[![PyPI](https://img.shields.io/pypi/v/ralph-workflow?color=green&label=pypi)](https://pypi.org/project/ralph-workflow/)
[![Downloads](https://img.shields.io/pypi/dm/ralph-workflow?color=green)](https://pepy.tech/projects/ralph-workflow)

> **GitHub is the mirror. Codeberg is the primary repo.**
> Star, watch, fork, and open issues on Codeberg: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>

**Ralph Workflow** orchestrates the coding agents you already use into a composable overnight workflow — plan, implement, verify, and review, on your own machine, with your own keys.

For substantial, well-specified software engineering tasks. Not for tiny tweaks or vague exploration.

## Quick start (60 seconds)

```bash
pipx install ralph-workflow    # requires Python 3.12+
cd /path/to/your/project
ralph --init                    # setup baseline
ralph --diagnose                # check health
$EDITOR PROMPT.md               # describe your task
ralph                           # run overnight
```

Wake up to a review bundle — what changed, what passed, what didn't.
[START_HERE.md](START_HERE.md) has the full walkthrough.

## Why Ralph Workflow

- **Runs unattended.** Describe the task, go to sleep, judge the result in the morning.
- **Composable.** Plan → implement → verify → review — pick what you need.
- **Your machine, your keys.** No vendor lock-in. No cloud agents.
- **Strong defaults.** Ships with a battle-tested workflow. Customize when you're ready.

## Reading path

| You want to… | Read this |
|---|---|
| Copy-paste your first real run | [START_HERE.md](START_HERE.md) |
| Pick a task you can judge honestly | [docs/first-task-guide.md](docs/first-task-guide.md) |
| See the full workflow structure | [content/examples/workflow_composition_example.md](content/examples/workflow_composition_example.md) |
| Judge a run in 10 min tomorrow morning | [content/examples/tomorrow_morning_scorecard.md](content/examples/tomorrow_morning_scorecard.md) |
| See what a review bundle looks like | [content/examples/review_bundle_example.md](content/examples/review_bundle_example.md) |
| Decide if you need workflows at all | [docs/when-to-use-ralph-workflow.md](docs/when-to-use-ralph-workflow.md) |
| Everything else | [docs/README.md](docs/README.md) |

## Before your first run

Install and authenticate the agent CLIs you want Ralph Workflow to orchestrate.
Ralph Workflow doesn't replace your coding agents — it composes them into a workflow.

## License

[AGPL-3.0-or-later](LICENSE).
