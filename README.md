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

```bash
pipx install ralph-workflow
cd /path/to/your/project
ralph --init
ralph --diagnose
$EDITOR PROMPT.md
ralph
```

The long-form walkthrough — including the trust boundaries, the
prose-intent of each step, and what success looks like — lives in
[`START_HERE.md`](START_HERE.md) and the maintained
[`Getting started`](ralph-workflow/docs/sphinx/getting-started.md) page.

## Supported agents

Ralph Workflow ships with built-in support for 8 agents: Claude Code
(interactive + headless), Codex, OpenCode, Nanocoder, AGY (Google Anti
Gravity), Pi, Claude Code OpenCode, and Claude Code Pi. Pick one,
authenticate it once on your machine, and Ralph Workflow uses it. The
selection and trust-boundary story is in
[`agents`](ralph-workflow/docs/sphinx/agents.md) and
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
