# Ralph Workflow Code Style

The maintained implementation in this repository is the Python package under `ralph-python/`.

## Source of truth

Current style and quality gates come from:

- `ralph-python/pyproject.toml`
- `ralph-python/CONTRIBUTING.md`
- `docs/agents/verification.md`
- existing package/module docstrings and tests in `ralph-python/`

## Core expectations

- Keep public APIs typed.
- Prefer explicit, readable control flow over clever abstractions.
- Keep public module and package docstrings good enough for `pydoc` to stand on its own.
- Preserve the separation between CLI, orchestration, MCP, Git, and workspace layers.
- Match the existing `ruff`, `mypy`, and `pytest` workflow.

## Legacy note

Older files in `docs/code-style/` may still describe the retired Rust implementation. Treat those as archival background unless the file explicitly says it has been refreshed for Python.