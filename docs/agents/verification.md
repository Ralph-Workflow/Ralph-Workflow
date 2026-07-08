# Required Verification (before PR/completion)

## Canonical command

```bash
cd ralph-workflow
make verify
```

The Sphinx docs build (`make docs`) is wired in as a Make prerequisite of `make verify` with `-W --keep-going` so any Sphinx warning fails the gate; it runs before the Python verify step and stays outside the immutable 60-second combined test budget.

## Fabrication guardrails

Any edit to a public-facing markdown file (README, USERS.md, docs/, the Sphinx operator manual) is in scope for the [fabrication guard](fabrication-guard.md). USERS.md is the single canonical community directory; the previous near-duplicate community surfaces (`SHOWCASE.md`, `ECOSYSTEM.md`, `COMPARISONS.md`, `CREDIT_TEMPLATE.md`) were removed in the 2026-07-07 docs cleanup. The guard runs as a pre-commit hook at Level 1; re-run it explicitly with `./scripts/fabrication_guard.py --level 1 <file>` or `--level 2 <file>` for network existence checks. Bypassing the guard (for example with `--no-verify`) is itself fabrication.

## Total test budget — 60 seconds, ABSOLUTE and IMMUTABLE

The 60-second combined total test budget is **absolute and immutable**. It cannot be changed, overridden, or circumvented. `make verify` runs `make test`, which executes one maintained parallel pytest invocation over `tests/` with `-m "not subprocess_e2e"`.

`ralph/verify.py` enforces these import-time invariants (using `if`/`raise RuntimeError`, NOT `assert`, so they survive `python -O`):

- `_TOTAL_TEST_BUDGET_SECONDS > 0` (must be positive)
- `abs(_TOTAL_TEST_BUDGET_SECONDS - 60.0) < 1e-9` (epsilon check on the 60.0 constant)
- `_BUDGET_TRACKED_STEPS` indices are valid indices into `_VERIFY_STEPS`
- Every budget-tracked step has a positive timeout
- `_VERIFY_STEP_TIMEOUT_SECONDS > 0` and `>= 5.0` (non-trivial per-step cap)
- `_KNOWN_TEST_STEP_LABELS` and `_BUDGET_TRACKED_STEPS` are non-empty
- `'make test'` is present in `_KNOWN_TEST_STEP_LABELS`
- Every label in `_KNOWN_TEST_STEP_LABELS` is tracked; every tracked step has its label

All invariants are tested in `tests/test_verify_invariants.py` under both normal execution and `python -O`. A timeout failure is a test design defect — fix the production coupling, never adjust the budget. Per-suite caps (`PYTEST_SUITE_TIMEOUT_SECONDS`, `DEFAULT_SUITE_TIMEOUT_SECONDS`) are SECONDARY; raising them does NOT increase the combined budget. The cumulative tracker sums time across ALL budget-tracked steps via `time.monotonic()`. See [AGENTS.md §'═══ ABSOLUTE TEST BUDGET — 60s, IMMUTABLE ═══'](../../AGENTS.md) for the canonical non-circumvention table.

## Non-circumvention table

The following do **NOT** circumvent the 60-second combined budget or the lint/typecheck enforcement (detected by the corresponding audit module under `ralph/testing/audit_*.py`):

