"""Regression harness for AgentBudgetRegistry / FailureBudget failure retention.

wt-024 memory-perf AC-01: ``BudgetState.failures`` is an unbounded tuple of
``ClassifiedFailure`` objects appended on every budget-counted failure (at
``agent_budget_registry.py:41`` and ``failure_budget.py:27``) and NEVER
read back for any decision. The two ``reset()`` methods
(``agent_budget_registry.py:47`` and ``failure_budget.py:31``) are dead
code with zero callers, and the failures tuple retains heavyweight
objects (original_exception + traceback frames) across a long unattended
run.

This test mirrors the canonical pattern at
``test_pipeline_memory_regression.py`` (tracemalloc over N iterations,
gc.collect after, retained/peak byte-delta caps, ``@pytest.mark.integration
@pytest.mark.timeout_seconds(10)``).

The leak manifests at the FINAL registry / final budget: that single
live object retains the full ``failures`` tuple (every ``ClassifiedFailure``
plus its ``original_exception`` traceback frame pinning the blob).
After step 3 the failures field is dropped from ``BudgetState``
entirely, so the FINAL object stays small.

Fails today: the final ``AgentBudgetRegistry`` / ``FailureBudget`` retains
``_ITERATION_COUNT`` ClassifiedFailures; each carries a traceback frame
holding the test's 8 KiB ``blob``. Worst-case retained bytes is
``_ITERATION_COUNT * _BLOB_SIZE_BYTES`` (well past the 256 KiB cap).
"""

from __future__ import annotations

import gc
import tracemalloc

import pytest

from ralph.recovery.agent_budget_registry import AgentBudgetRegistry
from ralph.recovery.budget_state import BudgetState
from ralph.recovery.classified_failure import ClassifiedFailure
from ralph.recovery.failure_budget import FailureBudget
from ralph.recovery.failure_category import FailureCategory

pytestmark = pytest.mark.subprocess_e2e

_ITERATION_COUNT = 64
_BLOB_SIZE_BYTES = 8 * 1024
_RETAINED_DELTA_LIMIT = 256 * 1024


def _make_classified_failure(index: int) -> ClassifiedFailure:
    """Build a ClassifiedFailure carrying a UNIQUE ``bytes`` blob in
    ``original_exception.args``.

    Each call allocates a fresh ``blob`` (the index suffix ensures the
    bytes object is distinct per iteration), then attaches it to a
    synthetic ``RuntimeError`` whose args tuple pins the blob. The
    ClassifiedFailure returned keeps the exception alive, and the
    exception keeps the blob alive for as long as the
    ClassifiedFailure is referenced.

    Without the fix (step 3) every ``debit`` appends the
    ClassifiedFailure to ``BudgetState.failures``, so the FINAL
    registry holds all 64 distinct blobs → ~512 KiB retained. With
    the fix the failures field is dropped and retained memory stays
    flat.
    """
    blob = (b"x" * _BLOB_SIZE_BYTES) + str(index).encode()
    try:
        raise RuntimeError("synthetic-budget-failure")
    except RuntimeError as exc:
        exc.args = (blob, *exc.args[1:])
        return ClassifiedFailure(
            category=FailureCategory.AGENT,
            reason="agent-error",
            attributed_agent="claude",
            attributed_phase="development",
            counts_against_budget=True,
            original_exception=exc,
            raw_message="synthetic-budget-failure",
        )


