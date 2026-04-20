# Contributing to Ralph Workflow (Python)

This directory contains the maintained Python package.

## Development setup

```bash
git clone https://codeberg.org/RalphWorkflow/Ralph-Workflow.git
cd Ralph-Workflow/ralph-workflow
make dev
```

To refresh the runnable `ralph` executable from the current checkout, run:

```bash
make install
```

## Required verification

Run this before opening or updating a PR:

```bash
make verify
```

The dead-code audit is available separately while the existing dead-code backlog is still being cleaned up:

```bash
make dead-code
```

`make dead-code` uses Vulture and is expected to fail until the repo is fully cleaned. Keep it separate from `make verify` for now so the tooling can be validated without blocking unrelated work.

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
