# Required Verification (before PR/completion)

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


## Canonical command

```bash
cd ralph-workflow
make verify
```

`make verify` runs:
1. `make lint` (`ruff check`)
2. `make typecheck` (`mypy --strict`)
3. `make test` (pytest, parallel, excludes `subprocess_e2e`)
4. Lint bypass audit (`ralph/testing/audit_lint_bypass.py`) — detects forbidden noqa, per-file-ignores
5. Typecheck bypass audit (`ralph/testing/audit_typecheck_bypass.py`) — detects non-compliant type:ignore, mypy config weakening (including `disable_error_code`)
6. Policy audit (`ralph/testing/audit_test_policy.py`) — detects slow test patterns, I/O in tests (including `os.system` and `os.popen` subprocess calls)

### Total test budget — 60 seconds, ABSOLUTE and IMMUTABLE

**THE 60-SECOND COMBINED TOTAL TEST BUDGET IS ABSOLUTE AND IMMUTABLE.**
It cannot be changed, overridden, or circumvented.

`make verify` runs `make test`, which executes one maintained parallel pytest invocation over `tests/` with `-m "not subprocess_e2e"`.

Budget enforcement:
- **Per-suite cap** (applies to `make test`, `make test-unit`, and `make test-integration`): each `python -m ralph.verify_timeout --suite-timeout 60` call is killed after 60 s. This cap is defined in `ralph/verify_timeout.py:DEFAULT_SUITE_TIMEOUT_SECONDS`. Per-suite caps are SECONDARY only — raising them does not increase the combined budget.
- **Combined-total cap** (enforced by `make verify` only): `ralph.verify` tracks cumulative wall-clock time via `time.monotonic()` across ALL test steps in `_BUDGET_TRACKED_STEPS`. The combined total must not exceed `_TOTAL_TEST_BUDGET_SECONDS` (60 s), defined at `ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS`. Adding suites, splitting tests, or creating new test targets does NOT reset or extend the budget — the cumulative tracker sums time across ALL tracked steps. If cumulative test time exceeds 60 s, `make verify` fails with `(budget exhausted — cumulative test time exceeded)`.

This combined limit is **IMMUTABLE** — the following do **NOT** circumvent it:
- Adding more test suites or shards
- Renaming test targets
- Moving tests between targets or suites
- Setting environment variables (RALPH_PYTEST_SUITE_TIMEOUT_SECONDS, etc.)
- Changing Makefile variables (PYTEST_SUITE_TIMEOUT_SECONDS)
- Modifying DEFAULT_SUITE_TIMEOUT_SECONDS or DEFAULT_TEST_TIMEOUT_SECONDS

**Example:** splitting your test suite into 3 separate test steps does NOT give you 3 × 60 s = 180 s of total budget. The cumulative tracker sums the elapsed time of every budget-tracked step, so all 3 suites together must still finish within the single 60 s combined cap.

| Attempted Circumvention | Why It Fails |
|---|---|
| Splitting tests into N suites | Cumulative tracker sums time across ALL tracked steps — NOT per-suite |
| Moving slow tests to a different suite/target | All test-budget-tracked steps count toward the same combined budget |
| Adding new test-related Makefile targets | `_BUDGET_TRACKED_STEPS` frozen set stays at `{2}` — new targets don't get budget tracking |
| Raising `PYTEST_SUITE_TIMEOUT_SECONDS` | Per-suite cap only; combined budget is enforced separately by `_TOTAL_TEST_BUDGET_SECONDS` |
| Modifying `_BUDGET_TRACKED_STEPS` frozenset | Blocked by `ralph/verify.py` import-time RuntimeError checks — immune to `python -O` |
| Raising `_TOTAL_TEST_BUDGET_SECONDS` | Blocked by import-time RuntimeError epsilon check (`must be 60.0`) — immune to `python -O` |
| Emptying `_KNOWN_TEST_STEP_LABELS` | Blocked by import-time non-empty RuntimeError check — immune to `python -O` |
| Emptying `_BUDGET_TRACKED_STEPS` | Blocked by import-time non-empty RuntimeError check — immune to `python -O` |
| Removing `'make test'` from `_KNOWN_TEST_STEP_LABELS` | Blocked by import-time containment RuntimeError check — immune to `python -O` |
| Adding `per-file-ignores` to pyproject.toml | Detected by `ralph/testing/audit_lint_bypass.py` (part of `make verify`) |
| Adding `ignore_missing_imports = true` to mypy config | Detected by `ralph/testing/audit_typecheck_bypass.py` (part of `make verify`) |
| Using bare `# noqa` or blanket `# type: ignore` | Detected by bypass audit modules with file:line reporting |

