# Documentation Map

This repository contains both **current Python documentation** and **legacy Rust-era reference material**.

## Current Python docs

Start here for the maintained package:

- `../ralph-workflow/README.md` — installation, development, and API overview
- `../ralph-workflow/CONTRIBUTING.md` — contributor workflow for the Python package
- `agents/verification.md` — required verification commands
- `agents/type-ignore-policy.md` — suppression policy for `# type: ignore[...]` usage
- `agents/parallelization.md` — parallel development mode guide
- `agents/testing-guide.md` — testing patterns for the Python package
- `agents/workspace-trait.md` — workspace trait documentation
- package docstrings under `../ralph-workflow/ralph/` — authoritative API-level pydoc

## Compatibility redirect stubs (agents/)

These files are retained for backwards compatibility but redirect to canonical guides:

- `agents/python-verification.md` — redirects to `agents/verification.md`
- `agents/integration-tests.md` — redirects to `agents/testing-guide.md`

## Current architecture entry points

The most reliable architecture references today are the Python modules themselves:

- `../ralph-workflow/ralph/cli/main.py`
- `../ralph-workflow/ralph/config/loader.py`
- `../ralph-workflow/ralph/pipeline/orchestrator.py`
- `../ralph-workflow/ralph/pipeline/reducer.py`
- `../ralph-workflow/ralph/phases/__init__.py`
- `../ralph-workflow/ralph/mcp/server/runtime.py`

## Tooling family (current Python)

The `tooling/` family covers Python development tooling:

- `tooling/python-tooling.md` — Python-specific development toolchain

Note: `tooling/remote-build.md` and `tooling/dylint.md` are historical Rust-era references retained for archival purposes.

## Performance family (historical Rust-era reference)

The `performance/` family contains historical Rust-era performance documentation
retained for reference. These documents describe the retired Rust implementation's
performance characteristics and are not current Python guidance:

- `performance/README.md` — historical performance overview
- `performance/memory-budget.md` — historical memory management (Rust)
- `performance/monitoring-guide.md` — historical performance monitoring (Rust)
- `performance/optimization-guide.md` — historical optimization patterns (Rust)

For Python performance guidance, refer to the package docstrings and Sphinx documentation.

## Operational guides (current Python)

- `agent-compatibility.md` — provider capability matrix
- `quick-reference.md` — command quick reference
- `template-guide.md` — template usage
- `git-workflow.md` — Git workflow with Ralph

## Legacy material

Unless a file explicitly says it has been refreshed for Python, treat these areas as archival background from the retired Rust implementation:

- `architecture/`
- `code-style/`
- `RFC/`
- `plans/`
- parts of `migration/`
- some root-level historical guides

These files are kept because they still contain useful design history, but they are not the source of truth for the Python package.