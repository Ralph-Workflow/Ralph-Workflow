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
- Test files must have zero `# type: ignore` or `# pyright:`
  suppressions (enforced by `test_type_ignore_policy.py`).
- Runtime suppressions must follow
  `docs/agents/type-ignore-policy.md` — prefer a typed helper, guard,
  adapter, or `cast(...)` first.
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
  no `# type: ignore` in tests.
- **No-plugin Pydantic contract.** `ralph-workflow/mypy.ini`
  intentionally does not enable the upstream Pydantic mypy plugin;
  solve Pydantic `Any` leaks with first-party typed helpers and
  adapters instead.
- **Policy-compliant runtime suppressions.** See
  `docs/agents/type-ignore-policy.md`; first prefer a typed helper,
  guard, adapter, or `cast(...)`.
- **Verification.** `cd ralph-workflow && make verify` runs three
  stages: `make lint` (ruff), `make typecheck` (mypy --strict), and
  `make test` (one 60-second-capped parallel pytest invocation with
  the combined budget enforced by
  `ralph.verify._TOTAL_TEST_BUDGET_SECONDS`).

These requirements are non-negotiable and enforced by CI/CD gates.

## Suppression policy

- Test files must contain **zero** `# type: ignore` or `# pyright:`
  comment suppressions. Runtime code may carry a suppression only with
  the exact policy reason suffix from `docs/agents/type-ignore-policy.md`.
- `# noqa: PLW0603` at `ralph/cli/commands/run.py:_get_run_func` is the
  sanctioned mutation pattern for the typed `run_module._run_func`
  test seam. It is a Ruff-rule suppression, not a type-checker
  suppression, carries a same-line rationale, and is not counted by
  the maintained-tree suppression inventory.
- `[tool.ruff.lint.per-file-ignores]` in `ralph-workflow/pyproject.toml`
  exempts `"tests/**" = ["ANN", "PLC0415"]`. Tests can omit
  annotations (Ruff `ANN` family) and import inside test functions
  (`PLC0415`) because pytest fixtures and parametrize patterns
  require importing inside test functions to isolate module-load side
  effects between cases. Production code under `ralph/` does not get
  either exemption.

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

The maintained-tree suppression policy is additionally enforced by a
tokenize-based inventory that walks `ralph-workflow/**/*.py` (excluding
`.venv`) at the COMMENT-token level and flags any `# type: ignore` or
`# pyright:` comment. Required output:
`No maintained-tree suppression comments found.` with exit code 0.
Because the inventory inspects comment tokens only, string-literal
occurrences inside test data or assertion messages are not flagged.