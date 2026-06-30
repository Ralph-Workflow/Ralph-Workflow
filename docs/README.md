# Documentation Map

Ralph Workflow is **the autopilot for coding agents** — a free and
open-source operating system for autonomous coding, an AI agent
orchestrator built around a simple Ralph-loop core that becomes powerful
through composition. **Hand it a well-specified coding task, let the
agents plan, build, verify, and fix, and come back to reviewable, tested
work.** The default workflow is already strong enough to adopt as-is
before you customize anything.

Use this page after [README.md](../README.md) and [START_HERE.md](../START_HERE.md).
Those pages explain what Ralph Workflow is and how to judge one honest first run.
This page routes you to the next page that best matches your question.

Every route bullet below is tagged with its doc-family role
(`tutorial` / `how-to` / `reference` / `explanation` / `proof` /
`internals`) so you can match a route to the kind of page you need.

## Choose one route

### I want the fastest first successful run (tutorial)

- [Choose your first task](../ralph-workflow/docs/sphinx/first-task-guide.md) — tutorial
- [First-task prompt templates](../ralph-workflow/docs/sphinx/first-task-prompt-templates.md) — tutorial
- [Getting started in the manual](../ralph-workflow/docs/sphinx/getting-started.md) — tutorial

### I want the maintained operator manual (how-to + reference)

- [Manual home](../ralph-workflow/docs/sphinx/index.rst) — how-to + reference
- [Configuration + Reference](../ralph-workflow/docs/sphinx/configuration.md) — reference
- [User stories](../ralph-workflow/docs/sphinx/user-stories.md) — how-to
- [Run diagnostics before a workflow](../ralph-workflow/docs/sphinx/diagnostics.md) — how-to
- [Agent CLI lifecycle (selection, auth, invocation)](../ralph-workflow/docs/sphinx/agents.md) — how-to

### I need the repo-root docs families mapped clearly (internals)

These repo-root docs are a **map of the surrounding documentation system**, not the main operator manual.
The maintained day-to-day operator path is the Sphinx manual above.
Use these folders only when you know you need contributor guidance or deeper background.

- [Operator manual (docs map)](operator-manual.md) — reference
- [Claims ledger](CLAIMS_LEDGER.md) — internals (every factual claim tracked for fabrication-guard)
- [Verification gate](VERIFICATION_GATE.md) — internals (process that prevents hallucinated claims from reaching public surfaces)
- `docs/agents/` — contributor and verification guidance for agents, testing, type-ignore policy, and verification workflow
- `docs/code-style/` — documentation rubric plus maintained style/process guidance
- `docs/tooling/` — tooling setup and support notes for the Python implementation (`python-tooling.md`)
- `docs/architecture/` — Python-runtime architecture: an `overview.md` plus the
  maintained topical pages `pipeline-lifecycle.md`, `event-loop-and-reducers.md`,
  and `parallel-fan-out.md`
- [Superpowers skill bundles](../ralph-workflow/docs/superpowers/README.md) —
  spec layouts and plan metadata for the maintained Python package

### I want to see a real overnight run before I decide (proof)

- [Real overnight demo: task spec → output](../ralph-workflow/docs/sphinx/overnight-demo-real.md) — proof

### I want product framing before I go deeper (explanation)

- [AI agent orchestration CLI](../ralph-workflow/docs/sphinx/ai-agent-orchestration-cli.md) — explanation
- [Why the spec still matters](../ralph-workflow/docs/sphinx/spec-driven-ai-agent.md) — explanation
- [What unattended use should mean](../ralph-workflow/docs/sphinx/unattended-coding-agent.md) — explanation

### Legacy (Rust-era)

Pages describing the **retired Rust implementation** are quarantined under
[`docs/legacy-rust/`](legacy-rust/README.md), including the archived
`docs/legacy-rust/performance/` performance notes. They are kept for
historical context only; do not rely on them for current behavior.

Start with [Rust Implementation Retired](migration/rust-implementation-retired.md)
for the one-sentence status and the path to the maintained Python package.

## Keep proof secondary

Use proof-oriented pages only after you already understand the product story or the operator route.
If you need deeper evidence, the manual and linked supporting pages will take you there.

## Primary repo

Codeberg is the primary repo and source of truth:
<https://codeberg.org/RalphWorkflow/Ralph-Workflow>