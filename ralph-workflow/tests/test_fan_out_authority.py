"""Unit tests for the fan-out authority (Authority 6)."""

from __future__ import annotations

import warnings

from ralph.pipeline._fan_out_authority import (
    FanOutDecision,
    fan_out_decision,
    handle_fan_out_started,
    handle_worker_completed,
    handle_worker_failed,
    handle_worker_started,
    handle_workers_resumed,
)
from ralph.pipeline.state import PipelineState
from ralph.pipeline.worker_state import WorkerState, WorkerStatus


def test_fan_out_decision_enum_has_four_values() -> None:
    assert len(FanOutDecision) == 4


def test_fan_out_decision_join_complete_when_no_workers() -> None:
    state = PipelineState(phase="plan")
    assert fan_out_decision(state) is FanOutDecision.JOIN_COMPLETE


def test_fan_out_decision_join_pending_when_workers_running() -> None:
    state = PipelineState(
        phase="plan",
        worker_states={
            "u1": WorkerState(unit_id="u1", status=WorkerStatus.RUNNING),
        },
    )
    assert fan_out_decision(state) is FanOutDecision.JOIN_PENDING


def test_fan_out_decision_partial_failure_when_any_failed() -> None:
    state = PipelineState(
        phase="plan",
        worker_states={
            "u1": WorkerState(unit_id="u1", status=WorkerStatus.SUCCEEDED),
            "u2": WorkerState(unit_id="u2", status=WorkerStatus.FAILED),
        },
    )
    assert fan_out_decision(state) is FanOutDecision.JOIN_PARTIAL_FAILURE


def test_fan_out_decision_complete_when_all_succeeded() -> None:
    state = PipelineState(
        phase="plan",
        worker_states={
            "u1": WorkerState(unit_id="u1", status=WorkerStatus.SUCCEEDED),
            "u2": WorkerState(unit_id="u2", status=WorkerStatus.SUCCEEDED),
        },
    )
    assert fan_out_decision(state) is FanOutDecision.JOIN_COMPLETE


def test_fan_out_decision_blocked_when_all_cancelled() -> None:
    state = PipelineState(
        phase="plan",
        worker_states={
            "u1": WorkerState(unit_id="u1", status=WorkerStatus.CANCELLED),
        },
    )
    assert fan_out_decision(state) is FanOutDecision.JOIN_BLOCKED


def test_reducer_worker_state_is_deprecated_shim() -> None:
    """The legacy module is a re-export with a DeprecationWarning.

    Uses :mod:`importlib.reload` so the module-level
    :func:`warnings.warn` runs again even if the module was
    already imported by another test.
    """
    import importlib

    from ralph.pipeline import _reducer_worker_state

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        importlib.reload(_reducer_worker_state)

    deprecation_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert any("_fan_out_authority" in str(w.message) for w in deprecation_warnings)


def test_reducer_worker_state_re_exports_handlers() -> None:
    """The legacy handlers are re-exported under their original names."""
    from ralph.pipeline import _reducer_worker_state

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        assert _reducer_worker_state.handle_fan_out_started is handle_fan_out_started
        assert _reducer_worker_state.handle_worker_completed is handle_worker_completed
        assert _reducer_worker_state.handle_worker_failed is handle_worker_failed
        assert _reducer_worker_state.handle_worker_started is handle_worker_started
        assert _reducer_worker_state.handle_workers_resumed is handle_workers_resumed