#### Verification Invariants

`ralph/verify.py` enforces import-time invariants to prevent accidental
or malicious weakening of budget enforcement:

- `_TOTAL_TEST_BUDGET_SECONDS > 0` — the budget must be positive.
- `_BUDGET_TRACKED_STEPS` indices are valid indices into `_VERIFY_STEPS` —
  all tracked steps must exist.
- Every budget-tracked step has a positive timeout — budget enforcement
  cannot be silently nullified by a `None` or zero timeout.
- An epsilon check (`abs(_TOTAL_TEST_BUDGET_SECONDS - 60.0) < 1e-9`)
  confirms the constant has not been altered from its ABSOLUTE and
  IMMUTABLE value of 60.0 seconds.
- `_KNOWN_TEST_STEP_LABELS` must not be empty — prevents silently
  hiding all test steps from budget tracking.
- `_BUDGET_TRACKED_STEPS` must not be empty — prevents disabling
  budget enforcement entirely.
- `'make test'` must be present in `_KNOWN_TEST_STEP_LABELS` — the
  canonical test step label always exists and is tracked.
- Every label in `_KNOWN_TEST_STEP_LABELS` must correspond to a
  tracked step, and every tracked step must have its label in
  `_KNOWN_TEST_STEP_LABELS` — prevents label/steps drift.

These invariants are checked at module import time. Any violation causes
a `RuntimeError` at startup (enforced by `if`/`raise` — immune to `python -O`
stripping), preventing `make verify` from running with
a weakened budget configuration.

All invariants are tested in `tests/test_verify_invariants.py` under
both normal execution and `python -O` to confirm survivability.

A timeout failure is a test design defect. Fix the production coupling — never adjust the budget. Remove I/O, use `MemoryWorkspace`, inject fake clocks. Do **not** raise `DEFAULT_SUITE_TIMEOUT_SECONDS` or `PYTEST_SUITE_TIMEOUT_SECONDS` to mask a slow test.

Verification passes only when all checks complete with **no ERROR/WARNING diagnostics**. If any step fails, fix the issue immediately and rerun. `make verify` emits a high-visibility failure banner that cites `AGENTS.md` and `CLAUDE.md`.

### Bypass Audit Policy

The `make verify` pipeline includes automated bypass audits that scan the entire codebase for lint and typecheck circumvention. These audits are **non-optional** — they run as mandatory steps and cannot be skipped.

**Lint bypass audit** (`ralph/testing/audit_lint_bypass.py`):
- Detects bare `# noqa` without a specific error code (must use `# noqa: CODE`)
- Detects `# noqa: CODE` where CODE is not in the allowlist
- Detects `per-file-ignores` and `extend-per-file-ignores` in `pyproject.toml`
- Violations produce output: `file:line: [LINT-BYPASS] category: detail`

**Typecheck bypass audit** (`ralph/testing/audit_typecheck_bypass.py`):
- Detects blanket `# type: ignore` without a specific mypy error code
- Detects `# type: ignore[CODE]` without a policy-compliant reason marker (see `../docs/agents/type-ignore-policy.md` for exact format requirements)
- Detects `# type: ignore` in test files (tests must be fully typed — no exceptions)
- Detects ALL mypy config that weakens type checking: `ignore_missing_imports = true`, `follow_imports = silent`, `exclude` patterns, `ignore_errors = true`, `disable_error_code` (globally suppresses error codes)
- Violations produce output: `file:line: [TYPECHECK-BYPASS] category: detail`

