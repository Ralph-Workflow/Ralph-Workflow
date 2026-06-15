---
orphan: true
---

# Ralph Workflow vs Google Anti Gravity

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple core loop inspired by the original Ralph loop.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


If you already use Google Anti Gravity, the simplest difference is this:

- **Google Anti Gravity** is an interactive coding agent you drive directly.
- **Ralph Workflow** is a **free and open-source** orchestration CLI that runs Google Anti Gravity or another supported coding agent **on your own machine** inside a **composable loop workflow** for real software work.

That makes Ralph Workflow a fit for developers and technical teams with work that is **too big to babysit and too risky to trust blindly**.

Why try it now? Because you do not need to replace Google Anti Gravity to use it. Keep your current Anti Gravity setup, hand Ralph Workflow one real backlog task tonight, and decide tomorrow whether the result is something you would actually merge.

## The core difference

Google Anti Gravity is strongest when you want to stay in the loop.

You prompt, inspect, redirect, and keep steering the session live.

Ralph Workflow is strongest when you want to get out of the loop for a while.

You write a bounded spec in `PROMPT.md`, Ralph Workflow runs planning, development, verification, and review as one unattended flow, and you come back to a diff, checks, logs, and artifacts you can inspect like normal engineering work.

For Google Anti Gravity support, the MCP contract matters too: Ralph Workflow automatically injects its MCP endpoint at run time; use `ralph --check-mcp` to verify AGY transport compatibility before the first run. Ralph Workflow-owned MCP tools, completion signals such as `declare_complete`, and proxied upstream servers are part of the supported-agent story rather than an escape hatch.

Ralph Workflow's AGY support is based on the upstream `agy` CLI source and the measured v1.0.8 wire format, not on assumptions. The canonical display names from `agy models` are the only valid `--model` values, and the flag order used by the harness matches what the real binary accepts. See `tmp/agy-source-of-truth.txt` for the recorded upstream-source facts and local measurements.

## Choose Ralph Workflow when

Ralph Workflow is usually the better fit when you want to:

- hand off a real backlog task and review it later
- wake up to a large chunk of work instead of babysitting the terminal
- keep Google Anti Gravity as your agent path but add a stronger handoff
- route different phases through different agents when needed
- judge the result by whether it produced working software and real verification

Typical good Ralph Workflow tasks:

- a bounded feature slice
- a narrow refactor with tests
- a cleanup pass with obvious verification
- repetitive implementation work with clear acceptance criteria

## Choose Google Anti Gravity when

Google Anti Gravity is usually the better fit when you want to:

- pair-program interactively in the terminal
- keep steering the work every few minutes
- explore a codebase conversationally
- make small edits while you stay present
- iterate live instead of handing off a full work unit

## Why some teams use both

These tools solve different parts of the job.

A practical split is:

- use **Google Anti Gravity** for live exploration, shaping, and interactive edits during the day
- use **Ralph Workflow** for unattended evening or overnight runs where the handoff quality matters as much as the model quality

If your current pain is not "how do I get Anti Gravity to edit faster?" but "how do I come back to something reviewable tomorrow morning?", Ralph Workflow is the sharper fit.

## What makes Ralph Workflow different from just running Google Anti Gravity longer

The difference is not simply more agent time.

Ralph Workflow is built around a different handoff:

- a real diff
- checks that actually ran
- artifacts saved in the repo
- review output you can inspect
- enough context to answer: **does the implementation hold up?**

That is the real product test.

## Fastest honest first test


Then run:

```bash
pipx install ralph-workflow
cd /path/to/your/project
ralph --init
ralph --diagnose
ralph --check-mcp
$EDITOR PROMPT.md
ralph
```

Use one real backlog task, not a vague demo.

**Completion contract:** Ralph Workflow expects Google Anti Gravity to signal completion using `declare_complete` (via the Ralph Workflow MCP tool surface) or by submitting a phase artifact — the same contract as Claude interactive mode.

If you want help picking that first task, read [When Unattended Coding Fits](when-unattended-coding-fits.md), [Choose Your First Ralph Workflow Task](first-task-guide.md), and [First-Task Prompt Templates](first-task-prompt-templates.md).

If you want to see the kind of morning-after handoff Ralph Workflow is aiming for before you install, inspect [What Good Ralph Workflow Output Looks Like](reviewable-output.md) and the [Example Review Bundle](example-review-bundle.md).

## Best public next step if Google Anti Gravity is already in your stack

Use **Codeberg** as the main public home for evaluating Ralph Workflow:

- **Inspect the primary repo first:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Star or watch on Codeberg if Ralph Workflow earns a place next to Google Anti Gravity:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Open first-run friction or docs issues on Codeberg if the handoff misses:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>
- **Use GitHub only as the mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>

Keeping adoption and feedback on Codeberg makes the primary repo a clearer trust surface for developers evaluating Anti Gravity-based setups.
