# Ralph Workflow

> **The operating system for autonomous coding.**
>
> Ralph Workflow is a **free and open-source** AI agent orchestrator that runs the coding agents you already use — Claude Code, Codex, OpenCode — on your own machine. Hand it a spec before you sleep, wake up to runnable, tested software.

**⭐ Star on Codeberg** → [codeberg.org/RalphWorkflow/Ralph-Workflow](https://codeberg.org/RalphWorkflow/Ralph-Workflow) (primary)
**GitHub mirror** → [github.com/Ralph-Workflow/Ralph-Workflow](https://github.com/Ralph-Workflow/Ralph-Workflow)

---

## What it does

Ralph Workflow takes the simple Ralph-loop idea — plan, build, verify — and turns it into a **composable loop framework** where each phase can loop independently and hand off to the next. A single `ralph run` spawns planning, development iteration, review, and fix cycles across multiple agents, then produces a finished artifact bundle you can inspect in the morning.

**This is not a chat window or a prompt tool.** It's an orchestrator that runs real engineering pipelines unattended — overnight, while you sleep.

## Why it's different

| What most tools do | What Ralph Workflow does |
|---|---|
| One agent, one chat session | Multiple agents routed by phase (planning → dev → review → fix) |
| Copy-paste between tools | Agents hand off work through repo-native artifacts |
| Hit context limits halfway | Phase-based summaries + checkpoint files keep context tight |
| Locked to one vendor | Claude + Codex + OpenCode in the same pipeline — your choice |
| "Look at the diff" | Runnable, tested software with integration checks |

## Who it's for

Developers and teams who have **ambitious, well-specified work** that's too big to babysit and too risky to trust blindly. A good first run looks like:

- The fitness app you wanted to build
- A major product milestone
- A substantial application slice with real acceptance criteria

It is **not** for small tweaks, narrow chores, or vague ideas with no spec.

## What you wake up to

```
$ ralph run --spec plan.md --output ./done/
```

The next morning:

```bash
ls ./done/
plan_final.md       # what it decided to build
build_artifacts/    # the actual code
test_report.md      # integration + unit test results
review_findings.md  # what the review agent flagged and fixed
```

Runnable, testable, ready to evaluate. Not a chat log — finished work.

## Install

```bash
pipx install ralph-workflow
ralph --init        # one-time setup: installs agent bundles and capabilities
ralph --help
```

Requires Python 3.12+. Full docs at [ralphworkflow.com](https://ralphworkflow.com/docs).

## Quick start

1. Write a spec in `plan.md` — what you want built, with acceptance criteria
2. Run `ralph run --spec plan.md`
3. Go to sleep. Wake up to finished artifacts in `./done/`
4. Read `done/review_findings.md` to decide: merge, iterate, or discard

That's it. The default workflow is already strong enough to start with. Customize later when you need more control.

## Built-in capabilities

- **Phase routing** — planning agent → development agent → review agent → fix loop
- **Cost arbitrage** — use cheaper agents for planning, stronger ones for coding
- **Artifact handoff** — agents read each other's output through the repo, not context stuffing
- **Recovery + retry** — each phase can loop independently on failure
- **Vendor-neutral** — your config is YAML, your agents are your choice, your code is yours
- **`ralph --diagnose`** — pre-flight health check for agents, tools, and capability bundles

## Documentation

| Page | What it covers |
|---|---|
| [Getting Started](https://ralphworkflow.com/docs/getting-started.html) | First install, first run, first wake-up |
| [Quickstart](https://ralphworkflow.com/docs/quickstart.html) | Write a spec and run it in 10 minutes |
| [Configuration](https://ralphworkflow.com/docs/configuration.html) | Agent routing, phase policies, model selection |
| [User Stories](https://ralphworkflow.com/docs/user-stories.html) | Real workflows from real runs |
| [Walkthrough](https://ralphworkflow.com/blog/real-task-walkthrough-overnight-refactoring/) | Step-by-step overnight refactoring example |

## Engineering-practice requirements

Ralph Workflow depends on good software engineering practices — it does not replace them. You need:

- **Clear specs** with concrete acceptance criteria
- **Meaningful tests** in your repo
- **Honest review discipline** — the review agent flags issues, you decide what to do

A repo without guardrails will produce results that reflect that. Plan accordingly.

---

**⭐ Star on Codeberg** if this looks useful → [codeberg.org/RalphWorkflow/Ralph-Workflow](https://codeberg.org/RalphWorkflow/Ralph-Workflow)

*Free and open source. Runs on your machine. Ships with a default workflow strong enough for real software engineering.*
