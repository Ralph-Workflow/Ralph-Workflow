# Ralph Workflow

> **The operating system for autonomous coding.**
>
> **Write the spec. Wake up to working software.**

[![PyPI](https://img.shields.io/pypi/v/ralph-workflow.svg)](https://pypi.org/project/ralph-workflow/)
[![PyPI downloads](https://img.shields.io/pypi/dm/ralph-workflow.svg)](https://pypi.org/project/ralph-workflow/)
[![Python](https://img.shields.io/pypi/pyversions/ralph-workflow.svg)](https://pypi.org/project/ralph-workflow/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

> **GitHub is the mirror. Codeberg is the primary repo.**
> Inspect, star, watch, fork, and open issues on Codeberg first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
>
> Already installed? Run **`ralph star`** to open the repo in your browser. ⭐

Ralph Workflow is a **free and open-source** AI agent orchestrator that runs the coding agents you already use — Claude Code, Codex, OpenCode, Nanocoder, and Google Anti Gravity — on your own machine. Hand it a spec before you sleep, wake up to runnable, tested software.

## What it does

Ralph Workflow takes the simple Ralph-loop idea — plan, build, verify — and turns it into a **composable loop framework** where each phase can loop independently and hand off to the next. A single `ralph` command spawns planning, development iteration, review, and fix cycles across multiple agents, then produces finished git commits you can review in the morning.

**This is not a chat window or a prompt tool.** It's an orchestrator that runs real engineering pipelines unattended — overnight, while you sleep. The default workflow ships strong enough to start with immediately; customize it later when you need more control.

The name comes from the original Ralph loop: repeat a strong prompt until the model can make real progress. Ralph Workflow takes that simple, powerful idea and adds planning before implementation, verification after development, agent fallbacks, agent-agnostic execution, and customizable pipelines so unattended runs keep moving and teams can review the results with confidence.

## Why it's different

| What most tools do | What Ralph Workflow does |
|---|---|
| One agent, one chat session | Multiple agents routed by phase (planning → dev → review → fix) |
| Copy-paste between tools | Agents hand off work through the repo, not context stuffing |
| Hit context limits halfway | Phase-based summaries + checkpoint files keep context tight |
| Locked to one vendor | Claude + Codex + OpenCode + Nanocoder + AGY in the same pipeline — your choice |
| "Look at the diff" | Runnable, tested software with integration checks |

[See how Ralph Workflow compares to 14 other autonomous coding tools →](https://ralphworkflow.com/compare)

## Who it's for

Developers and teams who have **ambitious, well-specified work** that's too big to babysit and too risky to trust blindly.

A good first run looks like:

1. **Write a spec** — what you want built, in plain English or markdown
2. **Run `ralph`** — the orchestrator plans, builds, tests, and iterates
3. **Review the PR** — come back to committed, tested code

**[Start here: your first overnight task →](https://ralphworkflow.com/start)**

New to autonomous coding? The 4-step guide walks you through picking a task, writing a short spec, running Ralph Workflow, and judging the result honestly — all in one page. Prefer a deeper narrative? [Read the blog version →](https://ralphworkflow.com/blog/your-first-overnight-task-start-here-guide)

Start with a bounded, verifiable task — the kind of work you would actually merge. A good first run is 2-6 hours, has a clear boundary, and a concrete correctness check. For a strong first run, pick a task with clear acceptance criteria: "add tests to an existing module so coverage reaches 80%", "refactor one subsystem with existing tests to confirm no regressions", or "build a fitness-app slice with concrete feature checks". The common thread is a well-specified outcome you can judge honestly in the morning, not how small the task is.

## Install

### pipx (recommended)

```bash
pipx install ralph-workflow
ralph --help
```

### PyPI

```bash
pip install ralph-workflow
ralph --help
```

### Docker

```bash
docker run --rm -it -v "$(pwd):/workspace" -v "$HOME/.ralph:/root/.ralph" ralphworkflow/ralph --help
```

Build from source:

```bash
git clone https://codeberg.org/RalphWorkflow/Ralph-Workflow.git
cd Ralph-Workflow/ralph-workflow
docker build -t ralph-workflow .
docker run --rm -it -v "$(pwd):/workspace" -v "$HOME/.ralph:/root/.ralph" ralph-workflow
```

### From source

```bash
git clone https://codeberg.org/RalphWorkflow/Ralph-Workflow.git
cd Ralph-Workflow/ralph-workflow
pip install -e .
ralph --version
```

Requires Python 3.12+.

**[Real-task walkthrough →](https://ralphworkflow.com/blog/real-task-walkthrough-overnight-refactoring)**

## Before your first run

1. Install the agent CLIs you want Ralph Workflow to call.
2. Authenticate those CLIs normally.
3. Pick one small, concrete task for the first run.

Ralph Workflow does not manage provider authentication or store your agent credentials. You authenticate the agent CLIs yourself first, and Ralph Workflow then invokes those tools directly and supervises the workflow, even when different phases are routed through different agent families.

## Quick start

```bash
cd /path/to/your/project
ralph --init
ralph --diagnose
$EDITOR PROMPT.md
ralph
```

What happens in that flow:

- **`ralph --init`** creates the local `.agent/` support files.
- **`ralph --diagnose`** checks whether your configured agents and MCP setup are reachable.
- **`PROMPT.md`** becomes the task spec for the run.
- **`ralph`** directly invokes your configured agent CLIs and starts the unattended workflow.

After `ralph --init`, review the generated `.agent/` support files. If this repository needs a project-local main-config override, run `ralph --init-local-config` to create `.agent/ralph-workflow.toml`, then point the workflow at the agent CLIs you already use for planning, development, and review.

Depth presets control iteration intensity:

```bash
ralph -Q     # quick: small fixes, single iteration
ralph        # standard: most features and tasks
ralph -T     # thorough: complex refactors, ten iterations
```

## A fast way to tell whether Ralph Workflow fits

1. Pick one real backlog task that is small enough to review in one sitting.
2. Write it down in `PROMPT.md` with clear acceptance criteria.
3. Run Ralph Workflow overnight.
4. Come back and ask one question: **would you merge this?**

If yes, give it a harder task next.
If no, tighten the spec, checks, or task choice and run again.

If the first run teaches you something real either way, turn that result into the right public Codeberg action: star/watch the primary repo if it earned trust, or report the exact first-run friction on Codeberg if it did not.

## What to expect from a run

Ralph Workflow is meant to get you to a strong implementation starting point while you are away, not to replace engineering judgment.

A good run should leave you with:

- code that compiles, tests, or clearly shows where work remains
- logs and output that explain what happened
- a result that is worth continuing from, not discarding and restarting

That may be a finished small task, or it may be a substantial first pass toward production on a larger one.

## When Ralph Workflow fits (and when it doesn't)

**Fits:**

- Multi-step tasks that outgrow one prompt
- Work you want to review after the fact instead of steering live
- Teams that want AI execution to stay in the repo
- Runs where you want to mix stronger and cheaper models by phase

**Does not fit:**

- One-shot interactive prompts
- Pair-programming sessions with constant human steering
- Tiny tasks where setup overhead is not worth it
- Workflows that need unpredictable mid-run human input

## Documentation

This README intentionally leaves out deeper implementation details and defers to the `docs/sphinx/` pages for those.

- **Quickstart:** [`docs/sphinx/quickstart.md`](docs/sphinx/quickstart.md) — shorter repeat-use reference with commands and flags
- **Getting Started:** [`docs/sphinx/getting-started.md`](docs/sphinx/getting-started.md) — fuller first-run walkthrough with task guidance
- **Concepts:** [`docs/sphinx/concepts.md`](docs/sphinx/concepts.md) — terminology and mental model
- **CLI Reference:** [`docs/sphinx/cli.md`](docs/sphinx/cli.md) — all flags and sub-commands
- **Configuration:** [`docs/sphinx/configuration.md`](docs/sphinx/configuration.md) — config files and precedence
- **Developer Reference:** [`docs/sphinx/developer-reference.md`](docs/sphinx/developer-reference.md) — maintained contributor and architecture reference
- **Modules Index:** [`docs/sphinx/modules.rst`](docs/sphinx/modules.rst) — API/module entry points for deeper internals

## Privacy & Error Reporting

Ralph Workflow sends anonymous crash reports and performance metrics to help fix bugs and improve reliability. No personal data is collected.

Each installation generates a random 32-character identifier stored in `~/.config/ralph-workflow-user.ini`. This identifier is not tied to your name, email address, IP address, or any other personal data — it is a random string used only to distinguish different installations in crash reports. A fresh random session identifier is generated on every run.

To opt out: delete or rename `~/.config/ralph-workflow-user.ini`. Ralph Workflow creates a new random ID on the next run.

## Community

⭐ **Star the project** — run `ralph star` from your terminal or visit <https://codeberg.org/RalphWorkflow/Ralph-Workflow>.

Every star helps more developers discover Ralph Workflow and drives development priority.

## Development and verification

If you are changing Ralph Workflow itself, start with [`CONTRIBUTING.md`](CONTRIBUTING.md) and run the canonical verification command before you finish:

```bash
make verify
```
