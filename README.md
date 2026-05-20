# Ralph Workflow

> **The operating system for autonomous coding.**
>
> **Write the spec. Wake up to working software.**

[![PyPI](https://img.shields.io/pypi/v/ralph-workflow.svg)](https://pypi.org/project/ralph-workflow/)
[![Python](https://img.shields.io/pypi/pyversions/ralph-workflow.svg)](https://pypi.org/project/ralph-workflow/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

> **GitHub is the mirror. Codeberg is the primary repo.**
> Inspect, star, watch, fork, and open issues on Codeberg first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>

Ralph Workflow is a free and open-source **AI agent orchestration CLI** for substantial, well-specified software engineering on your own machine.

It takes the simple Ralph-loop idea and turns it into a **composable workflow system** for planning, implementation, verification, review, and agent routing.
The core stays simple. That simplicity is what makes more complex workflows easier to build, easier to configure, and easier to extend.

Ralph Workflow also ships with a **strong default workflow for writing software**.
You can use that default as-is, or build on top of it when you need something more advanced.

**Who it is for:** developers and technical teams with repo work that is **too big to babysit and too risky to trust blindly**.

**Why it is different:** it does not stop at "the agent said done." The workflow is built to finish with **working software plus a review surface**: changed files, checks, and a clear handoff.

- **Simple at the center** — the Ralph Workflow loop core stays understandable.
- **Powerful in composition** — simple loops can be composed into much more complex workflows.
- **Built for orchestration** — planning, coding, verification, and review can use different agents when needed.
- **Strong default workflow** — you do not need to invent a workflow before getting value.
- **Easy to extend** — the same simplicity that makes it understandable also makes it easier to customize.

**Why use it now:** you can try the default workflow tonight on one real backlog task, judge it with a boring merge question, and keep or extend it from there.

## First honest path

1. Inspect the **primary repo on Codeberg**: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
2. Read [START_HERE.md](START_HERE.md) — shortest honest first run
3. Pick one real task you would actually care about merging tonight: feature slice, refactor with tests, verification pass, or cleanup with a concrete finish line
4. Ask one question after the run: **would you merge it?**

## Go deeper only if you need to

1. [docs/README.md](docs/README.md) — curated docs switchboard
2. [ralph-workflow/docs/sphinx/index.rst](ralph-workflow/docs/sphinx/index.rst) — operator manual and configuration reference

## Install

```bash
pipx install ralph-workflow
ralph --help
```

Requires Python 3.12+.

## Before your first run

Make sure the agent CLIs you want Ralph Workflow to call are already installed and authenticated.
Ralph Workflow does not replace those coding agents. It orchestrates them.

## License

[AGPL-3.0-or-later](LICENSE).
