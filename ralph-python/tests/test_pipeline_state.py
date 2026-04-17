"""Unit tests for PipelineState field behavior and invariants."""

from __future__ import annotations

import json

from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import PipelineState
from ralph.pipeline.work_units import WorkUnit


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
