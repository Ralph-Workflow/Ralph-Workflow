# Start Here: Use Ralph Workflow on One Real Task

If you want to know whether Ralph Workflow is useful, do not start with a vague demo.

Start with **one real backlog task** you already care about.

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

If you are unsure whether your task belongs in the good or bad bucket, read [docs/when-unattended-coding-fits.md](./docs/when-unattended-coding-fits.md).
If you already use worktrees or separate agent sessions and want to know what Ralph Workflow adds beyond that, read [docs/why-worktrees-are-not-enough.md](./docs/why-worktrees-are-not-enough.md).

## Write the task like a one-paragraph spec

Before the run starts, write down:
- what needs to change
- what should stay untouched
- what done looks like
- what checks matter

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
- [docs/free-open-source-proof.md](./docs/free-open-source-proof.md)
- [docs/when-unattended-coding-fits.md](./docs/when-unattended-coding-fits.md)
- [docs/why-worktrees-are-not-enough.md](./docs/why-worktrees-are-not-enough.md)
