# Ralph Workflow vs Aider

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


If you already know Aider, the simplest difference is this:

- **Aider** is an interactive AI pair-programming tool you steer in the terminal.
- **Ralph Workflow** is an **open-source** orchestration CLI that runs the coding agents you already use **on your own machine** and hands back a **strong software result** after an unattended run.

That means Ralph Workflow is for developers and technical teams with work that is **too big to babysit and too risky to trust blindly**.

Why try Ralph Workflow now? You can keep the agents you already use, hand off one real backlog task tonight, and decide tomorrow whether the result is something you would actually merge.

Before you install anything, inspect the **primary Codeberg repo** first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>

If Ralph Workflow earns trust after a real overnight run, keep the public next step on Codeberg:

- **Star or watch the primary repo:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Report first-run friction or proof gaps:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>
- **Use GitHub only as the mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>

## The core difference

Aider is strongest when you want to stay in the loop.

You ask, inspect, redirect, ask again, and co-edit with the model in real time.

Ralph Workflow is strongest when you want to get out of the loop for a while.

You write a bounded spec in `PROMPT.md`, Ralph runs planning, development, and review as one unattended flow, and you come back to a diff, checks, logs, and artifacts you can inspect like normal engineering work.

## Choose Ralph Workflow when

Ralph Workflow is usually the better fit when you want to:

- hand off a real backlog task and review it later
- wake up to a large chunk of work instead of babysitting the terminal
- use different agents for planning, development, and review
- keep the workflow repo-native and inspectable on your own machine
- judge the result with a simple merge / no-human review

Typical good Ralph tasks:

- a bounded feature slice
- a narrow refactor with tests
- a cleanup pass with obvious verification
- repetitive implementation work with clear acceptance criteria

## Choose Aider when

Aider is usually the better fit when you want to:

- pair-program interactively in the terminal
- keep steering the model every few minutes
- make small edits while you stay present
- iterate conversationally instead of handing off a full work unit

## Why some teams use both

These tools are not enemies.

A practical split is:

- use **Aider** for fast interactive editing during the day
- use **Ralph Workflow** for unattended overnight runs on tasks that are too big to keep nudging manually

If your current pain is not "how do I edit with AI faster?" but "how do I come back to something reviewable tomorrow morning?", Ralph Workflow is the sharper fit.

## What makes Ralph Workflow different

Ralph Workflow is not another chat window or terminal pairing loop.

It is built around a different handoff:

- a real diff
- checks that actually ran
- artifacts saved in the repo
- review output you can inspect
- enough context to answer: **does the implementation hold up?**

That is the real product test.

## Fastest honest first test

Before you start, have at least one supported agent CLI already installed and already authenticated on your own machine. Ralph Workflow is open source, but it does not replace the coding agent itself.

Then run:

```bash
pipx install ralph-workflow
cd /path/to/your/project
ralph --init
ralph --diagnose
$EDITOR PROMPT.md
ralph
```

Use one real backlog task, not a vague demo.

If you want help picking that first task, read [when unattended coding fits](./when-unattended-coding-fits.md), [the first-task guide](./first-task-guide.md), and [first-task prompt templates](./first-task-prompt-templates.md).

If you want to see the kind of morning-after handoff Ralph is aiming for before you install, inspect [what good output looks like](./free-open-source-proof.md) and the [example review bundle](../examples/first-review-bundle/README.md).

After that first real run, take exactly one public next step on Codeberg:

- **promising run:** star or watch the repo
- **shaky run:** open the right issue on Codeberg
