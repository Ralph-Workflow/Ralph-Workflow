# Why Worktrees Are Not Enough for Unattended AI Coding

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


If you already use Claude Code, Codex CLI, or similar tools with git worktrees, you have solved part of the problem.

You have **not** solved the whole one.

Ralph Workflow is an **open-source** tool that orchestrates the coding agents you already use **on your own machine**.

It is for developers and technical teams doing engineering work that is **too big to babysit and too risky to trust blindly**.

What makes Ralph Workflow different is not that it creates more branches or more sessions. It is that it is built to take an unattended task all the way to a **strong software result**: a scoped diff, checks that actually ran, artifacts, and enough context to decide whether you would merge it.

Why use it now? You can keep the agents and local setup you already trust, run one real backlog task tonight, and decide tomorrow whether the result actually holds up.

## What worktrees are good at

Worktrees are genuinely useful. They help with:

- separating concurrent coding tasks
- avoiding checkout thrash
- reducing file collisions between runs
- making branch-per-task workflows less painful

That matters.

If your main problem is *"multiple coding sessions keep stepping on each other"*, worktrees help.

## What worktrees do **not** solve

Worktrees do not solve the harder problem:

**Can you trust the unattended result enough to review and merge it quickly?**

They do not fix:

- vague task definitions
- agents claiming "done" before the result holds up
- weak or missing verification
- oversized diffs that are annoying to review
- poor handoff notes after a long unattended run
- unclear re-entry when something failed halfway through

This is why teams can have clean workspace isolation and still feel that unattended AI coding is messy.

## The missing layer

The missing layer is not more checkout isolation.

The missing layer is a workflow that:

1. sharpens the task before coding starts
2. builds, verifies, and fixes in the same loop
3. stops weak work from pretending it is complete
4. lands on a result a human can actually review

That is the layer Ralph Workflow is built for.

## Where Ralph Workflow fits

Ralph Workflow sits in the gap between:

- *"the agent can run in parallel safely"*
- and *"the result is something I would actually merge"*

That difference matters more than another branch-management trick.

A useful unattended run should hand back:

- a understandable diff
- changed files you can inspect normally
- checks that really ran
- artifacts and logs you can follow
- a clean yes/no human review

## The practical rule

Use worktrees when you need safer parallel isolation.

Use Ralph Workflow when you need an unattended run to come back as a **reviewable engineering handoff**, not just a separate sandbox.

The best first test is simple:

- pick one real backlog task
- describe it clearly in `PROMPT.md`
- run Ralph Workflow overnight
- ask in the morning: **does the implementation hold up?**

If yes, that is the product value.

## Next steps

- Start with [../START_HERE.md](../START_HERE.md)
- See [free-open-source-proof.md](./free-open-source-proof.md) for an example strong software result
- See [when-unattended-coding-fits.md](./when-unattended-coding-fits.md) to choose a good first task

If worktree isolation is already familiar but you still need a trustworthy unattended finish, inspect the **primary Codeberg repo** first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>

Best next public actions:

- **Inspect / star / watch on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Report first-run friction on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>
- **Use GitHub only as the mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>
