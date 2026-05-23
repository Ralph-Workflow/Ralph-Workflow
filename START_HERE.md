# Start Here: Try Ralph Workflow on One Real Task

> **GitHub is the mirror. Codeberg is the primary repo.**
> Inspect and follow Ralph Workflow on Codeberg first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>

If you are evaluating Ralph Workflow, do not start with a vague demo.
Start with **one real backlog task** you already care about.

## The fastest honest first run

1. Pick one meaningful task you can still judge in the morning.
2. Paste the spec template below into `PROMPT.md`.
3. Run Ralph Workflow tonight.
4. Open the diff and the checks tomorrow.
5. Ask: **would I merge this?**

That is the whole evaluation.

## If you want the lowest-friction first run

Start with one of these exact task shapes:
- **Validation rule:** reject empty or whitespace-only names in one CLI or form flow
- **Feature slice:** add one filter, one export, or one settings toggle with tests
- **Isolated refactor:** replace one duplicated helper path with a shared utility and keep behavior stable

If none of those sound easy to judge in the morning, the task is still too broad.

## Before you start

Have these ready:

- one real git repo you care about
- Python 3.12+
- one supported agent CLI already installed
- working auth for that agent

## Pick the right first task

Choose something that is:
- small enough to judge in one sitting
- real enough to matter
- bounded enough that rollback is cheap
- clear enough that success is easy to define

Good first tasks:
- a small feature slice
- a bounded refactor with tests
- a backlog item with obvious acceptance criteria
- repetitive implementation work with clear verification

Bad first tasks:
- a vague product idea
- risky production surgery
- mixed multi-part work
- anything where no one agrees what "done" means

If you are still unsure, use [docs/first-task-guide.md](./docs/first-task-guide.md) before you run it.

## Paste this spec template

```md
Change:
[what should change]

Keep unchanged:
[what must stay stable]

Done means:
[observable outcome]

Checks:
[tests, lint, build, or other verification]
```

Example:

```md
Change:
Add a billing history page with filters and CSV export.

Keep unchanged:
Do not alter the current invoice creation flow or billing calculations.

Done means:
Users can open billing history, filter by date range, and export matching rows to CSV.

Checks:
Relevant billing tests pass and any new billing-history tests pass.
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

If you are already trying to stitch Claude Code and Codex together by hand, read **[Claude Code + Codex workflow](./docs/claude-code-codex-workflow.md)** next.
If your bigger question is how to judge the result before merge, read **[Review AI coding output before merge](./docs/review-ai-coding-output-before-merge.md)**.
If you want the deeper reasoning behind the spec shape itself, read **[Spec-Driven AI Agent](./docs/spec-driven-ai-agent.md)**.

## What a good result should include

A useful Ralph Workflow run should hand back:
- a scoped result
- a real diff
- changed files you can inspect
- checks that actually ran
- a reasoning trail
- open questions called out clearly

## Morning-after review checklist

Do not ask whether the tool looked smart.

Ask:
- does the diff match the task?
- are the changes small enough to review?
- did the checks really run?
- what still needs a human judgment call?
- **would I merge this?**

If yes, the workflow earned a harder task.
If no, sharpen the spec and run it again.

## If the first run is promising

Use the public next step on **Codeberg**:
- star the repo if you want to track it
- watch it if you want updates
- open an issue if your first run exposed friction or a missing doc

That turns a private evaluation into a useful public signal or actionable feedback.

## Next examples

See:
- [First-task guide](./docs/first-task-guide.md)
- [First-task prompt templates](./docs/first-task-prompt-templates.md)
- [Claude Code + Codex workflow](./docs/claude-code-codex-workflow.md)
- [Good unattended task vs bad one](./docs/good-unattended-ai-coding-task.md)
- [Review bundle example](./docs/example-review-bundle.md)
- [After your first run](./docs/after-your-first-run.md)
- [Docs map](./docs/README.md)
- [Operator manual](./ralph-workflow/docs/sphinx/index.rst)
