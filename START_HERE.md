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

## Next pages only if you need them

- task selection — [docs/first-task-guide.md](./docs/first-task-guide.md)
- prompt templates — [docs/first-task-prompt-templates.md](./docs/first-task-prompt-templates.md)
- what good output looks like — [docs/free-open-source-proof.md](./docs/free-open-source-proof.md)
- after the run — [docs/after-your-first-run.md](./docs/after-your-first-run.md)
- docs switchboard — [docs/README.md](./docs/README.md)
- operator manual — [ralph-workflow/docs/sphinx/index.rst](./ralph-workflow/docs/sphinx/index.rst)
