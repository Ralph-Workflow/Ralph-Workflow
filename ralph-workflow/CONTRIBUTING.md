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
uv run python -m mypy ralph/
uv run python -m ralph.verify_timeout --suite-timeout 30 -- pytest tests/ -q -n 8 --cov=ralph --cov-report=term-missing --cov-report=html --cov-fail-under=80
make test
make test-unit
make test-integration
```

## Documentation expectations

- Update user-facing Markdown when workflows or commands change.
- Update public module/package docstrings when APIs change.
- Keep exported package docstrings self-sufficient enough for `pydoc` users.
- When changing pipeline hardening around artifacts or agent success criteria, document both the behavior change and the failure mode it prevents. Future contributors need to understand why the stricter contract exists.

## Agent hardening contract

When working on `ralph/pipeline/runner.py`, `ralph/phases/`, or Claude/CCS agent invocation, preserve these invariants unless you are deliberately replacing them with something stronger:

1. A clean subprocess exit is not enough evidence of useful work for `review`; `development` and `fix` must still produce real workspace side effects, not empty no-op runs.
2. `review` depends on a fresh per-phase artifact created during the current invocation; `development` and `fix` may emit artifacts or handoffs, but pipeline success must not depend on them.
3. The runner clears stale per-phase artifacts before invoking the agent so interrupted runs cannot satisfy later checks accidentally or leak old summaries into later phases.
4. Claude/CCS MCP invocations must avoid half-configured tool restriction flags. If the live MCP allowlist cannot be discovered, prefer the safer strict-MCP path over emitting brittle `--tools ""` combinations.

This logic is more complex than a naive "agent exited 0" flow, but it exists to prevent silent no-op runs in unattended mode without forcing side-effect-driven phases to produce busywork artifacts. If you change it, update tests and docs together.

## Recovery architecture contract

Recovery, failure classification, retry counting, and chain fallover each have a single conceptual owner in `ralph/recovery/`. Extend the owner, do not add handlers at call sites. New failure modes are added by extending the `FailureClassifier` in `ralph/recovery/classifier.py`, not by sprinkling classification logic at invoke sites.

## Release notes

Builds and publishing are defined in `pyproject.toml` and the repo automation. For local validation, build from this directory:

```bash
rm -rf dist
hatch build
python -m twine check dist/*
```
