# Documentation Map

Ralph Workflow is **the autopilot for coding agents** —
a free and open-source operating system for autonomous coding, an AI agent
orchestrator built around a simple Ralph-loop core that becomes powerful
through composition.
The default workflow is already strong enough to adopt as-is before you
customize anything.


Use this page after [README.md](../README.md) and [START_HERE.md](../START_HERE.md).
Those pages explain what Ralph Workflow is and how to judge one honest first run.
This page routes you to the next page that best matches your question.

## Choose one route

### I want the fastest first successful run

- [Choose your first task](../ralph-workflow/docs/sphinx/first-task-guide.md)
- [First-task prompt templates](../ralph-workflow/docs/sphinx/first-task-prompt-templates.md)
- [Getting started in the manual](../ralph-workflow/docs/sphinx/getting-started.md)

### I want the maintained operator manual

- [Manual home](../ralph-workflow/docs/sphinx/index.rst)
- [Configuration + Reference](../ralph-workflow/docs/sphinx/configuration.md)
- [User stories](../ralph-workflow/docs/sphinx/user-stories.md)
- [Run diagnostics before a workflow](../ralph-workflow/docs/sphinx/diagnostics.md)
- [Agent CLI lifecycle (selection, auth, invocation)](../ralph-workflow/docs/sphinx/agents.md)

### I need the repo-root docs families mapped clearly

These repo-root docs are a **map of the surrounding documentation system**, not the main operator manual.
The maintained day-to-day operator path is the Sphinx manual above.
Use these folders only when you know you need contributor guidance or deeper background.

- `docs/agents/` — contributor and verification guidance for agents, testing, type-ignore policy, and verification workflow
- `docs/code-style/` — documentation rubric plus maintained style/process guidance
- `docs/tooling/` — tooling setup and support notes for the Python implementation (`python-tooling.md`)
- `docs/architecture/` — Python-runtime architecture: an `overview.md` plus the
  maintained topical pages `pipeline-lifecycle.md`, `event-loop-and-reducers.md`,
  and `parallel-fan-out.md`

### I want to see a real overnight run before I decide

- [Real overnight demo: task spec → output](../ralph-workflow/docs/sphinx/overnight-demo-real.md)

### I want product framing before I go deeper

- [AI agent orchestration CLI](../ralph-workflow/docs/sphinx/ai-agent-orchestration-cli.md)
- [Why the spec still matters](../ralph-workflow/docs/sphinx/spec-driven-ai-agent.md)
- [What unattended use should mean](../ralph-workflow/docs/sphinx/unattended-coding-agent.md)

### Legacy (Rust-era)

Pages describing the **retired Rust implementation** are quarantined under
[`docs/legacy-rust/`](legacy-rust/README.md), including the archived
`docs/legacy-rust/performance/` performance notes. They are kept for
historical context only; do not rely on them for current behavior.

## Keep proof secondary

Use proof-oriented pages only after you already understand the product story or the operator route.
If you need deeper evidence, the manual and linked supporting pages will take you there.

## Primary repo

Codeberg is the primary repo and source of truth:
<https://codeberg.org/RalphWorkflow/Ralph-Workflow>
