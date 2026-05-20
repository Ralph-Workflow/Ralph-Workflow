# Ralph Workflow

> **The operating system for autonomous coding.**
>
> **Write the spec. Wake up to working software.**

[![PyPI](https://img.shields.io/pypi/v/ralph-workflow.svg)](https://pypi.org/project/ralph-workflow/)
[![Python](https://img.shields.io/pypi/pyversions/ralph-workflow.svg)](https://pypi.org/project/ralph-workflow/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

> **GitHub is the mirror. Codeberg is the primary repo.**
> Inspect, star, watch, fork, and open issues on Codeberg first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>

Ralph Workflow is a free and open-source **AI agent orchestration CLI** for real software engineering on your own machine.

It takes the simple Ralph-loop idea and turns it into a **composable workflow system** for planning, implementation, verification, review, and agent routing.

The core stays simple. That simplicity is what makes more complex workflows easier to build, easier to configure, and easier to extend.

Ralph Workflow also ships with a **strong default workflow for writing software**. You can use that default as-is, or build on top of it when you need something more advanced.

It is for developers and technical teams with work **too big to babysit and too risky to trust blindly**.

## Why it is different

- **Simple at the center** — the Ralph-loop core stays understandable.
- **Powerful in composition** — simple loops can be composed into much more complex workflows.
- **Built for orchestration** — planning, coding, verification, and review can use different agents when needed.
- **Strong default workflow** — you do not need to invent a workflow before getting value.
- **Easy to extend** — the same simplicity that makes it understandable also makes it easier to customize.

## Start here

- [Try Ralph Workflow on one real backlog task](START_HERE.md)
- [Choose Your First Ralph Workflow Task](docs/first-task-guide.md)
- [AI Agent Orchestration CLI](docs/ai-agent-orchestration-cli.md)
- [Spec-Driven AI Agent](docs/spec-driven-ai-agent.md)
- [Getting Started](ralph-workflow/README.md)

## A fast way to tell whether Ralph Workflow fits

1. Pick one real substantial backlog task with a defined product outcome.
2. Write it down in `PROMPT.md` with clear acceptance criteria.
3. Run Ralph Workflow.
4. Come back and check whether it produced **working software, real verification, or an honest blocked state**.

If yes, give it a harder task next.
If no, tighten the spec, checks, or task choice and run again.

## What a finished run should prove

A useful run should make three things obvious:

- **what changed**
- **what now works, or what failed honestly**
- **what verification actually ran**

That is supporting proof, not the main product story. The point of Ralph Workflow is the workflow itself: a simple core that scales into stronger autonomous software workflows.

## Install

### PyPI

```bash
pip install ralph-workflow
ralph --help
```

### pipx

```bash
pipx install ralph-workflow
ralph --help
```

### From source

```bash
git clone https://codeberg.org/RalphWorkflow/Ralph-Workflow.git
cd Ralph-Workflow/ralph-workflow
pip install -e ".[dev]"
ralph --version
```

Requires Python 3.12+.

## Before your first run

Make sure the agent CLIs you want Ralph Workflow to call are already installed and authenticated.

Ralph Workflow does not replace those coding agents. It orchestrates them.

## Good first tasks

- a bounded feature slice
- a narrow refactor with tests
- a known cleanup task with clear checks
- repetitive implementation work where success is easy to verify

## Bad first tasks

- vague product exploration
- risky production surgery
- tiny tasks where setup overhead dominates
- workflows that depend on unpredictable mid-run human input

## Why teams use Ralph Workflow

- **Write a spec, not a babysitting script.**
- **Start with a strong default workflow.**
- **Compose more complex workflows when you need them.**
- **Use the agents you already have.**
- **Keep the workflow in the repo.**
- **Aim past prototypes.**

## Need one deeper answer?

- fastest first run — [START_HERE.md](./START_HERE.md)
- first task selection — [docs/first-task-guide.md](./docs/first-task-guide.md)
- orchestration framing — [docs/ai-agent-orchestration-cli.md](./docs/ai-agent-orchestration-cli.md)
- full docs map — [docs/README.md](./docs/README.md)
- package usage and commands — [ralph-workflow/README.md](./ralph-workflow/README.md)

## Third-party proof before you install

If you want outside validation before your first run, use a short curated set instead of hunting around:

- [ToolWise review page](https://toolwise.ai/tools/ralph-workflow)
- [SaaSHub product page](https://www.saashub.com/ralph-workflow)
- [TechTools Launchpad listing](https://techtools.cz/tools/launchpad/?tool=71)

## License

[AGPL-3.0-or-later](LICENSE).

The framework is copyleft. The code Ralph Workflow generates belongs to you.
