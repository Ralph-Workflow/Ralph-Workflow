# Start Here

This path is for evaluators who already have one real coding task and
want one unattended run they can judge by what the software does and
what checks ran. "Unattended" means you start the run from your own
shell, then leave it alone until it finishes or stops with a clear
error.

Ralph Workflow manages the harness, not the models. The coding agents —
Claude Code, Codex, Cursor, and the rest — are harnesses around models;
Ralph Workflow is the harness around those: workflow, review, recovery,
and Git integration on top of whichever agent CLIs you already have. A
harness of harnesses.

In plain terms: you keep the coding agent and the model access you
already have, and Ralph Workflow runs the process around it. Agent chains
can mix backends and fall back to another agent when one fails. Ralph
Workflow holds no model credentials and needs no API key of its own — it
launches the agent CLI you installed and authenticated. Each agent
defines its own authentication (a login, an API key, or either), and its
vendor determines model access, rate limits, and cost; see
[`agents`](ralph-workflow/docs/sphinx/agents.md).

Ralph Workflow itself is a configurable workflow graph that runs plan →
build → verify → fix across Git worktrees, with review, recovery, and Git
integration around that core loop.

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

   If you do not use pipx, `pip install ralph-workflow` also works.

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
   instead: `ralph --init feature-spec`, `guardrail`, `refactor`,
   `test-coverage`, or `docs` (init will not overwrite an existing
   `PROMPT.md`).

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
- Public storefront → [`README.md`](README.md)

Ralph Workflow is licensed under AGPL-3.0-or-later. The Ralph Loop
pattern is credited to Geoffrey Huntley; Ralph Workflow is an independent
implementation built on that core.
