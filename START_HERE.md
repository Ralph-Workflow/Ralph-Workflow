# Start Here

This path is for evaluators who have one real coding task and want one
unattended run they can judge by what the software does and which
checks ran. "Unattended" means you start it from your own shell and
leave it alone until it finishes or stops with a clear error.

Ralph Workflow is the harness around the coding agent CLI you already
installed and authenticated. It needs no API key of its own; each agent
keeps its own authentication — a login, an API key, or either (see
[`agents`](ralph-workflow/docs/sphinx/agents.md)).

Every default ships with the package, and `ralph --init` seeds your
setup, so you are productive on day 1 (see
[`bundled defaults`](ralph-workflow/docs/sphinx/configuration.md#bundled-defaults)).

## Goal

Complete one focused backlog task end-to-end, then decide whether the
result is something you would actually merge.

## Prerequisites

- Python 3.12+
- Git
- pipx
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

   If you do not use pipx, `pip install ralph-workflow` in a Python 3.12+
   virtual environment also works.

2. **Confirm the install.**

   ```bash
   ralph --version
   ```

3. **Move into your project.**

   ```bash
   cd /path/to/your/project
   ```

4. **Seed the project.**

   ```bash
   ralph --init
   ```

   This creates your user-global config and a starter `PROMPT.md`.
   Project-local config is optional later with
   `ralph --init-local-config` (alias `ralph --generate-local-config`).

5. **Confirm a coding agent.**

   ```bash
   ralph --list-agents
   ```

   Ralph Workflow enables supported agents it finds on your `PATH`.
   If the list is empty, install and authenticate an agent CLI first
   (see [`agents`](ralph-workflow/docs/sphinx/agents.md)), then rerun
   `ralph --init`.

6. **Check the setup.**

   ```bash
   ralph --diagnose
   ```

   Every line should be green before you spend a real run on it. Fix
   any reported problem first (see
   [`diagnostics`](ralph-workflow/docs/sphinx/diagnostics.md)).

7. **Describe the task.** Edit `PROMPT.md` with the outcome and checks
   you expect. Delete the `<!-- ralph:starter-prompt ... -->` line at
   the top — Ralph Workflow refuses to run while that sentinel remains.

   If `PROMPT.md` does not exist yet, seed a task-shaped starter
   instead: `ralph --init feature-spec`, `guardrail` (or `bug-fix`),
   `refactor`, `test-coverage`, or `docs` (init will not overwrite an
   existing `PROMPT.md`).

8. **Run Ralph Workflow.**

   ```bash
   ralph
   ```

## Success looks like

After `ralph` returns:

1. The run completes — or it stops with a clear error rather than a
   confusing one.
2. A run summary names what changed, which checks ran, and what to
   review first.
3. The changes are committed in a worktree and integrated into the
   configured mainline, or reported with evidence of why integration did
   not happen.
4. You can exercise the changed feature in your real environment and
   decide the next action: keep it, ask for changes, rerun, or discard.

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
- Project overview → [`README.md`](README.md)

Ralph Workflow is licensed under AGPL-3.0-or-later. The Ralph loop
pattern is credited to Geoffrey Huntley; Ralph Workflow is an independent
implementation built on that core.
