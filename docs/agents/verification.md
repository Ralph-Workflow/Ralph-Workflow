# Required Verification (before PR/completion)

## Canonical command

```bash
cd ralph-python
make verify
```

Verification passes only when all checks complete successfully with **no ERROR/WARNING diagnostics**.

`make verify` runs:
1. `make lint` (`ruff check`)
2. `make typecheck` (`mypy --strict`)
3. `make test-cov` (`pytest` with coverage gate)

If any step fails, fix the issue immediately and rerun verification.

---

## Individual commands (when narrowing failures)

```bash
cd ralph-python
ruff check ralph/ tests/
ruff format --check ralph/ tests/
mypy ralph/ --strict
pytest --cov=ralph --cov-report=term-missing --cov-fail-under=80 -v
python -m ralph --help
python -m ralph --version
```

---

## Policy loader smoke check

```bash
cd ralph-python
python -c "from pathlib import Path; from ralph.policy.loader import load_policy; load_policy(Path('ralph/policy/defaults'))"
```

Run this smoke check whenever policy defaults or loader code changes.
