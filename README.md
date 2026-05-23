# Ralph Workflow

> **The operating system for autonomous coding.**
>
> **Write the spec. Wake up to working software.**

[![PyPI](https://img.shields.io/pypi/v/ralph-workflow.svg)](https://pypi.org/project/ralph-workflow/)
[![Python](https://img.shields.io/pypi/pyversions/ralph-workflow.svg)](https://pypi.org/project/ralph-workflow/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

> **GitHub is the mirror. Codeberg is the primary repo.**
> Inspect, star, watch, fork, and open issues on Codeberg first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>

Ralph Workflow is a free and open-source **AI agent orchestration CLI** for developers and technical teams doing substantial, well-specified software work on their own machine.

It keeps the Ralph-loop core simple, then composes that core into a stronger workflow for planning, implementation, verification, review, and agent routing.
That simplicity at the center is what makes the system extensible without turning it into glue-chaos.

It also ships with a **strong default workflow for writing software**.
Use the default as-is today, or build your own workflow on top later.

## Start in 5 minutes

1. Pick **one real backlog task** you can still judge tomorrow.
2. Put a one-paragraph spec in `PROMPT.md`.
3. Run Ralph Workflow tonight.
4. Review the diff and the checks in the morning.
5. Ask: **would I merge this?**

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

If you want the lowest-friction first run, use one of these task shapes:
- validation rule
- focused feature slice
- bounded refactor with tests

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

- **No new toolchain required** — keep the coding agents you already trust
- **Unattended runs with a clean finish** — not just a transcript and a claim
- **Reviewable output** — diff, checks, artifacts, and open questions
- **Composable default workflow** — start simple and extend later

## If the first run earns trust

Use **Codeberg** as the public home:
- ⭐ star the repo
- 👀 watch the repo
- 🐛 open an issue if your first run exposed friction
- 🔧 open a PR if you fix something real

Use GitHub only if you strongly prefer the mirror.

## License

[AGPL-3.0-or-later](LICENSE).
