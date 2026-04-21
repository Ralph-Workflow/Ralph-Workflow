# Recovery-First Refactor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make Ralph treat ordinary pipeline failures as recovery events instead of terminal exits so the run keeps progressing or re-prompting instead of dying.

**Architecture:** Centralize failure recovery in the pipeline reducer and runner instead of letting scattered terminal paths emit `ExitFailureEffect`. Analysis rejection should route through loopback, while exhausted retries, commit failures, and explicit failed events should re-enter the phase with recovery context. The runner should no longer stop on `PHASE_FAILED`; instead it should convert failure state into a recovery prompt cycle.

**Tech Stack:** Python 3.12+, Pydantic, pytest, Ruff, mypy

---

### Task 1: Lock the new recovery contract with failing tests

**Files:**
- Modify: `tests/test_reducer.py`
- Modify: `tests/test_phases_development.py`
- Modify: `tests/test_phases_review.py`
- Modify: `tests/test_pipeline_runner.py`

**Step 1: Write failing reducer tests**
Add tests asserting these behaviors:
- recoverable chain exhaustion does not transition to `PHASE_FAILED`
- non-recoverable `PhaseFailureEvent` does not terminate; it becomes recovery state
- `COMMIT_FAILURE` does not terminate
- `PipelineEvent.FAILED` does not terminate

**Step 2: Write failing analysis handler tests**
Change existing development/review analysis tests so `failure` and `escalate` decisions loop back instead of emitting non-recoverable `PhaseFailureEvent`.

**Step 3: Write failing runner test**
Add a runner-level test asserting failed state re-enters prompt preparation instead of producing exit behavior.

**Step 4: Run focused tests and verify red**
Run:
- `uv run pytest tests/test_reducer.py -k "phase_failure or commit_failure"`
- `uv run pytest tests/test_phases_development.py tests/test_phases_review.py -k "failure_decision or escalate_decision"`
- `uv run pytest tests/test_pipeline_runner.py -k "failed_state"`
Expected: failures showing current terminal behavior.

### Task 2: Centralize failure-to-recovery conversion in the reducer

**Files:**
- Modify: `ralph/pipeline/reducer.py`
- Modify: `ralph/pipeline/state.py` (only if extra state shaping is needed)
- Test: `tests/test_reducer.py`

**Step 1: Introduce a helper for failed-state recovery**
Add a reducer helper that:
- records `last_error`
- increments `recovery_epoch`
- preserves enough state to retry the same phase
- resets per-phase retry counters when the chain is exhausted
- emits no `ExitFailureEffect`

**Step 2: Route terminal reducer paths through the helper**
Update:
- `_handle_phase_failure()` for `recoverable=False`
- `_handle_agent_failure()` exhaustion/no-chain paths
- `_handle_agent_retry()` no-chain path
- `_handle_commit_failure()`
- `_handle_failed()`
- `_advance_to_terminal(... PHASE_FAILED ...)`
- worker merge conflict handling if it still routes to `PHASE_FAILED`

**Step 3: Re-run reducer tests and make them green**
Run: `uv run pytest tests/test_reducer.py -q`
Expected: all reducer recovery tests pass.

### Task 3: Make analysis rejection a loopback, not a kill switch

**Files:**
- Modify: `ralph/phases/development.py`
- Modify: `ralph/phases/review.py`
- Test: `tests/test_phases_development.py`
- Test: `tests/test_phases_review.py`

**Step 1: Convert explicit analysis failure decisions**
Change development/review analysis handlers so `FAILURE` and `ESCALATE` produce `PipelineEvent.ANALYSIS_LOOPBACK` instead of a terminal phase failure.

**Step 2: Preserve reason in logs/handoffs**
Keep the decision visible in logging and `last_error`-style context where possible, but do not terminate.

**Step 3: Run focused analysis tests**
Run: `uv run pytest tests/test_phases_development.py tests/test_phases_review.py -q`
Expected: loopback behavior passes.

### Task 4: Stop the runner from treating failed state as process exit

**Files:**
- Modify: `ralph/pipeline/runner.py`
- Modify: `ralph/pipeline/orchestrator.py` (consistency with runner)
- Test: `tests/test_pipeline_runner.py`

**Step 1: Remove the main-loop hard stop on `PHASE_FAILED`**
Change the runner loop so it stops only on success or interrupt, not on failed state.

**Step 2: Convert failed state into recovery preparation**
Update terminal-state/effect resolution so `PHASE_FAILED` yields a recovery step back into execution rather than `ExitFailureEffect`.

**Step 3: Keep recovery black-box visible**
Ensure recovery increments `recovery_epoch`, preserves `last_error`, and re-materializes a prompt or recovery context so behavior is observable in tests.

**Step 4: Run focused runner tests**
Run: `uv run pytest tests/test_pipeline_runner.py -k "failed_state or recover"`
Expected: runner no longer exits on ordinary failure.

### Task 5: Verify end-to-end recovery-first behavior

**Files:**
- Modify if needed: any touched files above
- Test: `tests/test_recovery_first_invariant.py`
- Test: `tests/test_pipeline_runner.py`

**Step 1: Add or extend black-box invariants**
Ensure there are end-to-end style tests proving:
- missing analysis artifact recovers
- transient session loss retries with prompt context
- chain exhaustion recovers instead of terminating
- commit failure retries instead of terminating

**Step 2: Run touched suites**
Run:
- `uv run pytest tests/test_reducer.py tests/test_recovery_first_invariant.py tests/test_phases_development.py tests/test_phases_review.py tests/test_pipeline_runner.py -q`
Expected: all pass.

**Step 3: Run full verification**
Run: `make verify`
Expected: Ruff, mypy, and full test suite pass.
