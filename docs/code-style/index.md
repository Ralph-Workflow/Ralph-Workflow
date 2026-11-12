# Project Code Style Guide

This directory (`docs/code-style/`) is the **canonical home** for the
maintained Python code style. It contains only Python-current
material:

- `documentation-rubric.md` — canonical documentation rubric for every
  docs-related change

## Python style source of truth

Use these when working on the maintained package:

- `ralph-workflow/pyproject.toml` — tool configuration (`ruff`,
  `mypy`, packaging)
- `ralph-workflow/CONTRIBUTING.md` — contributor workflow
- public module docstrings under `ralph-workflow/ralph/` — API
  expectations for `pydoc`
- existing Python modules and tests — preferred naming, typing, and
  error-handling patterns

## Python style expectations

- Type all public functions and exported APIs using strict mypy
  (configuration: `ralph-workflow/mypy.ini`).
- Test files carry zero type-checker suppression comments (enforced by
  `test_type_ignore_policy.py`).
- Tracked Python sources carry no inline suppression directive of any
  kind: the absolute comment gate `ralph.testing.audit_comments` fails
  on every comment token, so a suppression lives in tool configuration
  (`ralph-workflow/mypy.ini`) or is replaced with a typed helper, guard,
  adapter, or `cast(...)`. The policy is
  `docs/agents/type-ignore-policy.md`.
- Keep public module and package docstrings self-sufficient so
  `pydoc` is useful without external Markdown.
- Prefer small, explicit modules over deeply clever abstractions.
- Keep CLI, orchestration, MCP, workspace, and Git layers clearly
  separated.
- Match the existing `ruff` / `mypy` / `pytest`-driven development
  flow.
- Run `cd ralph-workflow && make verify` before completion to ensure
  strict type checking passes.

## Type checking requirements

The maintained Python package enforces strict type checking:

- **Strict mypy.** All code must pass
  `cd ralph-workflow && uv run python -m mypy ralph/`.
- **No implicit `Any` escape hatches.** `ralph-workflow/mypy.ini` keeps
  `disallow_any_explicit`, `disallow_any_decorated`,
  `disallow_any_unimported`, `disallow_any_expr`, `strict_equality`,
  `warn_return_any`, `warn_unused_ignores`, `warn_unused_configs`, and
  `enable_error_code = ignore-without-code` enabled.
- **Zero test suppressions.** `test_type_ignore_policy.py` validates
  that tests carry no type-checker suppression comment.
- **No-plugin Pydantic contract.** `ralph-workflow/mypy.ini`
  intentionally does not enable the upstream Pydantic mypy plugin;
  solve Pydantic `Any` leaks with first-party typed helpers and
  adapters instead.
- **No runtime suppressions.** Tracked Python carries no inline
  suppression directive; see `docs/agents/type-ignore-policy.md` and
  prefer a typed helper, guard, adapter, or `cast(...)`.
- **Verification.** `cd ralph-workflow && make verify` runs three
  stages: `make lint` (ruff), `make typecheck` (mypy --strict), and
  `make test` (one 60-second-capped parallel pytest invocation with
  the combined budget enforced by
  `ralph.verify._TOTAL_TEST_BUDGET_SECONDS`).

These requirements are non-negotiable and enforced by CI/CD gates.

## Suppression policy

- Test files must contain **zero** type-checker suppression comments,
  and tracked Python carries no inline suppression directive of any
  kind: the absolute comment gate `ralph.testing.audit_comments` fails
  on every comment token, so a suppression lives in tool configuration
  (`ralph-workflow/mypy.ini`) or is replaced with a typed helper, guard,
  adapter, or `cast(...)`. The policy is
  `docs/agents/type-ignore-policy.md`.
- Inline lint suppressions are rejected the same way:
  `ralph.testing.audit_lint_bypass` fails on a bare or code-specific
  inline lint suppression in the maintained tree.
- `[tool.ruff.lint.per-file-ignores]` in `ralph-workflow/pyproject.toml`
  exempts rule codes per path pattern. The `tests/**/*.py` pattern
  lists the annotation (`ANN`), import-placement (`PLC0415`),
  line-length (`E501`), and test-style codes that pytest fixtures and
  parametrize patterns require — tests can omit annotations and import
  inside test functions to isolate module-load side effects between
  cases. The annotation codes are test-only: production code under
  `ralph/` is fully annotated.

## Canonical verification commands

Always prefix Ruff / mypy / pytest with `uv run` so the uv-managed
binary matches what `make verify` actually executes. The `--no-cache`
qualifier on Ruff is mandatory because cached runs may undercount
errors:

```bash
cd ralph-workflow
uv run ruff check ralph/ tests/ --no-cache
uv run ruff format --check ralph/ tests/
uv run python -m mypy ralph/
make verify
```

The maintained-tree suppression policy is additionally enforced by
`tests/test_type_ignore_policy.py`, which walks
`ralph-workflow/**/*.py` (excluding `.venv`) at the COMMENT-token level
and flags any type-checker suppression comment. Because the inventory
inspects comment tokens only, string-literal occurrences inside test
data or assertion messages are not flagged.