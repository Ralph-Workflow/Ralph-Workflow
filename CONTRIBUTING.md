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
make dev          # dev build — installs the package in editable mode with the dev extras
make verify       # the canonical verification gate
```

The dev build must NOT be installed as the global `ralph` (it would shadow the
stable build) — leave the global install under `pipx install ralph-workflow`
and run `make dev` only inside the working tree. For the dev-build vs
stable-build distinction, see [`ralph-workflow/CONTRIBUTING.md`](ralph-workflow/CONTRIBUTING.md)
"Development setup".

Use `make install` instead of `make dev` when you only want the `rdev`
launcher (a stable binary that does NOT shadow `ralph`) — see the package
CONTRIBUTING for the exact semantics.

## Source of truth

The canonical source-of-truth list lives in [`AGENTS.md`](AGENTS.md)
"Source of truth". When changing the Python package, the priority order is:

1. `PROMPT.md` (root — the canonical brief/rubric for the project)
2. `ralph-workflow/CONTRIBUTING.md`
3. `docs/agents/verification.md`
4. `ralph-workflow/docs/agents/artifact-submission-contract.md`
5. `ralph-workflow/README.md`
6. Python source and docstrings under `ralph-workflow/ralph/`
7. `docs/code-style/documentation-rubric.md` (canonical documentation rubric)

## Required verification

```bash
cd ralph-workflow
make verify
```

Canonical verification is the `make verify` command run from the `ralph-workflow/` directory. `make verify` runs three prerequisites in order:

1. `verify-drift` — invariant checks that protect the Pro contract and other architectural guarantees.
2. `docs` — Sphinx HTML build with warnings-as-errors (`sphinx-build -W --keep-going`), so any documentation warning fails the gate before the Python verification step runs.
3. `ralph.verify` — the **18-step pipeline** documented as the single source of truth in [`docs/agents/verification.md`](docs/agents/verification.md) (ruff, mypy, the pytest run tracked against the 60-second combined test budget, plus 15 audits and the social-proof gate).

Coverage and subprocess E2E remain separate opt-in targets:

- `make test-cov` — coverage gate; not part of `make verify`.
- `make test-subprocess-e2e` — subprocess E2E suite; not part of `make verify`.

Use focused sub-commands (e.g. `uv run ruff check ralph/`) only when narrowing a specific failure. The authoritative gate is always `make verify`; refer to `docs/agents/verification.md` for the full ordered step list, the 60-second combined test budget, the non-circumvention rules, and the per-step timeout invariants.

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
