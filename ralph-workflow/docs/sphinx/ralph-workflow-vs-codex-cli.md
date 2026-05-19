# Ralph Workflow vs Codex CLI

If you already use Codex CLI, the simplest difference is this:

- **Codex CLI** is a direct coding agent you drive yourself.
- **Ralph Workflow** is a **free and open-source** orchestration CLI that runs Codex CLI or another supported coding agent **on your own machine** and hands back a **reviewable result** after an unattended run.

That means Ralph Workflow is for developers and technical teams with work that is **too big to babysit and too risky to trust blindly**.

Why try Ralph Workflow now? Because you do not need to replace Codex CLI to use it. You can keep Codex in the loop, hand off one real backlog task tonight, and decide tomorrow whether the result is something you would actually merge.

## The core difference

Codex CLI is strongest when you want to stay in the loop.

You prompt, inspect, redirect, and keep steering the session live.

Ralph Workflow is strongest when you want to get out of the loop for a while.

You write a bounded spec in `PROMPT.md`, Ralph Workflow runs planning, development, verification, and review as one unattended flow, and you come back to a diff, checks, logs, and artifacts you can inspect like normal engineering work.

## Choose Ralph Workflow when

Ralph Workflow is usually the better fit when you want to:

- hand off a real backlog task and review it later
- wake up to a large chunk of work instead of reopening the terminal all night
- keep Codex CLI but add a stronger morning-after handoff
- route planning, implementation, and review through different agents when needed
- judge the result with a simple merge / no-merge decision

Typical good Ralph Workflow tasks:

- a bounded feature slice
- a narrow refactor with tests
- a cleanup pass with obvious verification
- repetitive implementation work with clear acceptance criteria

## Choose Codex CLI when

Codex CLI is usually the better fit when you want to:

- pair-program interactively in the terminal
- keep steering the work every few minutes
- explore a codebase conversationally
- make small edits while you stay present
- iterate live instead of handing off a full work unit

## Why some teams use both

These tools solve different parts of the job.

A practical split is:

- use **Codex CLI** for live exploration, implementation bursts, and quick iteration during the day
- use **Ralph Workflow** for unattended evening or overnight runs where handoff quality matters as much as model quality

If your current pain is not "how do I get Codex to respond faster?" but "how do I come back to something reviewable tomorrow morning?", Ralph Workflow is the sharper fit.

## What makes Ralph Workflow different from just running Codex longer

The difference is not simply more model time.

Ralph Workflow is built around a different finish line:

- a real diff
- checks that actually ran
- artifacts saved in the repo
- review output you can inspect
- enough context to answer: **would I merge this?**

That is the real product test.

## Fastest honest first test

Before you start, have Codex CLI or another supported agent CLI already installed and already authenticated on your own machine. Ralph Workflow is free and open source, but it does not replace the coding agent itself.

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

If you want help picking that first task, read [When Unattended Coding Fits](when-unattended-coding-fits.md), [Choose Your First Ralph Workflow Task](first-task-guide.md), and [First-Task Prompt Templates](first-task-prompt-templates.md).

If you want to see the kind of morning-after handoff Ralph Workflow is aiming for before you install, inspect [What Good Ralph Workflow Output Looks Like](reviewable-output.md) and the [Example Review Bundle](example-review-bundle.md).

## Best next step if this sounds like the missing piece

Use **Codeberg** as the main public home:

- **Inspect the source on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Star / watch / fork on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Report first-run friction on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>
- **Use GitHub only as the mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>

Keeping adoption and feedback on Codeberg makes the primary repo a clearer trust surface for developers who discover Ralph Workflow while comparing Codex-first setups.
