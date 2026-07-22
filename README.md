# Ralph Workflow

Ralph Workflow is a free, open-source orchestrator for AI coding
agents. Hand it a well-specified task, let agents plan, build, verify,
and fix, and come back to reviewable, tested work. The full operator
manual lives under
[`ralph-workflow/docs/sphinx/`](ralph-workflow/docs/sphinx/index.rst).

## Who it's for

Ralph Workflow fits developers and small teams with work that is too big
to babysit and too risky to trust blindly. It is not for vague prompts
or repos without guardrails.

## First run

1. **Install Ralph.** Use `pipx install ralph-workflow` to keep it isolated from
   other Python tools. If you do not use pipx, `pip install ralph-workflow` also works.
2. **Start in your project.** Run `cd /path/to/your/project` and `ralph --init`.
   It creates your user-global config and a `PROMPT.md`; project-local config is
   optional later with `ralph --init-local-config`.
3. **Confirm a coding agent.** Ralph looks for supported agents already on your
   `PATH` and enables the ones it finds. Install and authenticate an agent first
   if none are found.
4. **Check the setup.** Run `ralph --diagnose` and fix any reported problem before
   starting work.
5. **Describe the task.** Edit `PROMPT.md` with the outcome and checks you expect.
   For a task-shaped starter, use `ralph --init feature-spec`, `guardrail`,
   `refactor`, `test-coverage`, or `docs` before a prompt file exists.
6. **Run Ralph.** Run `ralph`, then read the finish-receipt artifact: it names the
   change, checks run, and review focus before you decide what to do next.

For the full walkthrough, see
[`Getting started`](ralph-workflow/docs/sphinx/getting-started.md).

## Supported agents

Ralph Workflow ships with built-in support for 8 agents: Claude Code
(interactive + headless), Codex, OpenCode, Nanocoder, AGY (Google Anti
Gravity), Pi, and Cursor. Pick one, authenticate it once on your
machine, and Ralph Workflow uses it. The selection and trust-boundary
story is in [`agents`](ralph-workflow/docs/sphinx/agents.md) and
[`agent-compatibility`](ralph-workflow/docs/sphinx/agent-compatibility.md).

## Documentation route

1. [`START_HERE.md`](START_HERE.md) — first-run walkthrough
2. [`docs/README.md`](docs/README.md) — docs map by intent
3. [`ralph-workflow/docs/sphinx/index.rst`](ralph-workflow/docs/sphinx/index.rst) —
   the maintained operator manual

## Project home

- **Runtime:** Python ≥ 3.12, local-first.
- **License:** AGPL-3.0-or-later.
- **PyPI:** <https://pypi.org/project/ralph-workflow/>
- **Issue tracker:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>
- **Contribution route:** [`CONTRIBUTING.md`](CONTRIBUTING.md) →
  [`ralph-workflow/CONTRIBUTING.md`](ralph-workflow/CONTRIBUTING.md)

The Ralph Loop pattern is attributed to
[Geoffrey Huntley (ghuntley.com/ralph)](https://ghuntley.com/ralph);
Ralph Workflow is an independent reference implementation.
