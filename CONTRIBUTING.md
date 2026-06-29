# Contributing to Ralph Workflow

> **Primary repo:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
> Use Codeberg for stars, watches, forks, issues, and contribution tracking. GitHub stays in sync as a mirror for GitHub-native readers: <https://github.com/Ralph-Workflow/Ralph-Workflow>
> If you decide Ralph Workflow is worth following before you contribute, put that signal on Codeberg so the primary repo keeps the trust and adoption history in one place.

Ralph Workflow is now a **Python-first** project. The maintained CLI package lives in `ralph-workflow/`.

## Fastest way to help after a real first run

If you just tried Ralph Workflow, the highest-signal contribution is no longer a vague suggestion. It is one of these Codeberg-first actions:

- **Star or watch the primary repo:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Report first-run friction:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>
- **Report a docs / proof gap:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>

The new issue forms are designed to capture the exact adoption bottleneck: what blocked a real first run, what proof was missing, and what would have made the project easier to trust.

If you are not sure whether your first run earned a star, a watch, or a bug report, use [after-your-first-run.md](./ralph-workflow/docs/sphinx/after-your-first-run.md) first.

The intended post-run branch is simple:

- **Useful run:** star or watch the primary repo on Codeberg
- **Blocked run:** open the matching first-run or docs/proof issue form on Codeberg

That keeps first-run trust signals and first-run friction on the same primary surface.

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

Canonical verification is the `make verify` command run from the `ralph-workflow/` directory. It runs clean only when **all** of the following checks pass without unresolved errors:

- `ruff check ralph/ tests/` — lint and format
- `mypy ralph/` — type checking
- `pytest tests/ -v --cov=ralph --cov-report=term-missing --cov-report=html` — unit and integration tests with coverage
- `make docs` — Sphinx HTML build completes warning-free
- subprocess E2E smoke tests via the `test-subprocess-e2e` target

Use focused sub-commands (e.g. `uv run ruff check ralph/`) only when narrowing a specific failure. The authoritative gate is always `make verify`.

## Documentation expectations

- Update Markdown docs when user-facing behavior changes.
- Keep public module docstrings accurate enough for `pydoc` users to understand the package without external docs.
- Prefer package and module docstrings for API explanation; prefer Markdown for workflows and tutorials.

## Repository layout

- `ralph-workflow/` — maintained Python package
- `docs/` — mixed current Python docs and legacy Rust-era design notes

## Legacy docs

Some root-level docs and historical design notes still describe the retired Rust implementation. Treat those as archival background unless the file explicitly says it has been refreshed for Python.

## Pull requests

- Keep changes focused.
- Explain the why.
- Include tests when behavior changes.
- Update docs and docstrings together when you add or reshape public APIs.

## License

By contributing, you agree your contributions are licensed under AGPL-3.0-or-later.
