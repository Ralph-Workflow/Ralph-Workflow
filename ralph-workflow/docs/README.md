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
5. `ai-agent-orchestration-cli.md` — what Ralph Workflow is actually for
6. `first-task-guide.md` — fastest repo-native filter for choosing the right first backlog task
7. `reviewable-output.md` — what a good finished run should actually prove

## Current vs archival guidance

- **Current / maintained / Python:** `../README.md`, `../CONTRIBUTING.md`, `../docs/sphinx/`, and the repo-native guides in this directory.
- **Historical / archival / Rust-era:** if an older note still mentions cargo, xtask, or pre-Python workflow setup, treat it as historical context rather than current operating guidance.

## Documentation families covered here

- **agents** — orchestration, completion, retry, and transport behavior live under `docs/agents/` and `docs/sphinx/agents.md`
- **code-style** — style, naming, and contributor-facing coding expectations live under `docs/code-style/` and the repo-root style documents
- **tooling** — CLI, MCP, verification, and setup/tooling notes live under `docs/tooling/`, `docs/sphinx/cli.md`, and related references
- **performance** — timeout, watchdog, and runtime/performance notes live under `docs/performance/` and the relevant Sphinx architecture pages

## Best first docs for evaluators

- `ai-agent-orchestration-cli.md` — fastest explanation of the product positioning
- `../START_HERE.md` — fastest path to trying Ralph Workflow on one real task
- `first-task-guide.md` — fastest repo-native filter for whether your first task is a good Ralph Workflow fit
- `after-your-first-run.md` — convert a private first run into the right Codeberg-first action
- `reviewable-output.md` — what a strong finished run should contain once the orchestration itself is doing the right work
- `spec-driven-ai-agent.md` — practical evaluation path for spec-first intent
- `claude-code-automation.md` — practical evaluation path for Claude Code automation intent
- `claude-code-approval-mode.md` — practical repo-native path if approval mode is still the thing keeping Claude Code from feeling unattended
- `ralph-workflow-vs-opencode.md` — direct repo-native comparison for OpenCode evaluators
- `unattended-coding-agent.md` — practical page for unattended-coding-agent intent
- `../docs/sphinx/example-review-bundle.md` — public proof bundle before setup
- `../docs/sphinx/which-agent-should-i-start-with.md` — choose the first already-working agent path
- `../docs/sphinx/when-unattended-coding-fits.md` — decide whether your task is a good first unattended run

## Current package docs

- `../README.md` — install, usage, and overview
- `../CONTRIBUTING.md` — contributor workflow
- `../docs/sphinx/` — maintained full docs site source
- `mcp-tool-restriction.md` — MCP tool restriction notes

## Third-party proof before setup

If you want external inspection before your first run, use a short curated set instead of hunting around:

- [ToolWise review page](https://toolwise.ai/tools/ralph-workflow)
- [SaaSHub product page](https://www.saashub.com/ralph-workflow)
- [TechTools Launchpad listing](https://techtools.cz/tools/launchpad/?tool=71)

## Codeberg-first project relationship

- **Promising first run** → star or watch Ralph Workflow on Codeberg
- **Rough first run** → open the matching first-run or docs/proof issue on Codeberg
- **Need the scorecard first** → use `../docs/sphinx/after-your-first-run.md`