**Both audits** scan both `ralph/` and `tests/` directories. Each uses an allowlist of known-legitimate suppressions. Any new suppression that does not match the allowlist IS a violation. To add a legitimate suppression, the code must be added to the allowlist with a documented justification.

Reference: `AGENTS.md` §'Non-negotiables' for the full non-circumvention rules.

If the change touches README, docs, START_HERE, the manual, or any public-doc route, read `docs/code-style/documentation-rubric.md` first and check the edited surface against it before calling the docs work done.

For full verification (including docs build and subprocess E2E tests):

```bash
cd ralph-workflow
make docs
make test-subprocess-e2e
```

---

## Individual commands

```bash
cd ralph-workflow
make lint
make typecheck
make test
make test-unit
make test-integration
make test-cov
make docs
uv run ruff check ralph/ tests/
uv run ruff format --check ralph/ tests/
uv run python -m mypy ralph/
python -m ralph --help
python -m ralph --version
```

`make test` runs the maintained verification suite as one parallel invocation over `tests/` with `-m "not subprocess_e2e"`, wrapped in the 60-second suite timeout. `make test-unit` excludes `tests/integration/`. `make test-integration` runs only `tests/integration/`. `make test-cov` enforces an 80% coverage gate. `make docs` builds Sphinx HTML with warnings as errors.

---

## Dead-code audit

```bash
cd ralph-workflow
make dead-code
```

Runs Vulture with `pyproject.toml` config. Intentionally separate from `make verify` while known dead code remains. Non-zero exit until the backlog is cleared.

---

## Policy loader smoke check

```bash
cd ralph-workflow
python -c "from pathlib import Path; from ralph.policy.loader import load_policy; load_policy(Path('ralph/policy/defaults'))"
```

Run after changing policy defaults or loader code.

---

## Parallel-mode tests

```bash
cd ralph-workflow
uv run pytest -q tests/integration/test_parallel_resume.py
uv run pytest -q tests/integration/test_runner_fanout_wiring.py
uv run pytest -q tests/integration/test_old_checkpoint_loads.py
```

## Parallel worker bootstrap tests

Dedicated worker bootstrap isolates each fan-out worker's prompt, checkpoint, and runtime state under `.agent/workers/<unit_id>/`. These tests verify the isolation contract:

```bash
cd ralph-workflow
uv run pytest -q tests/test_parallel_worker_runtime.py
uv run pytest -q tests/integration/test_parallel_worker_bootstrap.py
```

## Interactive Claude PTY tests

```bash
cd ralph-workflow
uv run pytest -q tests/test_process_manager_pty.py
uv run pytest -q tests/test_claude_interactive_pty.py tests/test_claude_interactive_session_resume.py tests/test_claude_interactive_parser.py
uv run pytest -q tests/integration/test_claude_interactive_pty_e2e.py tests/integration/test_claude_interactive_interrupt_realtime.py
```

## Manual interactive Claude smoke test

**Not part of `make verify`** — consumes live agent tokens. Validates the interactive TUI interpreter surfaces expected semantic signals.

```bash
cd ralph-workflow
python -m ralph smoke-interactive-claude
```

What it does:
- writes a smoke prompt under `tmp/interactive-claude-smoke/`
- asks `claude/haiku` to create a JavaScript todo list
- prints a detailed report of what worked and what broke
- uses the headless Claude contract as a guide for expected semantics (session capture, tool activity, completion signal, parser events, tmp/ artifact creation)

## Recovery tests

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

All recovery tests run in under 10 seconds each with injectable fake clocks, fake sleep, and fake probes — no real network I/O.
