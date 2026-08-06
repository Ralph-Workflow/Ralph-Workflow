# Ralph Workflow

Ralph Workflow is a free, open-source AI agent orchestrator for coding
work. You give it one well-specified task; it runs a **Ralph loop**
(plan → build → verify → fix) with your chosen coding agent, then you
come back to inspect the result. That simple core ships with a strong
default workflow for writing software — adopt it as-is first, then
extend it when you need to.

## Who it's for

Ralph Workflow fits developers and small teams with work that is too big
to babysit and too risky to trust blindly. It is not for vague prompts
or repos without tests or other guardrails.

## Next step

For the shortest honest first run — install, diagnose, one focused task,
and what success looks like — open [`START_HERE.md`](START_HERE.md).

The fuller tutorial is
[`Getting started`](ralph-workflow/docs/sphinx/getting-started.md).
The full operator manual lives under
[`ralph-workflow/docs/sphinx/`](ralph-workflow/docs/sphinx/index.rst).

## Install from a checkout

For a self-contained manual snapshot, run this from `ralph-workflow/`:

```bash
make install  # `rdev --version` ends in -build
# or: make dev  # `rdev --version` ends in -dev
```

Both commands install the `rdev` launcher only, so they never shadow a
published `ralph`: an existing global `ralph` is reported and left in place,
and `rdev` is used instead of it. An `rdev` from an earlier dev build is
replaced. `make stable` owns the global `ralph` and still asks — continue,
remove a pipx or `uv tool` installation, or abort. See the
[contributor setup guide](ralph-workflow/CONTRIBUTING.md#dev-build-vs-stable-build)
for stable installs and switching builds.

## Supported agents

Ralph Workflow ships eight built-in agent backends: Claude Code,
Claude Code headless, Codex, OpenCode, Nanocoder, AGY (Google Anti
Gravity), Pi, and Cursor. Pick one, authenticate it once on your
machine, and Ralph Workflow uses it. Selection and trust-boundary
details are in [`agents`](ralph-workflow/docs/sphinx/agents.md) and
[`agent-compatibility`](ralph-workflow/docs/sphinx/agent-compatibility.md).

## Documentation route

1. [`START_HERE.md`](START_HERE.md) — runnable first-run walkthrough
2. [`docs/README.md`](docs/README.md) — docs map by reader question
3. [`ralph-workflow/docs/sphinx/index.rst`](ralph-workflow/docs/sphinx/index.rst) —
   maintained operator manual (configure, operate, extend)

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
