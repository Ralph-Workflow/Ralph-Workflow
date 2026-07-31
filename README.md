# Ralph Workflow

Ralph Workflow is a free, open-source AI agent orchestrator for coding
work. It is built on a simple Ralph loop: you hand agents a
well-specified task, they plan, build, verify, and fix, and you come
back to inspect the result. That simple core ships with a strong
default workflow for writing software — adopt it as-is first, then
extend it when you need to.

The full operator manual lives under
[`ralph-workflow/docs/sphinx/`](ralph-workflow/docs/sphinx/index.rst).

## Who it's for

Ralph Workflow fits developers and small teams with work that is too big
to babysit and too risky to trust blindly. It is not for vague prompts
or repos without guardrails.

## Try it

Shortest honest evaluation path:

1. Install with `pipx install ralph-workflow` (or `pip install ralph-workflow`).
2. In a real git repo, run `ralph --init`, then `ralph --diagnose`.
3. Replace the starter `PROMPT.md` with one focused task and run `ralph`.
4. When the run finishes, read the summary of what changed and which
   checks passed, then exercise the feature yourself.

Exact copy-paste steps, success checks, and task-picking guidance are in
[`START_HERE.md`](START_HERE.md). The fuller tutorial is
[`Getting started`](ralph-workflow/docs/sphinx/getting-started.md).

## Supported agents

Ralph Workflow ships eight built-in agent backends: Claude Code,
Claude Code headless, Codex, OpenCode, Nanocoder, AGY (Google Anti
Gravity), Pi, and Cursor. Pick one, authenticate it once on your
machine, and Ralph Workflow uses it. Selection and trust-boundary
details are in [`agents`](ralph-workflow/docs/sphinx/agents.md) and
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
- **Issue tracker:** <https://github.com/Ralph-Workflow/Ralph-Workflow/issues/new>
- **Contribution route:** [`CONTRIBUTING.md`](CONTRIBUTING.md) →
  [`ralph-workflow/CONTRIBUTING.md`](ralph-workflow/CONTRIBUTING.md)

The Ralph Loop pattern is attributed to
[Geoffrey Huntley (ghuntley.com/ralph)](https://ghuntley.com/ralph);
Ralph Workflow is an independent reference implementation.
