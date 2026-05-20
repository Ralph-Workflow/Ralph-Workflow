# Ralph Workflow — Unattended AI Coding Workflow Orchestrator

**Free, open-source CLI tool** that orchestrates AI coding agents (Claude Code, Codex, OpenCode) into a reviewable unattended workflow.

Run tonight. Review in the morning. Merge if earned.

[![Codeberg](https://img.shields.io/badge/Codeberg-Primary-blue?logo=codeberg)](https://codeberg.org/RalphWorkflow/Ralph-Workflow)
[![GitHub Mirror](https://img.shields.io/badge/GitHub-Mirror-lightgray?logo=github)](https://github.com/Ralph-Workflow/Ralph-Workflow)

**👉 [Star / Watch on Codeberg](https://codeberg.org/RalphWorkflow/Ralph-Workflow)** — primary repo  
[GitHub mirror](https://github.com/Ralph-Workflow/Ralph-Workflow) — mirror only

---

## TL;DR — Start in 5 minutes

1. **Pick one backlog task** you already care about
2. **Write a one-paragraph spec** — what needs to change, what stays untouched, what "done" looks like
3. **Run it tonight** with Ralph Workflow
4. **Wake up to a reviewable diff** — changed files, tests that ran, reasoning trail

Then ask: **would I merge this?**

If you are unsure whether your task is shaped correctly, use **[Good unattended task vs bad one](./content/guides/good_unattended_task.md)** before your first run.

That's the whole evaluation.

If the answer is **yes**, do the public next step on **Codeberg**:
- star the repo
- watch the repo
- open an issue for any friction you hit

## What Ralph Workflow does

Ralph Workflow pushes a task through three phases:

1. **Sharpen the task** — tighten unclear requirements before code starts
2. **Build, verify, and fix** — code, checks, and repair happen in the same loop
3. **Land a reviewable result** — the output is something you can inspect, test, and decide whether to merge

The point is simple:

> **proof of completion, not just a claim it is done**

## The problem

A lot of AI coding tools can generate code quickly.

That is not the hard part.

The hard part is getting a result that:
- matches the task you actually asked for
- survives checks instead of just claiming success
- lands as a **reviewable diff**
- gives you a **clean re-entry point** after an unattended run

Too often, the tool says it is done before the job actually holds up.

## When it works best

Ralph Workflow is strongest on:
- backlog items
- bounded feature slices
- refactors with tests
- repetitive implementation work with clear checks

It is a worse fit for:
- vague product direction
- risky production surgery with no rollback room
- tasks where nobody agrees what "done" looks like

## Why people use it

- **No new toolchain required** — keep your current agents
- **Unattended runs with a clean finish** — not just a long session transcript
- **Reviewable output** — changed files, checks, reasoning trail, open questions
- **Works across providers** — use the tools you already trust

## Quick links

- 📋 **[Start here: one real task](./START_HERE_RALPHWORKFLOW.md)** — fastest honest first run
- ✅ **[Good unattended task vs bad one](./content/guides/good_unattended_task.md)** — choose a first task you can actually judge
- 🧭 **[Spec-driven AI agent guide](./content/guides/spec_driven_ai_agent.md)** — why the spec is the real trust surface
- 📁 **[First-task example](./content/examples/first_task_example.md)** — what a good first task looks like
- 📦 **[Review bundle example](./content/examples/review_bundle_example.md)** — what a finished result should look like
- 🤝 **[Contributing](./CONTRIBUTING.md)** — bugs, docs fixes, and real first-run feedback

## Core framing

Ralph Workflow is for work that is:
- **too big to babysit**
- **too risky to trust blindly**

The goal is to:
- hand off the job tonight
- review the result in the morning
- decide honestly whether you would merge it

If you are comparing approaches, start with the **[spec-driven AI agent guide](./content/guides/spec_driven_ai_agent.md)** and then inspect the primary repo on **Codeberg**.

## If Ralph Workflow fits your workflow

Use **Codeberg** as the main public home:
- ⭐ **Star the repo** — helps other developers find it
- 👀 **Watch for updates** — follow the project's progress
- 🐛 **File issues** — report friction, not just bugs
- 🔧 **Open PRs** — real improvements welcome

Use GitHub only if you strongly prefer the mirror.

## License

AGPL-3.0

---

**Free and open source** · [Codeberg](https://codeberg.org/RalphWorkflow/Ralph-Workflow) (primary) · [GitHub](https://github.com/Ralph-Workflow/Ralph-Workflow) (mirror)
