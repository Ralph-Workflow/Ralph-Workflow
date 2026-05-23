# Ralph Workflow — Unattended AI Coding Workflow Orchestrator

**Free, open-source CLI tool** that orchestrates AI coding agents into a reviewable unattended workflow.

Run tonight. Review in the morning. Merge if earned.

[![PyPI](https://img.shields.io/pypi/v/ralph-workflow.svg)](https://pypi.org/project/ralph-workflow/)
[![Python](https://img.shields.io/pypi/pyversions/ralph-workflow.svg)](https://pypi.org/project/ralph-workflow/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

> **GitHub is the mirror. Codeberg is the primary repo.**
> Inspect, star, watch, fork, and open issues on Codeberg first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>

Ralph Workflow is the **operating system for autonomous coding**: a free and open-source composable workflow for substantial, well-specified software work on your own machine.

It is for developers and technical teams with work that is **too big to babysit and too risky to trust blindly**.

What makes it different is the finish: Ralph Workflow is built to hand back a **reviewable result** — diff, checks, artifacts, and open questions — instead of a transcript and a claim that the task is done.

## TL;DR — Start in 5 minutes

1. **Pick one real backlog task** you already care about.
2. **Paste this one-paragraph spec template** into `PROMPT.md`.
3. **Run it tonight** with Ralph Workflow.
4. **Wake up to a reviewable diff** and verification output.
5. Ask one question: **would I merge this?**

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

If you want the lowest-friction first run, start with one of these task shapes:
- **Validation rule:** reject empty or whitespace-only project names in one CLI or form flow
- **Feature slice:** add one filter, one export, or one settings toggle with tests
- **Isolated refactor:** replace one duplicated helper path with a shared utility and keep behavior stable

If none of those feel easy to judge tomorrow morning, the task is still too broad.

## The route to use

1. [START_HERE.md](START_HERE.md) — shortest honest first run
2. [docs/README.md](docs/README.md) — curated docs switchboard
3. [ralph-workflow/docs/sphinx/index.rst](ralph-workflow/docs/sphinx/index.rst) — operator manual and configuration reference

If you only click one next page, click [START_HERE.md](START_HERE.md).

## Install

```bash
pipx install ralph-workflow
ralph --help
```

Requires Python 3.12+.

## Before your first run

Make sure the agent CLIs you want Ralph Workflow to call are already installed and authenticated.
Ralph Workflow does not replace those coding agents. It orchestrates them.

## Why people use it

- **No new toolchain required** — keep your current agents
- **Unattended runs with a clean finish** — not just a long session transcript
- **Reviewable output** — changed files, checks, artifacts, open questions
- **Composable default workflow** — start with the default and extend later without throwing it away

## If the first run earns trust

Use **Codeberg** as the public home:
- ⭐ **Star the repo** — helps other developers find it
- 👀 **Watch for updates** — follow the project's progress
- 🐛 **File issues** — report first-run friction, not just bugs
- 🔧 **Open PRs** — real improvements welcome

Use GitHub only if you strongly prefer the mirror.

## License

[AGPL-3.0-or-later](LICENSE).
