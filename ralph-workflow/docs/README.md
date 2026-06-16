# Documentation Map

> **Codeberg is the primary repo for Ralph Workflow.**
> Inspect, follow, and open issues there first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
> The GitHub mirror stays in sync here: <https://github.com/Ralph-Workflow/Ralph-Workflow>

This map distinguishes the **current maintained Python guidance** from historical or narrower family-specific material.
If you are evaluating Ralph Workflow rather than maintaining it, start with the shortest repo-native current path first.

## Current maintained entrypoints

1. `../README.md` — package overview, install path, and current workflow framing
2. `../START_HERE.md` — shortest honest first-run path
3. `../CONTRIBUTING.md` — maintained contributor workflow and verification contract
4. `../docs/sphinx/` — maintained Sphinx docs source for the current Python package
5. `first-task-guide.md` — fastest repo-native filter for choosing the right first backlog task
6. `first-task-prompt-templates.md` — copy-paste first-run specs for evaluators who should try a real task now
7. `reviewable-output.md` — what a good finished run should actually prove
8. `ai-agent-orchestration-cli.md` — what Ralph Workflow is actually for
9. `after-your-first-run.md` — shortest Codeberg-first scorecard after a real run

## Current vs archival guidance

- **Current / maintained / Python:** `../README.md`, `../CONTRIBUTING.md`, `../docs/sphinx/`, and the repo-native guides in this directory.
- **Historical / archival / Rust-era:** if an older note still mentions cargo, xtask, or pre-Python workflow setup, treat it as historical context rather than current operating guidance.

## Documentation families covered here

- **agents** — orchestration, completion, retry, and transport behavior live under `docs/agents/` and `docs/sphinx/agents.md`. Start at [`docs/agents/README.md`](agents/README.md) for adding, updating, or removing agent support.
  - **Adding and managing agent support:** [`docs/agents/README.md`](agents/README.md) — entry point for adding, updating, or removing a built-in or custom agent
- **code-style** — style, naming, and contributor-facing coding expectations live under `docs/code-style/` and the repo-root style documents
- **tooling** — CLI, MCP, verification, and setup/tooling notes live under `docs/tooling/`, `docs/sphinx/cli.md`, and related references
- **performance** — timeout, watchdog, and runtime/performance notes live under `docs/performance/` and the relevant Sphinx architecture pages

## Best first docs for evaluators

- `ai-agent-orchestration-cli.md` — fastest explanation of the product positioning
- `../START_HERE.md` — fastest path to trying Ralph Workflow on one real task
- `first-task-guide.md` — fastest repo-native filter for whether your first task is a good Ralph Workflow fit
- `first-task-prompt-templates.md` — copy-paste first-run specs when a blank `PROMPT.md` would slow you down
- `after-your-first-run.md` — convert a private first run into the right Codeberg-first action
- `reviewable-output.md` — what a strong finished run should contain once the orchestration itself is doing the right work
- `../docs/sphinx/example-review-bundle.md` — public proof bundle before setup
- `../docs/sphinx/which-agent-should-i-start-with.md` — choose the first already-working agent path
- `../docs/sphinx/when-unattended-coding-fits.md` — decide whether your task is a good first unattended run

## Current package docs

- `../README.md` — install, usage, and overview
- `../CONTRIBUTING.md` — contributor workflow
- `../docs/sphinx/` — maintained full docs site source

## Codeberg-first project relationship

- **Promising first run** → star or watch Ralph Workflow on Codeberg
- **Rough first run** → open the matching first-run or docs/proof issue on Codeberg
- **Need the scorecard first** → use `../docs/sphinx/after-your-first-run.md`
