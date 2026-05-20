# Architecture Docs

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


This directory holds mixed-state architecture documentation: some pages describe current Python behavior, others are historical Rust-era reference.

## Maintained: Current Python Behavior

These pages are kept current with the Python implementation in `ralph-workflow/ralph/`:

- **`pipeline-lifecycle.md`** — End-to-end pipeline lifecycle: planning, development, commit, review, and fix loops. Policy-driven orchestration via `ralph/pipeline/`.
- **`event-loop-and-reducers.md`** — Event loop, reducer architecture, and policy-based routing. Covers `ralph/pipeline/orchestrator.py` and `ralph/pipeline/reducer.py`.
- **`parallel-fan-out.md`** — Same-workspace v1 parallel fan-out. Key constraints: `allowed_directories` path isolation, `.agent/workers/<unit_id>/` namespaces, artifact-based worker completion. No per-worker git branches or post-development merge step.

## Historical: Rust-Era Reference

These pages reference retired Rust paths (`src/`, `crates/`, `cargo`, libgit2). They are retained for migration context and design history, not current behavior:

| Page | Rust path referenced |
|------|---------------------|
| `checkpoint-and-resume.md` | `src/checkpoint/` |
| `agents-and-prompts.md` | `src/agents/` |
| `git-and-rebase.md` | libgit2 via `git2` crate |
| `analysis-agent.md` | Historical Rust reference |
| `effect-system.md` | Historical Rust reference |
| `streaming-and-parsers.md` | Historical Rust reference |
| `mcp-upstream-proxy.md` | Historical Rust reference |
| `codebase-tour.md` | Historical Rust reference |
| `logging-and-observability.md` | Historical Rust reference |
| `memory-budget.md` | Historical Rust reference |
| `memory-safety.md` | Historical Rust reference |

For current behavior, prefer Python module docstrings and `ralph-workflow/README.md`.
