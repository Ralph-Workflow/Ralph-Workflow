# Documentation Map

This repository contains both **current Python documentation** and **legacy Rust-era reference material**.

## Current Python docs

Start here for the maintained package:

- `../ralph-python/README.md` — installation, development, and API overview
- `../ralph-python/CONTRIBUTING.md` — contributor workflow for the Python package
- `agents/verification.md` — required verification commands
- `agents/parallelization.md` — parallel development mode guide
- package docstrings under `../ralph-python/ralph/` — authoritative API-level pydoc

## Current architecture entry points

The most reliable architecture references today are the Python modules themselves:

- `../ralph-python/ralph/cli/main.py`
- `../ralph-python/ralph/config/loader.py`
- `../ralph-python/ralph/pipeline/orchestrator.py`
- `../ralph-python/ralph/pipeline/reducer.py`
- `../ralph-python/ralph/phases/__init__.py`
- `../ralph-python/ralph/mcp/server/runtime.py`

## Legacy material

Unless a file explicitly says it has been refreshed for Python, treat these areas as archival background from the retired Rust implementation:

- `architecture/`
- `code-style/`
- `RFC/`
- `plans/`
- parts of `migration/`
- some root-level historical guides

These files are kept because they still contain useful design history, but they are not the source of truth for the Python package.