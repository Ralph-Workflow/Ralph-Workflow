# Start Here

This path is for evaluators who already have one real coding task and
want one unattended run they can judge by what the software does and
what checks ran. "Unattended" means you start the run from your own
shell, then leave it alone until it finishes or stops with a clear
error.

## Goal

Complete one focused backlog task end-to-end, then decide whether the
result is something you would actually merge.

## Prerequisites

- Python 3.12+
- One supported agent CLI installed and authenticated on your machine
  (see [`agents`](ralph-workflow/docs/sphinx/agents.md))
- One real git repo you care about
- One backlog task with a clear finish line (what "done" means in one
  sentence, plus a test or other check you trust)

## Exact steps

Run every command from your own terminal — not from inside a coding
agent that Ralph Workflow is already driving.

1. **Install Ralph Workflow.**

   ```bash
   pipx install ralph-workflow
   ```

   If you do not use pipx, `pip install ralph-workflow` also works.
   Confirm with `ralph --version`.

2. **Start in your project.**

   ```bash
   cd /path/to/your/project
   ralph --init
   ```

   This creates your user-global config and a starter `PROMPT.md`.
   Project-local config is optional later with `ralph --init-local-config`.

3. **Confirm a coding agent.**

   ```bash
   ralph --list-agents
   ```

   Ralph Workflow enables supported agents it finds on your `PATH`.
   If the list is empty, install and authenticate an agent CLI first
   (see [`agents`](ralph-workflow/docs/sphinx/agents.md)), then rerun
   `ralph --init`.

4. **Check the setup.**

   ```bash
   ralph --diagnose
   ```

   Every line should be green before you spend a real run on it. Fix
   any reported problem first (see
   [`diagnostics`](ralph-workflow/docs/sphinx/diagnostics.md)).

5. **Describe the task.** Edit `PROMPT.md` with the outcome and checks
   you expect. Delete the `<!-- ralph:starter-prompt ... -->` line at
   the top — Ralph Workflow refuses to run while that sentinel remains.

   If `PROMPT.md` does not exist yet, seed a task-shaped starter
   instead: `ralph --init feature-spec`, `guardrail`, `refactor`,
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

If diagnose was still red, or the summary leaves you unsure what
changed, stop and open
[`Troubleshooting`](ralph-workflow/docs/sphinx/troubleshooting.md)
before trusting the result.

## Where to go next

- Deeper first-run tutorial (task picking, `PROMPT.md` templates) →
  [`Getting started`](ralph-workflow/docs/sphinx/getting-started.md)
- Docs map by question → [`docs/README.md`](docs/README.md)
- Operator manual →
  [`ralph-workflow/docs/sphinx/index.rst`](ralph-workflow/docs/sphinx/index.rst)
- Public storefront → [`README.md`](README.md)
