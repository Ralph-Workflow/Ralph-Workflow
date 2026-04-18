# Architecture Docs Index

This directory mostly contains architecture notes from the retired Rust implementation. The maintained product is the Python package in `ralph-workflow/`.

## Current source of truth

For current behavior, prefer the Python modules directly:

- `ralph-workflow/ralph/cli/main.py` — CLI entry point and top-level control flow
- `ralph-workflow/ralph/config/loader.py` — layered config loading
- `ralph-workflow/ralph/pipeline/orchestrator.py` — pure effect routing
- `ralph-workflow/ralph/pipeline/reducer.py` — pure state transitions
- `ralph-workflow/ralph/phases/__init__.py` — phase registration and dispatch
- `ralph-workflow/ralph/git/` — GitPython-backed repository operations
- `ralph-workflow/ralph/mcp/` — MCP bridge, artifacts, and standalone server runtime
- `ralph-workflow/ralph/workspace/` — filesystem abstraction for production and tests

## How to read this directory now

- Files that still describe crates, `cargo`, `libgit2`, or Rust-only effect layers are **historical reference**, not current implementation docs.
- Keep them for migration context unless and until they are ported to Python.
- Use `docs/README.md` and `ralph-workflow/README.md` for the current documentation entry points.

## Current mental model

The Python package still follows the same high-level orchestration shape:

```text
state -> orchestrator -> effect -> handler -> event -> reducer -> next state
```

The difference is that the implementation now lives in Python, with Git operations handled through GitPython and CLI/config/prompt systems living under `ralph-workflow/ralph/`.