# Contributing to Ralph Workflow

Ralph Workflow is now a **Python-first** project. The maintained CLI package lives in `ralph-workflow/`.

## Start here

```bash
git clone https://codeberg.org/RalphWorkflow/Ralph-Workflow.git
cd Ralph-Workflow/ralph-workflow
python -m pip install -e ".[dev]"
make verify
```

## Source of truth

Use these first when changing the Python package:

- `ralph-workflow/README.md`
- `ralph-workflow/CONTRIBUTING.md`
- `docs/agents/verification.md`
- package docstrings in `ralph-workflow/ralph/`

## Required verification

```bash
cd ralph-workflow
make verify
```

Verification is clean only when the Python checks pass without unresolved errors:

- `ruff check ralph/ tests/`
- `mypy ralph/`
- `pytest tests/ -v --cov=ralph --cov-report=term-missing --cov-report=html`

## Documentation expectations

- Update Markdown docs when user-facing behavior changes.
- Keep public module docstrings accurate enough for `pydoc` users to understand the package without external docs.
- Prefer package and module docstrings for API explanation; prefer Markdown for workflows and tutorials.

## Repository layout

- `ralph-workflow/` — maintained Python package
- `docs/` — mixed current Python docs and legacy Rust-era design notes
- `vendor/ralph-workflow-gui/` — vendored Rust/Tauri GUI

## Legacy docs

Some root-level docs and historical design notes still describe the retired Rust implementation. Treat those as archival background unless the file explicitly says it has been refreshed for Python.

## Pull requests

- Keep changes focused.
- Explain the why.
- Include tests when behavior changes.
- Update docs and docstrings together when you add or reshape public APIs.

## License

By contributing, you agree your contributions are licensed under AGPL-3.0-or-later.