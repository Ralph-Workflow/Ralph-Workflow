# Ralph Workflow Code Style

The maintained implementation in this repository is the Python package under `ralph-workflow/`.

## Source of truth

Current style and quality gates come from:

- `ralph-workflow/pyproject.toml`
- `ralph-workflow/CONTRIBUTING.md`
- `docs/agents/verification.md`
- existing package/module docstrings and tests in `ralph-workflow/`

## Core expectations

- Keep public APIs typed.
- Prefer explicit, readable control flow over clever abstractions.
- Keep public module and package docstrings self-sufficient for `pydoc` users — external documentation must not be required for local context.
- Maintain Sphinx/internal API documentation that is comprehensive for developers — all public packages, modules, classes, and functions must have substantive docstrings suitable for autodoc.
- Keep public-facing Markdown docs current and self-sufficient — readers must not need external documentation to understand maintained workflow docs.
- Preserve the separation between CLI, orchestration, MCP, Git, and workspace layers.
- Match the existing `ruff`, `mypy`, and `pytest` workflow.

## Legacy note

Older files in `docs/code-style/` may still describe the retired Rust implementation. Treat those as archival background unless the file explicitly says it has been refreshed for Python.