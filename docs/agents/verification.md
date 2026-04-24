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
3. `make docs` (`sphinx-build -W --keep-going` into `docs/sphinx/_build/html`)
4. `make test-cov` (`pytest` with coverage gate)

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
make docs
python -m ralph --help
python -m ralph --version
```

`make test` runs the full suite without coverage. `make test-unit` excludes `tests/integration/`. `make test-integration` runs only `tests/integration/`. `make test-cov` is the single authoritative covered suite used by `make verify`. `make docs` builds the Sphinx HTML docs into `docs/sphinx/_build/html` with warnings treated as errors.

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

## Recovery tests

When working on or verifying the failure recovery feature:

```bash
cd ralph-workflow
uv run pytest -x tests/recovery/
uv run pytest -x tests/test_recovery_first_invariant.py tests/test_reducer.py tests/test_pipeline_runner.py tests/test_asyncio_bridge.py tests/test_checkpoint.py
```

Recovery tests cover:
- Network loss / connectivity monitoring (environmental failures do not count against budget)
- Agent empty output / idle timeout (agent failures count against budget, trigger fallover)
- Agent chain exhaustion (full chain → PHASE_FAILED, fallover history populated)
- Recovery cycle cap (global cap prevents infinite loops, emits ExitFailureEffect)
- Worker failures and merge conflicts (do not terminate pipeline, route through recovery)
- Pre-flight validation (invalid chain / missing agent caught at startup with exit code 2)
- User interrupt (first SIGINT → ordered shutdown; second → os._exit(130))

All recovery tests run in under 10 seconds each and use injectable fake clocks, fake sleep, and fake probes — no real network I/O in tests.
