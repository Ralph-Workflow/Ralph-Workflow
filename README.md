# Ralph Workflow

## ⚡ Quickstart

```bash
pipx install ralph-workflow   # 1. install (Python 3.12+)
ralph --init                  # 2. scaffold .agent/ and PROMPT.md
ralph                         # 3. run the unattended workflow
```

## Stop babysitting your coding agents.

**Hand off a spec, step away, and come back to tested code worth reviewing.**

**Autopilot for your coding agents.** Ralph Workflow runs the coding agents you already use — Claude Code, Codex, or OpenCode — **unattended**, on your own machine. Free, open-source, local-first. *(Underneath: a composable loop framework.)*

Use it for well-specified work that can run against your repo's tests while you do something else.

**The reference implementation of Loop Engineering.** Three org-backed commercial derivatives — [Atomic](https://github.com/bastani-inc/atomic) (260★), [Ralphify](https://github.com/computerlovetech/ralphify) (68★), and [LoopTroop](https://github.com/looptroop-ai/loop-troop) (38★) — have adopted the architecture, with 20+ independent projects and 450+ cumulative ecosystem stars. *(Source: [ECOSYSTEM.md](ECOSYSTEM.md), verified 2026-06-28).*

![Codeberg stars](https://img.shields.io/codeberg/stars/RalphWorkflow/Ralph-Workflow) ![GitHub stars](https://img.shields.io/github/stars/Ralph-Workflow/Ralph-Workflow) ![PyPI](https://img.shields.io/pypi/v/ralph-workflow.svg) ![PyPI downloads](https://img.shields.io/pypi/dm/ralph-workflow.svg) ![Python](https://img.shields.io/pypi/pyversions/ralph-workflow.svg) ![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)  
[![Built with Ralph Loop](assets/built-with-ralph-loop.svg)](https://ghuntley.com/ralph)

*13,796 lifetime PyPI downloads · 4,814 in the last 30 days (source: [pepy.tech](https://pepy.tech/projects/ralph-workflow), verified 2026-06-26).*

🌐 **[ralphworkflow.com](https://ralphworkflow.com)** — comparison guides, first-run walkthrough, and the [Loop Engineering blog](https://ralphworkflow.com/blog).

🌐 **[English](#)** | **[中文](README.zh.md)**

## You've been flying with a copilot. Ralph Workflow is the autopilot for it.

Every AI coding tool today is a **copilot** — Cursor, GitHub Copilot, interactive Claude Code. A copilot keeps you in the seat, hands on, assisting in real time. That's right for the hard parts and wrong for the long stretch between them, where you're approving an agent's work prompt-by-prompt for a task you already specified.

Ralph Workflow is the **autopilot** for that stretch. It commands the coding agents you already run — it doesn't replace them, and you stay pilot in command.

You stay responsible for the important judgment calls: write the spec, start the run, exercise the result, confirm it works, read the diff, and decide what merges. Ralph Workflow handles the long middle: plan, build, verify, fix, and return a concrete handoff instead of a chat transcript.

## Install and run

```bash
pipx install ralph-workflow   # 1. install (Python 3.12+)
ralph --init                  # 2. scaffold .agent/ and PROMPT.md
$EDITOR PROMPT.md             # 3. write PROMPT.md — your spec for the run
ralph                         # 4. run the unattended workflow, then walk away
```

`ralph --init` installs the agent bundles and capabilities and seeds a batteries-included `.gitignore`. Run `ralph --diagnose` first if you want to confirm your agents and tools are healthy before a run.

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

New here? **[START_HERE.md](START_HERE.md)** is the fastest serious first-run path — task selection, diagnosis, and a walked-through first wake-up.

## What a run leaves you

This is an excerpt from the actual finish-receipt in the bundled [empty-name-validation example](examples/first-review-bundle/) — a real, unedited handoff you read when you come back, instead of a transcript:

```text
## Outcome

Implemented empty-name validation in the CLI create flow and added test coverage for empty and whitespace-only input.

## What changed

- added an early guard before any project files are created
- return a clear user-facing error for empty or whitespace-only names
```

The [full receipt](examples/first-review-bundle/.agent/DEVELOPMENT_RESULT.md) tells you exactly where to point your review. The same bundle also includes the review agent's `ISSUES.md` — where it caught that the first pass missed whitespace-only names like `"   "` — and the `FIX_RESULT.md` from the loop fixing it: plan → build → review → fix, captured on disk. Watch a full first run: [📺 the first-run walkthrough →](https://ralphworkflow.com/blog/ralph-workflow-in-5-minutes).

## What it does

Ralph Workflow takes the simple Ralph-loop idea — plan, build, verify — and turns it into a **composable loop framework** where each phase can loop independently and hand off to the next. A single `ralph` command spawns planning, development iteration, review, and fix cycles across multiple agents, then produces finished git commits you can review when you come back.

**This is not a chat window or a prompt tool.** It's an orchestrator that runs real engineering pipelines unattended. The default workflow works out of the box; customize it when you need more control.

### Why it's different

| What most tools do | What Ralph Workflow does |
|---|---|
| One agent, one chat session (a copilot) | Multiple agents routed by phase: planning → dev → review → fix |
| Copy-paste between tools | Agents hand off work through the repo, not context stuffing |
| Hit context limits halfway | Phase-based summaries + checkpoint files keep context tight |
| Locked to one vendor | Claude + Codex + OpenCode in the same pipeline — your choice |
| "Look at the diff" | Runnable, tested software with integration checks |

[See how Ralph Workflow compares to 19 other autonomous coding tools →](https://ralphworkflow.com/compare)

**Built-in capabilities:**

- **Phase routing** — planning agent → development agent → review agent → fix loop
- **Cost arbitrage** — use cheaper agents for planning, stronger ones for coding
- **Repo-based handoff** — each phase reads the previous one's output from the repo
- **Recovery + retry** — each phase can loop independently on failure
- **Vendor-neutral** — your config is YAML, your agents are your choice, your code is yours
- **`ralph --diagnose`** — health check for agents, tools, and capability bundles

Sample unedited terminal captures from a real run (Ralph Workflow v0.8.8): [`ralph --init`](docs/sphinx/_static/demo/init-output.txt) · [`ralph --diagnose`](docs/sphinx/_static/demo/diagnose-output.txt) · [`ralph --dry-run`](docs/sphinx/_static/demo/dry-run-output.txt).

## Who it's for

If one of these describes you, you're the reason Ralph Workflow exists:

**The solo builder.** You have side projects with real spec depth — you know what to build, but you're one person. Set `PROMPT.md` before bed, wake up to reviewed commits.

**The team lead.** Ralph Workflow fits between PR and review — unattended verification that your agents are shipping what you asked for, not what they guessed.

**The AI tool builder.** You're already wiring Claude Code into your workflow. Ralph Workflow gives you the loop pattern — phase routing, cost arbitrage, recovery — as infrastructure instead of something you'd build yourself.

**Ralph Workflow is not for** one-line fixes, vague prompts, or repos without tests. It's for **ambitious, well-specified work** you'd trust a capable colleague to do unattended. A repo without guardrails will produce results that reflect that.

## What builders say

> "I actually have a working implementation on my fork that converged on the same two primitives (`progress.json` + a wake-up file)."
>
> — **[CY Hsieh](https://github.com/Martingale42/superpowers/tree/main/skills/orchestrator-driven-development)**, building orchestrator-driven development on Ralph Workflow

> "I was missing: a system not primped on one language or framework, a straightforward repeatable workflow (plan → implement → record), a permanent spec-library, a system that keeps asking me instead of making assumptions."
>
> — **[Marco Nae](https://codeberg.org/RalphWorkflow/Ralph-Workflow)**, star-gazer, speq-skill maintainer

> "A Claude Code skill for running Claude unattended on a planned work track — overnight, day-trip, multi-hour meeting block."
>
> — **[endario](https://github.com/endario/unattended-loop)**, unattended-loop, a derivative Ralph skill

[Star Ralph Workflow on Codeberg →](https://codeberg.org/RalphWorkflow/Ralph-Workflow)

## Before you hand off a run

Ralph Workflow depends on good engineering practice — it doesn't replace it. A run is only as good as what you give it:

- **Clear specs** with concrete acceptance criteria
- **Meaningful tests** in your repo — they're how the loop knows it's done
- **Honest review discipline** — the review agent flags issues; you decide what to do

**What you need first.** Python 3.12+ on macOS, Linux, or Windows, plus the agent CLIs you want Ralph Workflow to drive (Claude Code, Codex, OpenCode, and more) — installed and authenticated by you. Ralph Workflow invokes those tools and supervises the loop; it never stores your credentials. [Full agent setup →](https://ralphworkflow.com/docs/getting-started.html)

**Where it runs, and staying safe.** Ralph Workflow runs on your machine and commits to your **current branch** — so start each run on a throwaway branch or worktree you can reset, and preview the plan first with `ralph --dry-run` (it invokes no agents). There's no OS-level sandbox, and because the run is unattended, Ralph Workflow launches the agent CLIs in auto-approve mode by default (for example Claude's `--permission-mode auto`, Codex's `--dangerously-bypass-approvals-and-sandbox`): they act without stopping for per-action approval, within whatever your shell and the CLI permit. Ralph Workflow's own MCP exec tool refuses a blacklist of dangerous commands — privilege escalation, `rm -rf /`, container escape, external network calls — but an agent can still reach the shell through its own CLI, so the real boundary is branch isolation plus running and reviewing the result yourself.

**What bounds a run, and what it costs.** A run is capped by iteration budgets (the default development budget is 5 cycles), not an open-ended loop; override them per run with `--counter`. Ralph Workflow itself is free, but it spends real tokens through your agent providers — an unattended multi-agent loop is not cheap, so size the task and the budget to match, and use cheaper models for planning (that's what cost arbitrage is for).

**If a run goes sideways.** Checkpoints let you resume an interrupted run with `--resume`. To undo work you don't want, it's plain git — `git revert` or reset the branch. Nothing is auto-merged; every change waits for your review.

## Part of the Ralph Loop ecosystem

Ralph Workflow is one of 27+ independent implementations of the Ralph Loop pattern documented in [USERS.md](USERS.md). The pattern is attributed to [Geoffrey Huntley](https://ghuntley.com/ralph), and [awesome-ralph](https://github.com/snwfdhmp/awesome-ralph) tracks the wider ecosystem.

**Ecosystem by category** (from [ECOSYSTEM.md](ECOSYSTEM.md)):

| Category | Count | Example |
|----------|-------|----------|
| CLI / orchestrators | 9 | Ralph Workflow, umputun/ralphex, bastani-inc/atomic |
| Agent SDKs / frameworks | 5 | computerlovetech/ralphify, benikigai/nightshift |
| Platform / CI pipelines | 4 | self-serve loop engineering platforms |
| IDE / editor integrations | 3 | Loop Engineering in VS Code, Emacs, Neovim |
| Skilling / learning packs | 3 | Skill packs, workshops, cookbooks |
| SDK / composite agents | 2 | Agent-to-agent orchestration |
| Workflow as Code | 1 | YAML/DSL-declared pipelines |
| Developer Experience | 1 | CLI tools, IDE helpers |

Built something with Ralph Workflow? Add a credit line to your README and you're on the [Showcase](SHOWCASE.md).

### Independent validators

The pattern is emerging independently. These projects ship loop-engineered agent runners — no affiliation, same architecture:

| Project | Stars | Approach |
|---------|-------|----------|
| [SantanderAI/ralph](https://github.com/SantanderAI/ralph) | 77 ⭐ | Bash/PowerShell — fresh-session Claude Code, Codex, and Gemini CLI loop | (verify: gh)
| [rxdt/py_ralph_frame](https://github.com/rxdt/py_ralph_frame) | 5 ⭐ | Python harness — lightweight spec-driven loop, `uvx`-installable | (verify: gh)
| [anthropics/ralph-loop plugin](https://github.com/anthropics/claude-plugins-official/tree/main/plugins/ralph-loop) | official | Anthropic's Claude Code plugin implementing the Ralph Loop pattern |

Ralph Workflow is the feature-complete orchestrator above these — composable phase routing, cost arbitrage, checkpoint/resume, and repo-based agent handoff. The validators each explore a different corner of the same loop-engineering space. [See all 27+ projects →](USERS.md) · [Explore the ecosystem →](ECOSYSTEM.md)

## Documentation

| Page | What it covers |
|---|---|
| [START_HERE.md](START_HERE.md) | The fastest serious first-run path |
| [Getting Started](https://ralphworkflow.com/docs/getting-started.html) | First install, first run, first wake-up |
| [Full Docs](https://ralphworkflow.com/docs) | Quickstart, configuration, user stories, and more |

Maintainers working in-repo should treat `ralph-workflow/docs/sphinx/` as the canonical source for the published manual.

## Community

Questions, ideas, or something not working? The Codeberg tracker is the project's public forum. Ralph Workflow collects zero telemetry, so issues and feedback are how we learn what to build.

- **[Star on Codeberg](https://codeberg.org/RalphWorkflow/Ralph-Workflow)** — stars are the only signal we get that Ralph Workflow is working for you, and they set what we build next
- **[Ask a question](https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new?labels=question)** — anything from "will this work for my stack?" to feature ideas
- **[PMF survey](https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new?labels=pmf-survey)** (60 seconds) — *"How would you feel if you could no longer use Ralph Workflow?"* Very / Somewhat / Not disappointed, plus a sentence on what you'd use instead. Or run `ralph --feedback` after a run.
- **[Contribute code](ralph-workflow/CONTRIBUTING.md)** — setup, verification, and the contribution workflow
- **[See who's building with Ralph Workflow](USERS.md)** — and the wider Ralph Loop ecosystem
- **[Read the blog](https://ralphworkflow.com/blog)** — Loop Engineering explained, ecosystem tours, and migration guides

The canonical forge is [codeberg.org/RalphWorkflow/Ralph-Workflow](https://codeberg.org/RalphWorkflow/Ralph-Workflow); GitHub is a mirror.

---

*Free and open source. Runs on your machine. Ships with a default workflow strong enough for real software engineering — write the spec, run `ralph`, and judge it by the finished commits.*
