from __future__ import annotations

import json

import pytest

from ralph.pipeline.state import PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerState, WorkerStatus


def _wu(unit_id: str, description: str = "A task") -> WorkUnit:
    return WorkUnit(unit_id=unit_id, description=description)


def _ws(unit_id: str, status: WorkerStatus = WorkerStatus.PENDING) -> WorkerState:
    return WorkerState(unit_id=unit_id, status=status)


def test_round_trip_with_workers() -> None:
    work_units = (
        _wu("task-a", "First subtask"),
        _wu("task-b", "Second subtask"),
        _wu("task-c", "Third subtask"),
    )
    worker_states = {
        "task-a": _ws("task-a", WorkerStatus.SUCCEEDED),
        "task-b": _ws("task-b", WorkerStatus.FAILED),
        "task-c": _ws("task-c", WorkerStatus.RUNNING),
    }
    state = PipelineState(phase="planning", work_units=work_units, worker_states=worker_states)

    restored = PipelineState.model_validate_json(state.model_dump_json())

    assert restored.work_units == work_units
    assert restored.worker_states == worker_states
    assert restored.worker_states["task-a"].status == WorkerStatus.SUCCEEDED
    assert restored.worker_states["task-b"].status == WorkerStatus.FAILED
    assert restored.worker_states["task-c"].status == WorkerStatus.RUNNING


@pytest.mark.timeout_seconds(5)
def test_round_trip_empty_parallel_fields() -> None:
    state = PipelineState(phase="planning", work_units=(), worker_states={})

    restored = PipelineState.model_validate_json(state.model_dump_json())

    assert restored.work_units == ()
    assert restored.worker_states == {}


def test_old_checkpoint_without_parallel_fields() -> None:
    state = PipelineState(
        phase="planning",
        work_units=(_wu("u1"), _wu("u2")),
        worker_states={"u1": _ws("u1", WorkerStatus.SUCCEEDED)},
    )

    raw = state.model_dump(mode="json")
    del raw["work_units"]
    del raw["worker_states"]
    legacy_json = json.dumps(raw)

    restored = PipelineState.model_validate_json(legacy_json)

    assert restored.work_units == ()
    assert restored.worker_states == {}


def test_worker_status_enum_serializes() -> None:
    all_statuses = list(WorkerStatus)

    for status in all_statuses:
        ws = WorkerState(unit_id="test", status=status)
        restored = WorkerState.model_validate_json(ws.model_dump_json())
        assert restored.status == status, f"Round-trip failed for status={status!r}"


@pytest.mark.parametrize("status", list(WorkerStatus))
def test_worker_status_values_round_trip(status: WorkerStatus) -> None:
    state = PipelineState(
        phase="planning", worker_states={"w": WorkerState(unit_id="w", status=status)}
    )

    restored = PipelineState.model_validate_json(state.model_dump_json())

    assert restored.worker_states["w"].status == status
