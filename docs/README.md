# Documentation Map

Ralph Workflow is a free and open-source AI agent orchestration system built around a simple core loop inspired by the original Ralph loop.
That simple core composes into a stronger workflow system for substantial, well-specified repo work, and the default workflow is already strong enough to start with before you customize anything.

This page is the authoritative map for the **current maintained Python** documentation set.
Use it after [README.md](../README.md) and [START_HERE.md](../START_HERE.md) to distinguish current guidance from historical or narrower family-specific material.

## Current vs archival status

- **Current / maintained / Python:** the repo-root README, `START_HERE.md`, the guides in this `docs/` directory, and the maintained Sphinx source under `../ralph-workflow/docs/sphinx/`.
- **Historical / archival / Rust-era:** any older page that still describes cargo, xtask, or non-Python workflow setup should be treated as historical context rather than current operating guidance.

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

### I want product framing before I go deeper

- [AI agent orchestration CLI](./ai-agent-orchestration-cli.md)
- [Why the spec still matters](./spec-driven-ai-agent.md)
- [What unattended use should mean](./unattended-coding-agent.md)

## Documentation families and status

The maintained Python path is the current route.
If a page looks historical, archival, or Rust-era, prefer the maintained Python manual and guide families below first.

- `docs/agents/` — current maintainer and verification guidance
- `docs/code-style/` — current Python code style and implementation rules
- `docs/tooling/` — current tooling references
- `docs/performance/` — current performance baselines and monitoring guidance
- `docs/architecture/` — current deeper implementation and runtime design docs
- `docs/RFC/` — historical design records and background context, not the first-run path

## Keep proof secondary

Use proof-oriented pages only after you already understand the product story or the operator route.
If you need deeper evidence, the manual and linked supporting pages will take you there.

## Primary repo

Codeberg is the primary repo and source of truth:
<https://codeberg.org/RalphWorkflow/Ralph-Workflow>
