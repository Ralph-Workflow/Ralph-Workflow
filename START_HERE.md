# Start Here: Run Ralph Workflow on One Real Task

> **GitHub is the mirror. Codeberg is the primary repo.**
> Inspect and follow Ralph Workflow on Codeberg first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>

Ralph Workflow is a free and open-source AI agent orchestration system built around a simple Ralph-loop core.
It runs the coding agents you already use on your own machine, turns that simple loop structure into a stronger composable workflow for substantial, well-specified software engineering work, and gives you a strong default workflow before you customize anything.

Start with one real, ambitious, well-specified engineering task and judge the outcome by what the software does now and what checks ran.

## Before you start

Have these ready:

- one real git repo you care about
- Python 3.12+
- one supported agent CLI already installed
- working auth for that agent

## Pick the right first task

Good first tasks:

- a serious application slice with clear acceptance criteria
- a major product milestone that is already well specified
- a substantial engineering chunk with real finish-line checks
- the side project you actually want built, as long as the spec is concrete

Bad first tasks:

- tiny edits where setup dominates the work
- narrow chores a single agent could finish quickly
- vague exploration
- risky production surgery
- work that depends on constant mid-run steering

If you are unsure, use [docs/first-task-guide.md](./docs/first-task-guide.md).

## Copy this into `PROMPT.md`

Use a one-paragraph contract instead of a loose wish list:

```md
Change:
[what should change]

Keep unchanged:
[what must stay stable]

Done means:
[observable outcome]

Checks:
[tests, lint, build, screenshots, or other verification]
```

Fast example:

```md
Change:
Add CSV export to the billing history page.

Keep unchanged:
Do not change invoice creation, billing calculations, or existing filters.

Done means:
Users can export the currently filtered billing-history rows to CSV from the page.

Checks:
Relevant billing tests pass, any new billing-history tests pass, and the app build succeeds.
```

## Install and run

```bash
pipx install ralph-workflow
cd /path/to/your/project
ralph --init
ralph --diagnose
$EDITOR PROMPT.md
ralph
```

## What success looks like

A good first run should leave you with:

- a real repo change that matches the task you wrote down
- explicit verification output you can inspect without reading a giant transcript
- a clear sense of whether Ralph Workflow helped on a task ambitious enough to justify orchestration
- an obvious next decision: keep iterating, adjust the prompt, or choose a better-scoped task

If the run only gives you activity or narration without a convincing repo outcome, treat that as a miss and tighten the task before trying again.

If you want to see the expected handoff shape before you run, read [content/examples/review_bundle_example.md](./content/examples/review_bundle_example.md).
If you want a fast pass/fail checklist for the next morning, use [content/examples/tomorrow_morning_scorecard.md](./content/examples/tomorrow_morning_scorecard.md).

## Tomorrow-morning review card

Ignore how confident the agent sounded and ask only:

- does the diff match the task?
- did the promised checks actually run?
- is the output small enough to review in one sitting?
- are open risks called out clearly?
- **would I merge this?**

If the answer is yes, follow the project on **Codeberg** first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>

## Next pages only if you need them

- task selection — [docs/first-task-guide.md](./docs/first-task-guide.md)
- tomorrow-morning scorecard — [content/examples/tomorrow_morning_scorecard.md](./content/examples/tomorrow_morning_scorecard.md)
- morning-after review bundle example — [content/examples/review_bundle_example.md](./content/examples/review_bundle_example.md)
- choosing Ralph Workflow vs another chat/editor loop — [docs/when-to-use-ralph-workflow.md](./docs/when-to-use-ralph-workflow.md)
- workflow composition walkthrough — [content/examples/workflow_composition_example.md](./content/examples/workflow_composition_example.md)
- docs switchboard — [docs/README.md](./docs/README.md)
- [Workflow composition example](./content/examples/workflow_composition_example.md)
