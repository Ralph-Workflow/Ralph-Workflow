# Documentation Map

Ralph Workflow is a free and open-source AI agent orchestration system built around a simple core loop inspired by the original Ralph loop.
That simple core composes into a stronger composable workflow system for substantial, well-specified repo work, and the default workflow is already strong enough to start with before you customize anything.

Use this page after [README.md](../README.md) and [START_HERE.md](../START_HERE.md).
Those pages explain what Ralph Workflow is and how to judge one honest first run.
This page routes you to the next page that best matches your question.

## Current maintained route

- [Choose your first task](./first-task-guide.md)
- [First-task prompt templates](./first-task-prompt-templates.md)
- [Getting started in the manual](../ralph-workflow/docs/sphinx/getting-started.md)
- [Manual home](../ralph-workflow/docs/sphinx/index.rst)
- [Configuration](../ralph-workflow/docs/sphinx/configuration.md)
- [Reference](../ralph-workflow/docs/sphinx/reference.md)
- [User stories](../ralph-workflow/docs/sphinx/user-stories.md)

## Current vs archival guidance

- **Current / maintained / Python:** `README.md`, `START_HERE.md`, this docs map, and the maintained manual under `ralph-workflow/docs/sphinx/`.
- **Historical / archival / Rust-era:** older notes under `docs/` that still describe cargo, xtask, or pre-Python runtime behavior should be treated as background context rather than current operator guidance.

## Documentation families

- **agents** — orchestration, verification, testing, and transport guidance live under `docs/agents/` and the maintained operator page at `ralph-workflow/docs/sphinx/agents.md`.
- **code-style** — documentation rules, naming guidance, and contributor-facing style expectations live under `docs/code-style/`; check `documentation-rubric.md` first for public-doc work and treat Rust-era notes in that family as archival when they conflict with current Python behavior.
- **tooling** — setup, CLI, MCP, and Python-tooling notes live under `docs/tooling/` plus the maintained manual pages such as `ralph-workflow/docs/sphinx/cli.md`.
- **performance** — timeout, watchdog, and runtime-performance notes live under `docs/performance/`; the Python package is current, while explicit Rust-era performance notes in that family are archival.

## Product framing before you go deeper

- [AI agent orchestration CLI](./ai-agent-orchestration-cli.md)
- [Why the spec still matters](./spec-driven-ai-agent.md)
- [What unattended use should mean](./unattended-coding-agent.md)

## Keep proof secondary

Use proof-oriented pages only after you already understand the product story or the operator route.
If you need deeper evidence, the manual and linked supporting pages will take you there.

## Primary repo

Codeberg is the primary repo and source of truth:
<https://codeberg.org/RalphWorkflow/Ralph-Workflow>
