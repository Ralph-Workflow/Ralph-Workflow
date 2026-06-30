<!--
  Review note (docs rubric §"Required review note for meaningful docs changes"):
  - What changed: rewrote the opening paragraph so the page leads with the
    canonical autopilot positioning language instead of the older "AI agent
    orchestrator built around a simple ... Ralph-loop core" lead category.
  - Why it belongs here: this page is part of the maintained Sphinx manual;
    it must agree with the README and the manual home so the product story
    is coherent across surfaces (rubric hard failure: surfaces fight each
    other).
  - What was pruned: nothing material; the page's page-specific argument is
    preserved.
  - How the route is clearer: the lead now matches the canonical autopilot
    framing used by the README and the manual home.
-->

# Ralph Workflow vs Claude Code

Ralph Workflow is **the autopilot for coding agents** — a free and open-source operating system for autonomous coding, an AI agent orchestrator built around a simple Ralph-loop core that becomes powerful through composition.
**Hand it a well-specified coding task, let the agents plan, build, verify, and fix, and come back to reviewable, tested work.**
The default workflow is strong enough to adopt as-is, before you customize anything.

If you already know Claude Code, the simplest difference is this:

- **Claude Code** is an interactive coding agent you drive directly.
- **Ralph Workflow** is an **open-source** orchestration CLI that runs the coding agents you already use **on your own machine** and hands back a **strong software result** after an unattended run.

That means Ralph Workflow is for developers and technical teams with work that is **too big to babysit and too risky to trust blindly**.

Why try Ralph Workflow now? You do not need to abandon Claude Code to use it. Keep Claude in the loop, hand off one real backlog task tonight, and decide tomorrow whether the result is something you would actually merge.

Before you install anything, inspect the **primary Codeberg repo** first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>

If Ralph Workflow earns trust after a real overnight run, keep the public next step on Codeberg:

- **Star or watch the primary repo:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Report first-run friction or proof gaps:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>
- **Use GitHub only as the mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>

## The core difference

Claude Code is strongest when you want to stay in the loop.

You prompt, inspect, redirect, and keep steering the session live.

Ralph Workflow is strongest when you want to get out of the loop for a while.

You write a bounded spec in `PROMPT.md`, Ralph Workflow runs planning, development, verification, and review as one unattended flow, and you come back to a diff, checks, logs, and artifacts you can inspect like normal engineering work.

## Choose Ralph Workflow when

Ralph Workflow is usually the better fit when you want to:

- hand off a real backlog task and review it later
- wake up to a large chunk of work instead of babysitting the terminal
- keep Claude Code but add a stronger morning-after handoff
- route different phases through different agents when needed
- judge the result with a simple merge / no-human review

Typical good Ralph Workflow tasks:

- a bounded feature slice
- a narrow refactor with tests
- a cleanup pass with obvious verification
- repetitive implementation work with clear acceptance criteria

## Choose Claude Code when

Claude Code is usually the better fit when you want to:

- pair-program interactively in the terminal
- keep steering the work every few minutes
- explore a codebase conversationally
- make small edits while you stay present
- iterate live instead of handing off a full work unit

## Why some teams use both

These tools solve different parts of the job.

A practical split is:

- use **Claude Code** for live exploration, shaping, and interactive edits during the day
- use **Ralph Workflow** for unattended evening or overnight runs where the handoff quality matters as much as the model quality

If your current pain is not "how do I get Claude to edit faster?" but "how do I come back to something reviewable tomorrow morning?", Ralph Workflow is the sharper fit.

## What makes Ralph Workflow different from just running Claude longer

The difference is not simply more agent time.

Ralph Workflow is built around a different handoff:

- a real diff
- checks that actually ran
- artifacts saved in the repo
- review output you can inspect
- enough context to answer: **does the implementation hold up?**

That is the real product test.

## Fastest honest first test

Before you start, have Claude Code or another supported agent CLI already installed and already authenticated on your own machine. Ralph Workflow is open source, but it does not replace the coding agent itself.

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

If you want to see the kind of morning-after handoff Ralph Workflow is aiming for before you install, inspect [what good output looks like](./free-open-source-proof.md) and the [example review bundle](../../../examples/first-review-bundle/README.md).

After that first real run, take exactly one public next step on Codeberg:

- **promising run:** star or watch the repo
- **shaky run:** open the right issue on Codeberg
