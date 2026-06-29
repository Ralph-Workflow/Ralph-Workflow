# The verification model

> **Mental model page.** This is explanation, not a how-to. For the practical
> verification contract, see
> [`docs/agents/verification.md`](../../../docs/agents/verification.md) and
> [Advanced pipeline configuration](advanced-pipeline-configuration.md).

Ralph Workflow treats verification as a **non-bypassable gate** that runs
after every code-affecting phase and at the terminal of every run. The
verification model is the same regardless of which agent produced the
change, which phase emitted the artifact, or which policy bundle drove the
run.

## What `make verify` proves

`make verify` is the canonical verification command. It runs four kinds
of checks:

1. **Lint** — `ruff check ralph/ tests/`
2. **Typecheck** — `mypy ralph/`
3. **Test** — `pytest` under the **immutable 60-second combined budget**
4. **Audit** — the `ralph.testing.audit_*` scripts that detect
   circumvention of the policy and quality gates

A clean `make verify` proves:

- The Python code is lint-clean
- The Python code is type-clean
- The unit and integration tests pass within the budget
- No audit invariant has been silently weakened

A green `make verify` is a **necessary** precondition for declaring work
done, but it is not sufficient: the runtime also verifies the run
artifact against the phase's declared contract (see
[Artifact lifecycle](artifact-lifecycle.md)).

## The 60-second combined test budget — immutable

The test budget is **60 seconds, combined, ABSOLUTE and IMMUTABLE**. This
is enforced by `ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS = 60.0` and
tracked cumulatively across all `_BUDGET_TRACKED_STEPS` via
`time.monotonic()`.

The budget cannot be circumvented by:

- Splitting tests into more suites (cumulative tracker sums time across
  ALL budget-tracked steps)
- Moving slow tests to a different suite, target, or Makefile recipe
- Renaming test targets without updating `_KNOWN_TEST_STEP_LABELS`
- Raising `DEFAULT_SUITE_TIMEOUT_SECONDS` or
  `PYTEST_SUITE_TIMEOUT_SECONDS`
- Setting `RALPH_PYTEST_SUITE_TIMEOUT_SECONDS` or
  `RALPH_PYTEST_TEST_TIMEOUT_SECONDS`
- Raising `_TOTAL_TEST_BUDGET_SECONDS` (blocked by import-time
  `RuntimeError` checks — immune to `python -O`)
- Emptying `_KNOWN_TEST_STEP_LABELS` to hide test steps
- Emptying `_BUDGET_TRACKED_STEPS` to disable enforcement
- Removing `'make test'` from `_KNOWN_TEST_STEP_LABELS`

Each `RuntimeError` is enforced via `if`/raise, not `assert`, so it
survives `python -O`. A timeout failure is a test design defect — fix
the test, not the budget.

## The audit invariant set

Ralph Workflow ships with 14 audit scripts in `ralph/testing/audit_*.py`.
Each one detects a class of circumvention:

| Audit                                | Detects                                                          |
| ------------------------------------ | ---------------------------------------------------------------- |
| `audit_lint_bypass.py`               | Lint rule weakening via per-file-ignores or blanket noqa        |
| `audit_typecheck_bypass.py`          | Mypy rule weakening via `ignore_missing_imports` etc.            |
| `audit_test_policy.py`               | Real I/O or `time.sleep` in non-subprocess_e2e tests              |
| `audit_mcp_timeout.py`               | Unbounded blocking calls in `ralph/mcp/`                         |
| `audit_resource_lifecycle.py`        | Unbounded accumulators (deque without maxlen, unbounded lists)   |
| `audit_artifact_submission_canonical_path.py` | Artifact writes not via canonical path                  |
| `audit_parallelization_dormant.py`   | Dormant parallel mode invariant violations                       |
| ... and 7 more                       | See `ralph/testing/audit_*.py` for the full set                  |

Each audit has a documented allowlist. Adding an entry to an allowlist is
the **only** way to weaken a check, and the entry must cite a real
justification.

## Per-step timeouts

The runtime enforces per-step timeouts (`_VERIFY_STEP_TIMEOUT_SECONDS`)
in addition to the combined budget. The per-step timeout is a secondary
cap — it cannot extend the combined budget, only fail fast on a stuck
step. The default per-step timeout is `>= 5.0` seconds.

## Non-circumvention rules

The verification model has explicit non-circumvention rules. The full set
lives in `AGENTS.md`; the highlights:

- Lint, typecheck, and test checks cannot be weakened to get green
- The MCP timeout contract cannot be bypassed without an inline marker
  and a documented reason
- Resource accumulators must carry a size cap or a justified marker
- The test budget cannot be circumvented by splitting or renaming
- Artifact submissions must go through the canonical path
- The fabrication guard cannot be weakened or skipped

These are **policies**, not suggestions. Each one is enforced by an audit
or a runtime check, and each bypass requires an entry in a documented
allowlist.

## Why the verification model is strict

The verification model exists because the project has shipped bugs,
stale claims, and fabricated stats. The strict checks are the response:
they make the failure mode **loud** rather than silent. Every rule that
seems excessive is the scar tissue of a real failure that happened.

The 60-second budget, in particular, exists because slow tests create
feedback loops that erode developer trust in the test suite. A test that
takes 30 seconds to run is a test that gets skipped in the inner loop.
The budget forces the test design to be fast by construction, which
forces production code to be testable by construction.

## What to read next

- [`docs/agents/verification.md`](../../../docs/agents/verification.md) —
  the contributor-side verification contract
- [`AGENTS.md`](../../../AGENTS.md) — the full non-circumvention policy
- [Advanced pipeline configuration](advanced-pipeline-configuration.md) —
  per-phase verification overrides
- [Watchdogs and timeouts](watchdogs-and-timeouts.md) — the runtime
  watchdog contract

## Related pages

- [Ralph-loop](ralph-loop.md) — the simple core the verification model
  guards (Ralph Workflow is built around this loop)
- [Artifact lifecycle](artifact-lifecycle.md) — what verification checks
  in artifacts