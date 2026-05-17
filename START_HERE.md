# Start Here: Use Ralph Workflow on One Real Task

Ralph Workflow is a **free and open-source** tool that orchestrates the coding agents you already use **on your own machine**.

It is for developers and technical teams with work that is **too big to babysit and too risky to trust blindly**.

What makes it different is the handoff: Ralph Workflow is built to bring back a **reviewable result** — a real diff, checks, artifacts, and enough context to decide whether you would merge it.

Why use it now? Because you can install it for free, hand off one real backlog task tonight, and judge the result honestly tomorrow.

Before you start: Ralph Workflow does **not** replace Claude Code, Codex CLI, OpenCode, or whichever coding agent you want to use. It orchestrates the agent you already have on **your own machine**. For the fastest honest first run, make sure one supported agent CLI is already installed and already authenticated before you continue.

If you want to know whether Ralph Workflow is useful, do not start with a vague demo.

Start with **one real backlog task** you already care about.

If the only thing you are stuck on right now is agent choice, read [docs/which-agent-should-i-start-with.md](./docs/which-agent-should-i-start-with.md) and pick the agent that is already working on your machine.

## The fastest honest first run

If you want the shortest path from curiosity to a real evaluation, use this exact flow in a real repo you already care about:

Checklist before you run it:

- Python 3.12+
- a git repo you can safely test in
- at least one supported agent CLI already working on your machine

If you are unsure which one to start with, use the one you already trust and see [docs/which-agent-should-i-start-with.md](./docs/which-agent-should-i-start-with.md).

```bash
pipx install ralph-workflow
cd /path/to/your/project
ralph --init
ralph --diagnose
$EDITOR PROMPT.md
ralph
```

Paste a small real spec into `PROMPT.md`:

```markdown
# Goal

Add validation so the CLI rejects empty project names before creating files.
Keep the rest of the flow unchanged.

## Acceptance criteria

- Empty or whitespace-only project names fail with a clear error
- No project files are created for invalid names
- Existing valid-name behavior stays unchanged
- Tests cover the new validation
```

Then review the result like normal engineering work and ask one question:

> **Would I merge this?**

If yes, give Ralph Workflow a harder task tomorrow night.
If not, tighten the task or checks and run again.

## Pick the right first task

Choose something that is:
- small enough to judge in one sitting
- real enough to matter
- bounded enough that rollback is cheap
- clear enough that success is easy to define

Good first tasks:
- a small feature slice
- a bounded refactor with tests
- a backlog item with clear acceptance criteria
- repetitive implementation work with obvious verification

Bad first tasks:
- a vague product idea
- risky production surgery
- mixed multi-part work
- anything where no one agrees what “done” means

If you are still hesitating over Claude Code vs Codex vs OpenCode, read [docs/which-agent-should-i-start-with.md](./docs/which-agent-should-i-start-with.md).
If you want copy-paste starter specs instead of drafting from scratch, read [docs/first-task-prompt-templates.md](./docs/first-task-prompt-templates.md).
If you are unsure whether your task belongs in the good or bad bucket, read [docs/when-unattended-coding-fits.md](./docs/when-unattended-coding-fits.md).
If you already use worktrees or separate agent sessions and want to know what Ralph Workflow adds beyond that, read [docs/why-worktrees-are-not-enough.md](./docs/why-worktrees-are-not-enough.md).

## Run the fastest honest first test

```bash
pipx install ralph-workflow
cd /path/to/your/project
ralph --init
ralph --diagnose
$EDITOR PROMPT.md
ralph
```

Use a real repo and a real backlog item. The point is not to watch the run live.
The point is to come back to something you can review like normal engineering work.

## Write the task like a one-paragraph spec

Before the run starts, write down:
- what needs to change
- what should stay untouched
- what done looks like
- what checks matter

Use a simple structure like this in `PROMPT.md`:

```markdown
# Goal

Add validation so the CLI rejects empty project names before creating files.
Keep the rest of the create flow unchanged.

## Acceptance criteria

- Empty or whitespace-only project names fail with a clear error
- No project files are created for invalid names
- Existing valid-name behavior stays unchanged
- Tests cover the new validation
```

That level of specificity is enough for a strong first run.
If you want more ready-made shapes for feature work, validation, refactors, tests, or docs, use [docs/first-task-prompt-templates.md](./docs/first-task-prompt-templates.md).

## How to judge the result honestly

Do not ask whether the agent looked smart.

Ask:
- does the diff match the task?
- are the changes small enough to review?
- did the checks really run?
- **would I merge this?**

That is the whole evaluation.

## What a good run should hand back

A useful Ralph Workflow run should leave you with:
- a scoped result
- a real diff
- changed files you can inspect
- checks that actually ran
- a reasoning trail
- open questions called out clearly

## Next reading

- [README.md](./README.md)
- [docs/quick-reference.md](./docs/quick-reference.md)
- [docs/which-agent-should-i-start-with.md](./docs/which-agent-should-i-start-with.md)
- [docs/first-task-prompt-templates.md](./docs/first-task-prompt-templates.md)
- [docs/free-open-source-proof.md](./docs/free-open-source-proof.md) — see the concrete artifact bundle and morning-after review path
- [docs/when-unattended-coding-fits.md](./docs/when-unattended-coding-fits.md)
- [docs/why-worktrees-are-not-enough.md](./docs/why-worktrees-are-not-enough.md)
