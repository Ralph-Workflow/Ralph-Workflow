# Documentation Map

Ralph Workflow is a free and open-source AI agent orchestration system built around a simple Ralph-loop core.
That simple core composes into a stronger composable workflow system for substantial, well-specified repo work, and the default workflow is already strong enough to start with before you customize anything.


Use this page after [README.md](../README.md) and [START_HERE.md](../START_HERE.md).
Those pages explain what Ralph Workflow is and how to judge one honest first run.
This page routes you to the next page that best matches your question.

## Choose one route

### I want the fastest first successful run

- [Choose your first task](./first-task-guide.md)
- [First-task prompt templates](./first-task-prompt-templates.md)
- [Getting started in the manual](../ralph-workflow/docs/sphinx/getting-started.md)

### I want the maintained operator manual

- [Manual home](../ralph-workflow/docs/sphinx/index.rst)
- [Configuration](../ralph-workflow/docs/sphinx/configuration.md)
- [Reference](../ralph-workflow/docs/sphinx/reference.md)
- [User stories](../ralph-workflow/docs/sphinx/user-stories.md)

### I need the repo-root docs families mapped clearly

These repo-root docs are a **map of the surrounding documentation system**, not the main operator manual.
The maintained day-to-day Python/operator path is the Sphinx manual above.
These bullets are status notes, not recommended next-click routes for normal end users.

- `docs/agents/` — current Python contributor and verification guidance; maintainer-facing, not a normal first click
- `docs/code-style/` — current Python style and documentation-rubric guidance, plus some historical pages; use selectively
- `docs/tooling/` — mixed-status tooling notes; prefer current Python-specific guidance when available
- `docs/performance/` — mostly archival Rust-era material, not the main Python operator path

### I want product framing before I go deeper

- [AI agent orchestration CLI](./ai-agent-orchestration-cli.md)
- [Why the spec still matters](./spec-driven-ai-agent.md)
- [What unattended use should mean](./unattended-coding-agent.md)

## Keep proof secondary

Use proof-oriented pages only after you already understand the product story or the operator route.
If you need deeper evidence, the manual and linked supporting pages will take you there.

## Primary repo

Codeberg is the primary repo and source of truth:
<https://codeberg.org/RalphWorkflow/Ralph-Workflow>
