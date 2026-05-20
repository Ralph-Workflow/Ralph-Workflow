# Ralph Workflow vs OpenCode

If you already use OpenCode, the simplest difference is this:

- **OpenCode** is the coding-agent interface and provider-routing layer you drive directly.
- **Ralph Workflow** is a **free and open-source** orchestration CLI that runs OpenCode or another supported coding agent **on your own machine** inside a **composable loop workflow** for real software work.

That makes Ralph Workflow a fit for developers and technical teams with work that is **too big to babysit and too risky to trust blindly**.

Why try it now? Because you do not need to replace OpenCode to use it. Keep your current OpenCode setup, hand Ralph Workflow one real backlog task tonight, and decide tomorrow whether the result is something you would actually merge.

## The core difference

OpenCode is strongest when you want flexible provider access and a direct agent surface you can steer yourself.

Ralph Workflow is strongest when you want the workflow itself to do more than one agent session can do cleanly.

You write a bounded spec in `PROMPT.md`, Ralph Workflow runs planning, development, verification, and review as one unattended flow, and you come back to executable changes, test evidence, logs, and artifacts you can inspect like normal engineering work.

## Choose Ralph Workflow when

Ralph Workflow is usually the better fit when you want to:

- keep OpenCode as your agent path but stop babysitting the session
- hand off a real backlog task and review it later
- run a real loop across planning, implementation, verification, and review
- route planning, implementation, and review through different agents when needed
- evaluate the result by running software and checks, not by trusting a chat session

Typical good Ralph Workflow tasks:

- a bounded feature slice
- a narrow refactor with tests
- a cleanup pass with obvious verification
- repetitive implementation work with clear acceptance criteria

## Choose OpenCode when

OpenCode is usually the better fit when you want to:

- stay in an interactive coding loop
- switch providers live while you are still steering the work
- explore a codebase conversationally
- make small edits while you remain present
- tune model/provider choices more than the handoff itself

## Why some teams use both

These tools solve different parts of the job.

A practical split is:

- use **OpenCode** for interactive exploration, model/provider flexibility, and live coding sessions
- use **Ralph Workflow** for unattended evening or overnight runs where review quality matters as much as model choice

If your current pain is not "which provider should I route this to?" but "how do I come back to something reviewable tomorrow morning?", Ralph Workflow is the sharper fit.

## What makes Ralph Workflow different from just running OpenCode longer

The difference is not simply more runtime.

Ralph Workflow is built around a different workflow model:

- a spec-first loop instead of a single session
- planning, implementation, verification, and review in one run
- agent routing across phases when different tools fit different jobs
- executable results and checks you can inspect in the repo
- enough context to answer: **did this actually work, and would I merge it?**

That is the real product test.

## Fastest honest first test

Before you start, have OpenCode or another supported agent CLI already installed and already authenticated on your own machine. Ralph Workflow is free and open source, but it does not replace the coding agent itself.

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

If you want to see the kind of executable result and verification trail Ralph Workflow is aiming for before you install, inspect [What Good Ralph Workflow Output Looks Like](reviewable-output.md) and the [Example Review Bundle](example-review-bundle.md).

## Best next step if this sounds like the missing piece

Use **Codeberg** as the main public home:

- **Inspect the source on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Star / watch / fork on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Report first-run friction on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>
- **Use GitHub only as the mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>

Keeping adoption and feedback on Codeberg makes the primary repo a clearer trust surface for developers evaluating OpenCode-based setups.