@pytest.mark.integration
@pytest.mark.timeout_seconds(10)
def test_agent_budget_registry_failures_do_not_grow() -> None:
    """``AgentBudgetRegistry.debit`` MUST NOT retain every ``ClassifiedFailure``.

    The leak surfaces in the FINAL registry — that single live object
    holds the cumulative ``BudgetState.failures`` tuple. Without the
    fix (step 3), ``failures=(*current.failures, failure)`` keeps every
    ``ClassifiedFailure`` (and its traceback frame pinning the blob)
    alive for the lifetime of the registry, so a 64-debit loop
    inflates retained bytes well past the 256 KiB cap. With the fix,
    the failures field is dropped from ``BudgetState`` entirely.
    """
    gc.collect()
    tracemalloc.start()
    baseline_current, _ = tracemalloc.get_traced_memory()
    tracemalloc.reset_peak()

    registry: AgentBudgetRegistry = AgentBudgetRegistry().set_budget(
        "development", "claude", max_retries=_ITERATION_COUNT + 1
    )
    for i in range(_ITERATION_COUNT):
        failure = _make_classified_failure(i)
        registry = registry.debit("development", "claude", failure)
    # Hold the final registry alive until after the retained-memory
    # measurement — the leak class is exactly "the live final object
    # retains the full tuple", so the measurement must include it.
    final_registry = registry
    assert final_registry is not None

    gc.collect()
    final_current, peak_current = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    retained_delta_bytes = final_current - baseline_current
    peak_delta_bytes = peak_current - baseline_current

    assert retained_delta_bytes <= _RETAINED_DELTA_LIMIT, (
        f"AgentBudgetRegistry failure retention regression: retained delta "
        f"{retained_delta_bytes} bytes > {_RETAINED_DELTA_LIMIT}-byte budget "
        f"after {_ITERATION_COUNT} debits of {_BLOB_SIZE_BYTES}-byte payloads"
    )
    assert peak_delta_bytes <= _RETAINED_DELTA_LIMIT, (
        f"AgentBudgetRegistry failure retention peak regression: peak delta "
        f"{peak_delta_bytes} bytes > {_RETAINED_DELTA_LIMIT}-byte budget "
        f"after {_ITERATION_COUNT} debits of {_BLOB_SIZE_BYTES}-byte payloads"
    )


@pytest.mark.integration
@pytest.mark.timeout_seconds(10)
def test_failure_budget_failures_do_not_grow() -> None:
    """``FailureBudget.debit`` MUST NOT retain every ``ClassifiedFailure``.

    The ``FailureBudget`` wrapper composes onto ``BudgetState``; the
    same leak pattern (every debit appends to ``failures``) applies.
    Without the fix, the final ``FailureBudget`` retains all 64
    failures and their traceback frames. With the fix, the failures
    field is dropped from ``BudgetState`` entirely.
    """
    gc.collect()
    tracemalloc.start()
    baseline_current, _ = tracemalloc.get_traced_memory()
    tracemalloc.reset_peak()

    state = BudgetState(max_retries=_ITERATION_COUNT + 1)
    budget: FailureBudget = FailureBudget(state=state)
    for i in range(_ITERATION_COUNT):
        failure = _make_classified_failure(i)
        budget = budget.debit(failure)
    final_budget = budget
    assert final_budget is not None

    gc.collect()
    final_current, peak_current = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    retained_delta_bytes = final_current - baseline_current
    peak_delta_bytes = peak_current - baseline_current

    assert retained_delta_bytes <= _RETAINED_DELTA_LIMIT, (
        f"FailureBudget failure retention regression: retained delta "
        f"{retained_delta_bytes} bytes > {_RETAINED_DELTA_LIMIT}-byte budget "
        f"after {_ITERATION_COUNT} debits of {_BLOB_SIZE_BYTES}-byte payloads"
    )
    assert peak_delta_bytes <= _RETAINED_DELTA_LIMIT, (
        f"FailureBudget failure retention peak regression: peak delta "
        f"{peak_delta_bytes} bytes > {_RETAINED_DELTA_LIMIT}-byte budget "
        f"after {_ITERATION_COUNT} debits of {_BLOB_SIZE_BYTES}-byte payloads"
    )


def test_budget_state_no_failures_field() -> None:
    """After step 3, ``BudgetState`` MUST NOT expose a ``failures`` field.

    The field was a leak vector (every debit appended a
    ``ClassifiedFailure`` that was never read for any decision). With
    it dropped, callers that still try to access ``.failures`` fail
    loudly at attribute access instead of silently retaining dead data.

    This regression pins the API surface: a future commit cannot
    silently re-introduce a failures accumulator on ``BudgetState``
    without tripping this test.
    """
    state = BudgetState(max_retries=4)
    assert not hasattr(state, "failures"), (
        "BudgetState must not expose a `failures` field — it was an "
        "unbounded accumulator never read for any decision and is the "
        "exact leak class the wt-024 memory-perf contract prevents"
    )
