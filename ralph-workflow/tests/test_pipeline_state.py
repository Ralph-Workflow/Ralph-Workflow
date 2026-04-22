"""Unit tests for PipelineState field behavior and invariants."""

from __future__ import annotations

import json

from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import FalloverRecord, PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerState, WorkerStatus

# Constants for magic values used in tests
_DEFAULT_RECOVERY_CYCLE_CAP = 200
_TEST_RECOVERY_CYCLE_COUNT = 3
_TEST_RECOVERY_CYCLE_CAP = 100


def _wu(unit_id: str = "u1", description: str = "A task") -> WorkUnit:
    return WorkUnit(unit_id=unit_id, description=description)


def test_work_units_default_empty() -> None:
    """PipelineState.work_units defaults to an empty tuple."""
    state = PipelineState()

    assert state.work_units == ()


def test_work_units_coerces_list_to_tuple_on_load() -> None:
    """field_validator coerces list → tuple during JSON deserialization."""
    wu = _wu()
    raw = {
        "work_units": [
            {
                "unit_id": wu.unit_id,
                "description": wu.description,
                "allowed_directories": [],
                "dependencies": [],
            }
        ]
    }

    state = PipelineState.model_validate(raw)

    assert isinstance(state.work_units, tuple)
    assert len(state.work_units) == 1
    assert state.work_units[0].unit_id == wu.unit_id


def test_work_units_none_coerces_to_empty_tuple() -> None:
    """field_validator coerces None → () for backward compat when key is absent."""
    state = PipelineState.model_validate({"work_units": None})

    assert state.work_units == ()


def test_old_checkpoint_json_without_work_units_loads_as_empty() -> None:
    """Old checkpoint JSON (without work_units key) loads with work_units == ()."""
    old_json = json.dumps(
        {
            "phase": "development",
            "iteration": 1,
        }
    )

    state = PipelineState.model_validate_json(old_json)

    assert state.work_units == ()


def test_work_units_round_trip_serialization() -> None:
    """work_units round-trips through model_dump_json / model_validate_json."""
    wu = _wu()
    state = PipelineState(work_units=(wu,))

    loaded = PipelineState.model_validate_json(state.model_dump_json())

    assert loaded.work_units == state.work_units


def test_work_units_preserved_across_copy() -> None:
    """copy_with preserves work_units when it is not in the update dict."""
    wu = _wu()
    state = PipelineState(work_units=(wu,))

    new_state = state.copy_with(phase="development")

    assert new_state.work_units == (wu,)
    assert new_state.phase == "development"


def test_work_units_immutable_once_set() -> None:
    """Once work_units is non-empty, copy_with silently ignores attempts to change it."""
    wu1 = _wu("u1", "First task")
    wu2 = _wu("u2", "Second task")
    state = PipelineState(work_units=(wu1,))

    new_state = state.copy_with(work_units=(wu2,))

    assert new_state.work_units == (wu1,)


def test_work_units_immutable_via_reducer() -> None:
    """Reducer guard: work_units cannot be changed once set, even across reducer events."""
    wu = _wu()
    state = PipelineState(work_units=(wu,))

    new_state, _ = reduce(state, PipelineEvent.CHECKPOINT_SAVED)

    assert new_state.work_units == (wu,)


def test_work_units_empty_allows_set_via_copy_with() -> None:
    """When work_units is empty, copy_with can set it to a non-empty tuple."""
    wu = _wu()
    state = PipelineState()

    new_state = state.copy_with(work_units=(wu,))

    assert new_state.work_units == (wu,)


def _ws(unit_id: str = "u1", status: WorkerStatus = WorkerStatus.PENDING) -> WorkerState:
    return WorkerState(unit_id=unit_id, status=status)


def test_worker_states_default_empty() -> None:
    """PipelineState.worker_states defaults to an empty dict."""
    state = PipelineState()

    assert state.worker_states == {}


def test_worker_states_round_trip_with_three_entries() -> None:
    """worker_states with 3 entries round-trips through model_dump_json."""
    states = {
        "u1": _ws("u1", WorkerStatus.PENDING),
        "u2": _ws("u2", WorkerStatus.RUNNING),
        "u3": _ws("u3", WorkerStatus.SUCCEEDED),
    }
    state = PipelineState(worker_states=states)

    loaded = PipelineState.model_validate_json(state.model_dump_json())

    assert loaded.worker_states == states


def test_old_checkpoint_without_worker_states_loads_as_empty() -> None:
    """Old checkpoint JSON (without worker_states key) loads with worker_states == {}."""
    old_json = json.dumps({"phase": "development", "iteration": 2})

    state = PipelineState.model_validate_json(old_json)

    assert state.worker_states == {}


def test_pipeline_state_no_longer_exposes_legacy_continuation_state() -> None:
    """ContinuationState is legacy bookkeeping and should not remain on PipelineState."""
    assert "continuation" not in PipelineState.model_fields


def test_old_checkpoint_with_legacy_continuation_state_still_loads() -> None:
    """Old checkpoints carrying legacy continuation data should still deserialize."""
    old_json = json.dumps(
        {
            "phase": "development",
            "iteration": 1,
            "continuation": {
                "active": True,
                "previous_status": "partial",
                "context_write_pending": False,
            },
        }
    )

    state = PipelineState.model_validate_json(old_json)

    assert state.phase == "development"
    assert state.iteration == 1


def test_worker_states_none_coerces_to_empty_dict() -> None:
    """field_validator coerces None → {} for backward compat."""
    state = PipelineState.model_validate({"worker_states": None})

    assert state.worker_states == {}


def test_worker_states_preserved_across_copy_with() -> None:
    """copy_with preserves worker_states when not in the update dict."""
    ws = _ws("u1", WorkerStatus.RUNNING)
    state = PipelineState(worker_states={"u1": ws})

    new_state = state.copy_with(phase="development")

    assert new_state.worker_states == {"u1": ws}


def test_recovery_fields_default_values() -> None:
    """Recovery fields have correct defaults so legacy checkpoints load cleanly."""
    old_json = json.dumps({"phase": "planning", "iteration": 0})

    state = PipelineState.model_validate_json(old_json)

    assert state.recovery_cycle_count == 0
    assert state.fallover_history == ()
    assert state.last_failure_category is None
    assert state.last_connectivity_state == "unknown"
    assert state.recovery_cycle_cap == _DEFAULT_RECOVERY_CYCLE_CAP


def test_recovery_fields_round_trip() -> None:
    """Recovery fields round-trip through model_dump_json / model_validate_json."""
    state = PipelineState(
        recovery_cycle_count=_TEST_RECOVERY_CYCLE_COUNT,
        fallover_history=(
            FalloverRecord(
                phase="development",
                from_agent="claude",
                to_agent="opencode",
                timestamp_iso="2026-04-21T12:00:00Z",
            ),
        ),
        last_failure_category="agent",
        last_connectivity_state="online",
        recovery_cycle_cap=_TEST_RECOVERY_CYCLE_CAP,
    )

    loaded = PipelineState.model_validate_json(state.model_dump_json())

    assert loaded.recovery_cycle_count == _TEST_RECOVERY_CYCLE_COUNT
    assert len(loaded.fallover_history) == 1
    assert loaded.fallover_history[0].from_agent == "claude"
    assert loaded.last_failure_category == "agent"
    assert loaded.last_connectivity_state == "online"
    assert loaded.recovery_cycle_cap == _TEST_RECOVERY_CYCLE_CAP
