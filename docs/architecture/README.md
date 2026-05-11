# Architecture Docs Index

This directory contains **mixed-state** architecture documentation. Some pages describe current Python behavior; others are historical Rust-era reference. Classification is explicit below.

## Maintained current Python-facing architecture docs

These pages describe current Python implementation behavior and are kept up to date:

- **`pipeline-lifecycle.md`** — End-to-end pipeline lifecycle (planning → development → commit → review/fix loops). Policy-driven orchestration in `ralph/pipeline/`. Current Python behavior.
- **`event-loop-and-reducers.md`** — Event loop, reducer architecture, and policy-based routing. Describes `ralph/pipeline/orchestrator.py` and `ralph/pipeline/reducer.py`. Current Python behavior.
- **`parallel-fan-out.md`** — Same-workspace v1 parallel fan-out architecture. Key constraints: `allowed_directories` path isolation, `.agent/workers/<unit_id>/` namespaces, artifact-based worker completion. The design omits individual git branches, separate filesystem worktrees, and any post-development merge step.

## Historical Rust-era reference (not current Python behavior)

These pages reference retired Rust paths (`src/`, `crates/`, `cargo`, libgit2) and are retained for migration context and design history. They do not describe current Python implementation:

- **`checkpoint-and-resume.md`** — References Rust `src/checkpoint/` paths. For current checkpoint behavior, see `ralph-workflow/ralph/checkpoint/`.
- **`agents-and-prompts.md`** — References Rust `src/agents/` paths. For current agent behavior, see `ralph-workflow/ralph/agents/`.
- **`git-and-rebase.md`** — References Rust libgit2 via `git2` crate. For current git operations, see `ralph-workflow/ralph/git/`.
- **`analysis-agent.md`** — Historical Rust reference.
- **`effect-system.md`** — Historical Rust reference.
- **`streaming-and-parsers.md`** — Historical Rust reference.
- **`mcp-upstream-proxy.md`** — Historical Rust reference.
- **`codebase-tour.md`** — Historical Rust reference.
- **`logging-and-observability.md`** — Historical Rust reference.
- **`memory-budget.md`** — Historical Rust reference.
- **`memory-safety.md`** — Historical Rust reference.

## How to read this directory

1. Check this index to determine whether the page you need is in the **maintained current** list or the **historical Rust** list.
2. Maintained pages point to `ralph-workflow/ralph/` Python modules.
3. Historical pages point to `ralph-workflow/src/` Rust paths — these describe the retired implementation and are not current behavior.
4. For the authoritative current source, prefer Python module docstrings and `ralph-workflow/README.md`.
