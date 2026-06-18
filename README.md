# Ralph Workflow

> Mirror of [codeberg.org/RalphWorkflow/Ralph-Workflow](https://codeberg.org/RalphWorkflow/Ralph-Workflow) — star/issues/discussion on Codeberg.

**The original Loop Engineering toolkit.** Hand your coding agents a spec. Walk away. Come back to reviewable, tested commits.

Ralph Workflow is a free, open-source Loop Engineering framework that runs the coding agents you already use — Claude Code, Codex, or OpenCode — on your own machine. Simple at the center, powerful in composition.

![PyPI](https://img.shields.io/pypi/v/ralph-workflow.svg) ![PyPI downloads](https://img.shields.io/pypi/dm/ralph-workflow.svg) ![Python](https://img.shields.io/pypi/pyversions/ralph-workflow.svg) ![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)

*The original Loop Engineering toolkit · 10,700+ lifetime PyPI downloads · 4,000+ in the last 30 days (pepy.tech, 2026-06-12).*

> **Built something with Ralph?** See the [Showcase](SHOWCASE.md) — add a credit line to your README and you're on the page (60-second task). Also see the [Ecosystem Map](ECOSYSTEM.md) — projects using Ralph discovered through code-level search.

> **Help us make Ralph better (60 seconds, optional).** If you've installed `ralph-workflow`, we'd love ONE line of feedback: **"How would you feel if you could no longer use Ralph Workflow?"** — Very / Somewhat / Not disappointed. Open a [pmf-survey](https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new?labels=pmf-survey) issue (just the letter V/S/N + a sentence of what you'd use instead), or run `ralph --feedback` after a run. This is the only signal we get — we don't collect telemetry, so this is how we know whether to keep going.

## Install and run

```bash
pipx install ralph-workflow   # 1. install
ralph --init                  # 2. scaffold .agent/ and PROMPT.md
$EDITOR PROMPT.md             # 3. edit PROMPT.md — your spec for the run
ralph                         # 4. run the unattended workflow
```

## What a run leaves you

Here is the actual finish-receipt from the bundled [empty-name-validation example](examples/first-review-bundle/) — a real, unedited handoff you read when you come back instead of a transcript:

```text
# Development Result

## Outcome
Implemented empty-name validation in the CLI create flow and added
test coverage for empty and whitespace-only input.

## Changed files
- cli/create.py
- tests/test_create.py

## Checks run
- pytest tests/test_create.py        ✓ passed
- project formatting / lint checks    ✓ passed

## Reviewer focus
- confirm validation happens before any file creation side effect
- confirm the error message is clear enough for CLI users
- confirm no unrelated flow changed
```

Watch a full first run — the getting-started walkthrough:
[📺 See the first-run guide →](https://ralphworkflow.com/blog/ralph-workflow-in-5-minutes)

> Ralph is free and runs locally — stars are the only signal we get that it's working for you, and they set what we build next. If a run shipped real software for you: ⭐ [star on Codeberg](https://codeberg.org/RalphWorkflow/Ralph-Workflow).

---

## What it does

Ralph Workflow takes the simple Ralph-loop idea — plan, build, verify — and turns it into a **composable loop framework** where each phase can loop independently and hand off to the next. A single `ralph` command spawns planning, development iteration, review, and fix cycles across multiple agents, then produces finished git commits you can review when you come back.

**This is not a chat window or a prompt tool.** It's an orchestrator — an operating system for autonomous coding — that runs real engineering pipelines unattended. Fire it off overnight, over lunch, or while you're in meetings. The default workflow ships strong enough to start with immediately; customize it later when you need more control. The default workflow ships strong enough to start with immediately; customize it later when you need more control.

## Why it's different

| What most tools do | What Ralph Workflow does |
|---|---|
| One agent, one chat session | Multiple agents routed by phase (planning → dev → review → fix) |
| Copy-paste between tools | Agents hand off work through the repo, not context stuffing |
| Hit context limits halfway | Phase-based summaries + checkpoint files keep context tight |
| Locked to one vendor | Claude + Codex + OpenCode in the same pipeline — your choice |
| "Look at the diff" | Runnable, tested software with integration checks |

[See how Ralph Workflow compares to 19 other autonomous coding tools →](https://ralphworkflow.com/compare)

## Who it's for

Developers and teams who have **ambitious, well-specified work** that's too big to babysit and too risky to trust blindly. A good first run looks like:

- The fitness app you wanted to build
- A major product milestone
- A substantial application slice with real acceptance criteria

It is **not** for small tweaks, narrow chores, or vague ideas with no spec.

## Quick start

```
$ ralph --init
$ $EDITOR PROMPT.md
$ ralph
```

Write your task in `PROMPT.md`, then walk away. Ralph reads it, runs planning → development → review cycles, and produces git commits you can inspect when you come back.

### Docker (no Python required)

```bash
docker run --rm -it \
  -v "$(pwd):/workspace" \
  -v "$HOME/.ralph:/root/.ralph" \
  ralphworkflow/ralph --help
```

Build from source:

```bash
git clone https://codeberg.org/RalphWorkflow/Ralph-Workflow.git
cd Ralph-Workflow/ralph-workflow
docker build -t ralph-workflow .
docker run --rm -it -v "$(pwd):/workspace" -v "$HOME/.ralph:/root/.ralph" ralph-workflow
```

### pipx (Python 3.12+)

```bash
pipx install ralph-workflow
ralph --init        # one-time setup: installs agent bundles and capabilities
```

Full docs at [ralphworkflow.com](https://ralphworkflow.com/docs).
Maintainers working in-repo should use `ralph-workflow/docs/sphinx/` as the canonical
source for the published manual.

1. Run `ralph --diagnose` to confirm healthy helpers
2. Write your task in `PROMPT.md` in your project root
3. Run `ralph`
4. Walk away. Come back to finished git commits you can review

That's it — works overnight, over lunch, or while you're heads-down on something else. The default workflow is already strong enough to start with. Customize later when you need more control.

For first-run guidance — task selection, diagnosis, and a walked-through first wake-up — see **[START_HERE.md](START_HERE.md)**.

## See it in action

Example terminal output from Ralph Workflow v0.8.8 on a fresh project:

| Command | Output |
|---|---|
| `ralph --init` | [init-output.txt](docs/sphinx/_static/demo/init-output.txt) — banner, capabilities, first-run setup |
| `ralph --diagnose` | [diagnose-output.txt](docs/sphinx/_static/demo/diagnose-output.txt) — agent inventory, config, MCP check |
| `ralph --dry-run` | [dry-run-output.txt](docs/sphinx/_static/demo/dry-run-output.txt) — pipeline phases and iteration plan |
| ▶ **Full demo** | [Watch the first-run walkthrough](https://ralphworkflow.com/blog/ralph-workflow-in-5-minutes) |
| ⭐ **Contribute** | `ralph contribute` — opens Codeberg in your browser so you can star the project |

These are **unedited terminal captures** from a real run — not mock-ups.

**Built-in capabilities:**

- **Phase routing** — planning agent → development agent → review agent → fix loop
- **Cost arbitrage** — use cheaper agents for planning, stronger ones for coding
- **Repo-based handoff** — agents read each other's output through the repo, not context stuffing
- **Recovery + retry** — each phase can loop independently on failure
- **Vendor-neutral** — your config is YAML, your agents are your choice, your code is yours
- **`ralph --diagnose`** — pre-flight health check for agents, tools, and capability bundles

## Documentation

| Page | What it covers |
|---|---|
| [Getting Started](https://ralphworkflow.com/docs/getting-started.html) | First install, first run, first wake-up |
| [Full Docs](https://ralphworkflow.com/docs) | Quickstart, configuration, user stories, and more |

## Engineering-practice requirements

Ralph Workflow depends on good software engineering practices — it does not replace them. You need:

- **Clear specs** with concrete acceptance criteria
- **Meaningful tests** in your repo
- **Honest review discipline** — the review agent flags issues, you decide what to do

A repo without guardrails will produce results that reflect that. Plan accordingly.

---

*Free and open source. Runs on your machine. Ships with a default workflow strong enough for real software engineering.*