| Attempted circumvention | Why it fails |
|---|---|
| Splitting tests into N suites | Cumulative tracker sums time across ALL tracked steps — not per-suite |
| Moving slow tests to a different suite/target | All budget-tracked steps count toward the same combined budget |
| Renaming test targets or adding new test-related `_VERIFY_STEPS` without updating `_KNOWN_TEST_STEP_LABELS` | `_BUDGET_TRACKED_STEPS` stays frozen — new targets do not get budget tracking |
| Raising `PYTEST_SUITE_TIMEOUT_SECONDS` / `DEFAULT_SUITE_TIMEOUT_SECONDS` | Per-suite cap only; combined budget enforced separately by `_TOTAL_TEST_BUDGET_SECONDS` |
| Setting `RALPH_PYTEST_SUITE_TIMEOUT_SECONDS` / `RALPH_PYTEST_TEST_TIMEOUT_SECONDS` | Env vars only adjust per-suite/per-test caps, not the combined budget |
| Modifying `_BUDGET_TRACKED_STEPS` / raising `_TOTAL_TEST_BUDGET_SECONDS` | Blocked by import-time `if`/`raise RuntimeError` checks — immune to `python -O` |
| Emptying `_KNOWN_TEST_STEP_LABELS` / `_BUDGET_TRACKED_STEPS` | Blocked by import-time non-empty RuntimeError check — immune to `python -O` |
| Removing `'make test'` from `_KNOWN_TEST_STEP_LABELS` | Blocked by import-time containment RuntimeError check — immune to `python -O` |
| Adding `per-file-ignores` / `extend-per-file-ignores` / blanket `# noqa` | Detected by `ralph/testing/audit_lint_bypass.py` |
| Adding `ignore_missing_imports`, `disable_error_code`, blanket `# type: ignore` | Detected by `ralph/testing/audit_typecheck_bypass.py` |
| Using `time.sleep()`, real subprocess, real file I/O in non-`subprocess_e2e` tests | Detected by `ralph/testing/audit_test_policy.py` |
| Unbounded `subprocess.run` / `httpx.*` / `urlopen` / `socket.create_connection` (no `timeout=`) in `ralph/mcp/`, `ralph/git/`, `ralph/process/manager/` | Detected by `ralph/testing/audit_mcp_timeout.py` |
| Mutable collection literals (`list` / `dict` / `set` / `deque`) assigned to module-level names or `self.X` in `__init__` without `maxlen=` or a justified `# bounded-accumulator-ok:` marker | Detected by `ralph/testing/audit_resource_lifecycle.py` |

Every circumvention above is detected by `make verify`. Any bypass requires a documented justification and an entry in the audit allowlist — there is no other path. See [AGENTS.md §'Non-negotiables'](../../AGENTS.md) for the full policy text.

## Smoke-check subsections

Use these focused commands when a smoke check is required for the area you are touching. Each command lives outside the budget-tracked combined budget (per-suite caps only) so they do not inflate the 60-second gate.

```bash
# Policy loader smoke check (after changing policy defaults)
python -c "from pathlib import Path; from ralph.policy.loader import load_policy; load_policy(Path('ralph/policy/defaults'))"

# Parallel-mode regression tests (work-units / namespaced payloads)
uv run pytest -q tests/test_parallel_mode_docs_banned_phrases_across_all_docs.py tests/test_parallel_mode_docs_namespaced_payload_docs.py

# Parallel worker bootstrap tests
uv run pytest -q tests/test_parallel_worker_runtime.py tests/integration/test_parallel_worker_bootstrap.py

# Interactive Claude PTY tests
uv run pytest -q tests/test_process_manager_pty.py tests/test_claude_interactive_pty.py tests/test_claude_interactive_session_resume.py tests/test_claude_interactive_parser.py

# Recovery tests
uv run pytest -x tests/recovery/ tests/test_recovery_first_invariant.py tests/test_reducer.py tests/test_pipeline_runner.py
```

For full verification including docs and subprocess E2E:

```bash
cd ralph-workflow
make docs
make test-subprocess-e2e
```

Verification passes only when all checks complete with **no ERROR/WARNING diagnostics**. If any step fails, fix the issue immediately and rerun. `make verify` emits a high-visibility failure banner that cites `AGENTS.md`.

## Cross-links

- `ralph/verify.py` — budget tracker, `_VERIFY_STEPS`, invariant checks
- `ralph/testing/audit_*.py` — per-audit machinery (see each docstring for the per-audit invariant list)
- [AGENTS.md §'Non-negotiables'](../../AGENTS.md) — canonical policy and circumvention table
- [Testing Guide](testing-guide.md) — test design rules and required doubles
- [Documentation Rubric](../code-style/documentation-rubric.md) — for any docs/README/manual change

If the change touches README, docs, START_HERE, the manual, or any public-doc route, read [Documentation Rubric](../code-style/documentation-rubric.md) first and check the edited surface against it before calling the docs work done.