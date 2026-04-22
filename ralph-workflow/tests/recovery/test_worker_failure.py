"""Black-box test: worker failures do not terminate the pipeline."""

from __future__ import annotations

from ralph.config.enums import PHASE_DEVELOPMENT, PHASE_FAILED
from ralph.pipeline.events import WorkerFailedEvent, WorkersMergeConflictEvent
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerState, WorkerStatus

_MIN_ERROR_LEN = 10


def _make_work_unit(unit_id: str) -> WorkUnit:
    return WorkUnit(unit_id=unit_id, description="Test unit", allowed_directories=[])


def _make_state_with_workers(unit_ids: list[str]) -> PipelineState:
    work_units = tuple(_make_work_unit(uid) for uid in unit_ids)
    worker_states = {
        uid: WorkerState(unit_id=uid, status=WorkerStatus.RUNNING) for uid in unit_ids
    }
    return PipelineState(
        phase=PHASE_DEVELOPMENT,
        work_units=work_units,
        worker_states=worker_states,
    )


def test_single_worker_failure_sets_failed_status() -> None:
    """A single WorkerFailedEvent marks that worker FAILED but does not terminate pipeline."""
    state = _make_state_with_workers(["w1", "w2"])

    event = WorkerFailedEvent(unit_id="w1", exit_code=1, error="agent crashed")
    new_state, effects = reduce(state, event, None)

    # w1 is now FAILED; w2 still RUNNING
    assert new_state.worker_states["w1"].status == WorkerStatus.FAILED
    assert new_state.worker_states["w2"].status == WorkerStatus.RUNNING
    # Pipeline phase unchanged — worker failures don't terminate
    assert new_state.phase == PHASE_DEVELOPMENT
    assert effects == []


def test_merge_conflict_enters_phase_failed_with_descriptive_reason() -> None:
    """WorkersMergeConflictEvent routes to PHASE_FAILED with a descriptive reason."""
    state = _make_state_with_workers(["w1", "w2"])

    event = WorkersMergeConflictEvent(conflicting_unit_ids=["w1", "w2"])
    new_state, _ = reduce(state, event, None)

    assert new_state.phase == PHASE_FAILED
    assert new_state.last_error is not None
    assert "Merge conflict in workers" in new_state.last_error
    assert "w1" in new_state.last_error
    assert "w2" in new_state.last_error


def test_merge_conflict_reason_is_non_sentinel() -> None:
    """WorkersMergeConflictEvent reason must not be a forbidden sentinel."""
    state = _make_state_with_workers(["worker-alpha"])
    event = WorkersMergeConflictEvent(conflicting_unit_ids=["worker-alpha"])
    new_state, _ = reduce(state, event, None)

    last_err = new_state.last_error
    assert last_err is not None
    assert last_err not in ("Unknown failure", "unknown failure", "None", "null", "")
    assert len(last_err) > _MIN_ERROR_LEN


def test_worker_failure_preserves_work_units() -> None:
    """Worker failure must not destroy the work_units tuple on the state."""
    state = _make_state_with_workers(["w1"])
    event = WorkerFailedEvent(unit_id="w1", exit_code=2, error="timeout")
    new_state, _ = reduce(state, event, None)

    assert len(new_state.work_units) == 1
    assert new_state.work_units[0].unit_id == "w1"
