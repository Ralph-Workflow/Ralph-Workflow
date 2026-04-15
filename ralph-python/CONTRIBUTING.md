# Contributing to Ralph Workflow (Python)

This directory contains the maintained Python package.

## Development setup

```bash
git clone https://codeberg.org/RalphWorkflow/Ralph-Workflow.git
cd Ralph-Workflow/ralph-python
python -m pip install -e ".[dev]"
```

## Required verification

Run this before opening or updating a PR:

```bash
make verify
```

You can narrow failures with:

```bash
ruff check ralph/ tests/
ruff format --check ralph/ tests/
mypy ralph/
pytest tests/ -v --cov=ralph --cov-report=term-missing --cov-report=html
```

## Documentation expectations

- Update user-facing Markdown when workflows or commands change.
- Update public module/package docstrings when APIs change.
- Keep exported package docstrings self-sufficient enough for `pydoc` users.

## Release notes

Builds and publishing are defined in `pyproject.toml` and the repo automation. For local validation, build from this directory:

```bash
rm -rf dist
hatch build
python -m twine check dist/*
```