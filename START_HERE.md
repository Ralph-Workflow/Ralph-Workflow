# Start Here

This path is for evaluators who already have one real coding task and
want one unattended run they can judge by what the software does and
what checks ran.

## Goal

Complete one focused backlog task end-to-end, then decide whether the
result is something you would actually merge.

## Prerequisites

- Python 3.12+
- One supported agent CLI installed and authenticated on your machine
  (see [`agents`](ralph-workflow/docs/sphinx/agents.md))
- One real git repo you care about
- One backlog task with a clear finish line

## Exact steps

Run every command from a human-operated shell outside any Ralph-managed
agent session.

1. **Install Ralph Workflow.**

   ```bash
   pipx install ralph-workflow
   ```

   If you do not use pipx, `pip install ralph-workflow` also works.

2. **Start in your project.**

   ```bash
   cd /path/to/your/project
   ralph --init
   ```

   This creates your user-global config and a starter `PROMPT.md`.
   Project-local config is optional later with `ralph --init-local-config`.

3. **Confirm a coding agent.** Ralph Workflow looks for supported agents
   already on your `PATH` and enables the ones it finds. Install and
   authenticate an agent first if none are found.

4. **Check the setup.**

   ```bash
   ralph --diagnose
   ```

   Fix any reported problem before starting work.

5. **Describe the task.** Edit `PROMPT.md` with the outcome and checks
   you expect. Delete the `<!-- ralph:starter-prompt ... -->` line at
   the top — Ralph Workflow refuses to run while that sentinel remains.
   Prefer a task-shaped starter before `PROMPT.md` exists:
   `ralph --init feature-spec`, `guardrail`, `refactor`,
   `test-coverage`, or `docs` (init will not overwrite an existing
   `PROMPT.md`).

6. **Run Ralph Workflow.**

   ```bash
   ralph
   ```

## Success looks like

After `ralph` returns:

1. A run summary names what changed, which checks ran, and what to
   review first.
2. You can exercise the changed feature in your real environment and
   decide the next action: keep the branch, ask for changes, rerun, or
   discard.

## Where to go next

- Deeper first-run tutorial →
  [`Getting started`](ralph-workflow/docs/sphinx/getting-started.md)
- Docs map by question → [`docs/README.md`](docs/README.md)
- Operator manual →
  [`ralph-workflow/docs/sphinx/index.rst`](ralph-workflow/docs/sphinx/index.rst)
- Public storefront → [`README.md`](README.md)
