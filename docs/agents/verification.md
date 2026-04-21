# Required Verification (before PR/completion)

## Canonical command

```bash
cd ralph-workflow
make verify
```

Verification passes only when all checks complete successfully with **no ERROR/WARNING diagnostics**.

`make verify` runs:
1. `make lint` (`ruff check`)
2. `make typecheck` (`mypy --strict`)
3. `make test-cov` (`pytest` with coverage gate)

If any step fails, fix the issue immediately and rerun verification.

---

## Dead-code audit

```bash
cd ralph-workflow
make dead-code
```

`make dead-code` runs Vulture with the repository's `pyproject.toml` configuration and is intentionally separate from `make verify` while the repo still contains known dead code. Today it is a validation and cleanup command: the expected behavior is a non-zero exit until the backlog is removed.

---

## Individual commands (when narrowing failures)

```bash
cd ralph-workflow
ruff check ralph/ tests/
ruff format --check ralph/ tests/
uv run python -m mypy ralph/
make test
make test-unit
make test-integration
make test-cov
python -m ralph --help
python -m ralph --version
```

`make test` runs the full suite without coverage. `make test-unit` excludes `tests/integration/`. `make test-integration` runs only `tests/integration/`. `make test-cov` is the single authoritative covered suite used by `make verify`.

---

## Policy loader smoke check

```bash
cd ralph-workflow
python -c "from pathlib import Path; from ralph.policy.loader import load_policy; load_policy(Path('ralph/policy/defaults'))"
```

Run this smoke check whenever policy defaults or loader code changes.

---

## Parallel-mode tests

When working on or verifying the parallel development feature:

```bash
cd ralph-workflow
uv run pytest -q tests/integration/test_parallel_resume.py
uv run pytest -q tests/integration/test_runner_fanout_wiring.py
uv run pytest -q tests/integration/test_old_checkpoint_loads.py
```
