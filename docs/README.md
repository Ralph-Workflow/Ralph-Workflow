# Documentation Map

> **Codeberg is the primary repo for Ralph Workflow.**
> Inspect, follow, and open issues there first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>

This page is the authoritative map for the **current maintained Python** documentation set.
Use it to distinguish current guidance from historical or narrower family-specific material.

## Current vs archival status

- **Current / maintained / Python:** the repo-root README, `START_HERE.md`, the guides in this `docs/` directory, and the maintained Sphinx source under `../ralph-workflow/docs/sphinx/`.
- **Historical / archival / Rust-era:** any older page that still describes cargo, xtask, or non-Python workflow setup should be treated as historical context rather than current operating guidance.

## First-click path for most evaluators

If you are still deciding whether Ralph Workflow is worth trying, use these in order:

1. [README.md](../README.md)
2. [START_HERE.md](../START_HERE.md)
3. [reviewable-output.md](./reviewable-output.md)
4. [after-your-first-run.md](./after-your-first-run.md)

## Documentation families covered here

- **agents** — transport behavior, orchestration, completion, retries, and supervision guides
- **code-style** — code-style expectations, naming, and contributor-facing writing/style references
- **tooling** — CLI, MCP, setup, verification, and other tooling references
- **performance** — timeout, watchdog, runtime, and performance-oriented guidance

## Pick the question you actually have

### First run

- [first-task-guide.md](./first-task-guide.md)
- [first-task-prompt-templates.md](./first-task-prompt-templates.md)
- [which-agent-should-i-start-with.md](./which-agent-should-i-start-with.md)

### Trust and proof

- [reviewable-output.md](./reviewable-output.md)
- [example-review-bundle.md](./example-review-bundle.md)
- [free-open-source-proof.md](./free-open-source-proof.md)

### Product framing and comparisons

- [agent-compatibility.md](./agent-compatibility.md)
- [claude-code-vs-ralph-workflow.md](./claude-code-vs-ralph-workflow.md)
- [ralph-workflow-vs-opencode.md](./ralph-workflow-vs-opencode.md)

### Workflow and usage details

- [quick-reference.md](./quick-reference.md)
- [unattended-coding-agent.md](./unattended-coding-agent.md)
- [why-worktrees-are-not-enough.md](./why-worktrees-are-not-enough.md)

### Family directories and maintained sources

- `agents/` — current agent behavior and orchestration guidance
- `code-style/` — code-style family notes and standards
- `tooling/` — tooling family notes for setup, verification, and operational helpers
- `performance/` — performance family notes for runtime behavior and limits
- `../ralph-workflow/docs/sphinx/` — maintained Sphinx/manual entrypoints for the Python package

## Third-party proof before setup

If you want external inspection before your first run, use a short curated set instead of hunting around:

- [ToolWise review page](https://toolwise.ai/tools/ralph-workflow)
- [SaaSHub product page](https://www.saashub.com/ralph-workflow)
- [TechTools Launchpad listing](https://techtools.cz/tools/launchpad/?tool=71)

## Keep the routing simple

If you feel yourself opening many pages at once, stop.
Use `README -> START_HERE -> one deeper page` as the default path.

## Primary repo

Use Codeberg for issues, follow, and source-of-truth browsing:
<https://codeberg.org/RalphWorkflow/Ralph-Workflow>
